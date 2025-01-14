import ssl
import re
import json
import time
import mimetypes
from os import path as os_path
from http.server import BaseHTTPRequestHandler, HTTPServer
from http.cookies import SimpleCookie
from socketserver import ThreadingMixIn, BaseServer
from urllib.parse import urlparse, parse_qs
from datetime import datetime
from typing import Tuple
import const
from _config import Config
from auth import Auth
from videos import Videos
from images import Images
from share import Share
from log import Log


class Server:
    @staticmethod
    def run() -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(Config.ssl_certificate, Config.ssl_private_key)

        web_server = ThreadingServer((Config.web_server_host, Config.web_server_port), Handler)
        web_server.socket = context.wrap_socket(web_server.socket, server_side=True)

        Log.write(f'Serving HTTP on https://{Config.web_server_host}:{Config.web_server_port}/ ...')

        try:
            web_server.serve_forever()
        except KeyboardInterrupt:
            pass

        web_server.server_close()
        Log.write('Server stopped.')


class ThreadingServer(ThreadingMixIn, HTTPServer):
    pass


class Handler(BaseHTTPRequestHandler):
    def __init__(self, request: bytes, client_address: Tuple[str, int], server: BaseServer):
        super().__init__(request, client_address, server)
        self.hash = None
        self._query = None
        self._videos = None
        self._images = None

    def do_GET(self) -> None:
        """ Router
            Possible GET params: ?<page|video|image|bell>=<val>[...]&hash=<hash>[...]
        """
        self._init()
        self._query = parse_qs(urlparse(self.path).query)  # GET params (dict)

        if not self._query and self.path != '/':
            return self._send_static(self.path)

        if not self._query and self.path == '/':
            return self._send_page()  # index page

        if 'bell' in self._query:
            return self._send_bell()

        if 'hash' not in self._query:
            return self._send_error()

        self.hash = self._query['hash'][0]
        if self.hash not in Config.cameras and (not hasattr(Config, 'groups') or self.hash not in Config.groups):
            return self._send_error()  # Invalid hash
        if not self.auth.info() or (self.auth.info() != Config.master_cam_hash and self.auth.info() != self.hash):
            return self._send_error(403)  # Invalid auth

        if 'page' in self._query:
            return self._send_page()  # authorized page

        if 'video' in self._query:
            self._videos = Videos(self.hash)
            return self._send_segment(*self._videos.get(self._query))

        if 'image' in self._query:
            self._images = Images(self.hash)
            return self._send_image(*self._images.get(self._query))

        self._send_error()  # No valid route found

    def do_POST(self) -> None:
        """ Auth form handler
        """
        self._init()

        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        auth_info = self.auth.login(post_data)
        if not auth_info:
            Log.write('Web: ERROR: invalid auth')
            return self._send_error(403)

        self.send_response(200)
        self.send_header('Set-Cookie', self._create_auth_cookie())
        self.end_headers()
        Log.write(f'Web: logged in: {auth_info}')

    def version_string(self) -> str:
        """Overrides parent method."""
        return Config().web_server_name

    def _init(self) -> None:
        self.cookie = SimpleCookie()
        raw_cookies = self.headers.get('Cookie')
        if raw_cookies:
            self.cookie.load(raw_cookies)

        self.auth = Auth(self.cookie['auth'].value if 'auth' in self.cookie else None)

    def _get_client_type(self) -> str:
        host = self.headers.get('Host').split(':')[0]
        if host == '127.0.0.1' or host == 'localhost' or host.startswith('192.168.'):
            return 'local'
        return 'web'

    def _send_static(self, static_file: str) -> None:
        if not re.search(r'^/([a-z]+/)*[a-z\d._]+$', static_file):
            return self._send_error()
        try:
            with open(f'{os_path.dirname(os_path.realpath(__file__))}/../client{static_file}', 'rb') as file:
                mime_type, _enc = mimetypes.MimeTypes().guess_type(static_file)
                self.send_response(200)
                self.send_header('Content-Type', mime_type)
                self.end_headers()
                if static_file == '/cams.webmanifest':
                    title = Config.web_title if self._get_client_type() == 'web' else Config.title
                    self.wfile.write(file.read().replace('{title}'.encode('UTF-8'), title.encode('UTF-8')))
                    return
                self.wfile.write(file.read())
        except Exception as e:
            Log.write(f"Web: ERROR: can't open static file {static_file} ({repr(e)})")
            self._send_error()

    def _send_page(self) -> None:
        page = self._query['page'][0] if self._query else 'index'
        if page not in ['index', 'cam', 'group', 'events']:
            return self._send_error()

        template = f'/{page}.html'
        if not self.auth.info():
            template = '/auth.html'
        try:
            with open(f'{os_path.dirname(os_path.realpath(__file__))}/../client/layout.html', 'rb') as file:
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Set-Cookie', self._create_auth_cookie())
                self.end_headers()
                self.wfile.write(self._replace_template(template, file.read()))
        except Exception as e:
            Log.write(f'Web: ERROR: page "{page}" not found ({repr(e)})')
            self._send_error()

    def _create_auth_cookie(self) -> str:
        return (
            f'auth={self.auth.encrypt(self.auth.info())}; '
            'Path=/; Max-Age=3456000; Secure; HttpOnly; SameSite=Lax')

    def _replace_template(self, template: str, content: bytes) -> bytes:
        content = content.replace('{content}'.encode('UTF-8'), self._get_content(template))
        title = Config.title

        cams_list = {}
        bell_hidden = 'hidden'
        for cam_hash, cam in Config.cameras.items():
            if self.auth.info() == Config.master_cam_hash or self.auth.info() == cam_hash:
                cams_list[cam_hash] = {
                    'name': cam['name'],
                    'codecs': cam['codecs'],
                    'sensitivity': cam['sensitivity'],
                    'events': cam['events'],
                    'bell': self._get_bell_time(cam_hash)}
                if cam['sensitivity'] or cam['events']:
                    bell_hidden = ''

        if template == '/index.html':
            groups_list = {}
            if hasattr(Config, 'groups'):
                for k, v in Config.groups.items():
                    if self.auth.info() == Config.master_cam_hash:
                        groups_list[k] = {'name': v['name']}

            content = content.replace(
                '{cams}'.encode('UTF-8'), json.dumps(cams_list).encode('UTF-8')
            ).replace(
                '{groups}'.encode('UTF-8'), json.dumps(groups_list).encode('UTF-8')
            )
        elif template == '/cam.html':
            if self.hash not in cams_list:
                return b''
            videos = Videos(self.hash)
            cam = Config.cameras[self.hash]
            title = cam['name']
            events_hidden = 'hidden' if not cams_list[self.hash]['events'] else ''
            content = content.replace(
                '{days}'.encode('UTF-8'), json.dumps(videos.get_days()).encode('UTF-8')
            ).replace(
                '{cam_info}'.encode('UTF-8'), json.dumps(cams_list[self.hash]).encode('UTF-8')
            ).replace(
                '{events_hidden}'.encode('UTF-8'), events_hidden.encode('UTF-8')
            )
        elif template == '/group.html':
            cams = {}
            for cam_hash in Config.groups[self.hash]['cams']:
                if cam_hash in cams_list:
                    cams[cam_hash] = cams_list[cam_hash]
            if hasattr(Config, 'groups'):
                title = Config.groups[self.hash]['name']
            content = content.replace(
                '{cams}'.encode('UTF-8'), json.dumps(cams).encode('UTF-8')
            )
        elif template == '/events.html':
            if self.hash not in cams_list:
                return b''
            images = Images(self.hash)
            cam = Config.cameras[self.hash]
            title = cam['name']
            content = content.replace(
                '{cam_info}'.encode('UTF-8'), json.dumps(cams_list[self.hash]).encode('UTF-8')
            ).replace(
                '{chart_data}'.encode('UTF-8'), json.dumps(images.get_chart_data()).encode('UTF-8')
            )
        content = content.replace('{bell_hidden}'.encode('UTF-8'), bell_hidden.encode('UTF-8'))
        return content.replace('{title}'.encode('UTF-8'), title.encode('UTF-8'))

    @staticmethod
    def _get_bell_time(cam_hash) -> str:
        if cam_hash not in Share.cam_motions:
            return ''
        last_bell_datetime = datetime.strptime(Share.cam_motions[cam_hash], const.DT_WEB_FORMAT)
        if (datetime.now() - last_bell_datetime).total_seconds() > 43200:  # not older than 12 hours
            return ''
        return last_bell_datetime.strftime('%H:%M')

    @staticmethod
    def _get_content(template: str) -> bytes:
        try:
            with open(f'{os_path.dirname(os_path.realpath(__file__))}/../client{template}', 'rb') as file:
                return file.read()
        except Exception as e:
            Log.write(f'Web: ERROR: template "{template}" not found ({repr(e)})')

    def _send_segment(self, file_path: str, file_size: int) -> None:
        query_date_time = self._query['dt'][0] if 'dt' in self._query else ''
        file_date_time = self._videos.get_datetime_by_path(file_path)
        try:
            self.send_response(200)
            if file_path and file_size and query_date_time != file_date_time:
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Content-Length', str(file_size))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Datetime', file_date_time)
                self.send_header('X-Range', self._videos.get_range_by_path(file_path))
                self.end_headers()
                with open(file_path, 'rb') as video_file:
                    self.wfile.write(video_file.read())
            else:
                self.end_headers()
        except Exception as e:
            Log.write(f'Web: request aborted ({repr(e)})')

    def _send_image(self, file_path: str, file_size: int, position: str, rng: int) -> None:
        try:
            mime_type, _enc = mimetypes.MimeTypes().guess_type(file_path)
            self.send_response(200)
            self.send_header('Content-Type', mime_type)
            self.send_header('Content-Length', str(file_size))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Range', str(rng))
            self.send_header('X-Position', position)
            self.end_headers()
            with open(file_path, 'rb') as video_file:
                self.wfile.write(video_file.read())
        except Exception as e:
            Log.write(f'Web: request aborted ({repr(e)})')

    def _send_bell(self) -> None:
        if not self.auth.info():
            return self._send_error(403)

        try:
            last_date_time = self._query['dt'][0]
        except Exception as e:
            last_date_time = ''
            Log.write(f'Web bell: send query ERROR {repr(e)}')

        prev_motions = Share.cam_motions.copy()
        cnt = 0
        time.sleep(1)
        while True:
            res = {}
            for cam_hash, date_time in Share.cam_motions.items():
                if self.auth.info() != Config.master_cam_hash and self.auth.info() != cam_hash:
                    continue
                if last_date_time >= date_time:
                    continue
                if cam_hash in prev_motions and prev_motions[cam_hash] >= date_time:
                    continue
                res[cam_hash] = {'dt': date_time, 'name': Config.cameras[cam_hash]["name"]}

            cnt += 1
            if not res and cnt < 60:
                time.sleep(1)
                continue

            try:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(res).encode('UTF-8'))
            except Exception as e:
                Log.write(f'Web bell: send ERROR {repr(e)}')

            return

    def _send_error(self, code: int = 404) -> None:
        self.send_response(code)
        self.end_headers()
