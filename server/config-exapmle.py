class Config:
    # Camera(s) settings.
    #    * The keys of this dictionary will be called "camera hash".
    #    * "folder" is used for the storage.
    #    * "url" must contain at least <protocol>://<host>
    #    * "name" is just visible camera name, can include emoji
    #    * "sensitivity" is used as threasold value for the Motion Detector.
    #    *     Must be more than 1. Set to 0 to disable.
    #
    cameras = {
        'some-URL-compatible-string/including-UTF-characters': {
            'folder': 'some folder in the storage_path',
            'url': 'rtsp://[<login>:<password>@]<host>[:554][/<params>]',
            'name': 'Any camera name',
            'sensitivity': 1.5,
        },
    }

    # Temporary group settings
    groups = {
        'grp1': {
            'cams': ['camera-hash'],
            'name': '❄️ Group 1',
        }
    }

    # Web page title
    title = 'Cams'

    webServerHost = '0.0.0.0'
    webServerPort = 8000

    master_cam_hash = 'master cam hash'
    # Use hashlib.sha256(b"my_secret_password").hexdigest() to encode "my_secret_password"
    # default is "1234"
    master_password_hash = '03ac674216f3e15c761ee1a5e255f067953623c8b388b4459e13f978d7c846f4'
    # default is "1111"
    cam_password_hash = '0ffe1abd1a08215353c233d6e009613e95eec4253832a761af28ff37ac5a150c'
    encryption_key = 'Secret Encryption Key'

    # Path to VALID ssl certificates.
    # Use any service likes https://letsencrypt.org/
    # or create self-signed certificate, then import root one to your browser.
    ssl_certificate = '/<path>/_localhost.crt'
    ssl_private_key = '/<path>/_localhost.key'

    # Check "storage_command" output, secs (int).
    # Used as the storage watchdog interval and motion detector interval
    min_segment_duration = 4

    # Run this script with root permissions or set up log rotation yourself
    log_file = '/var/log/cams-pwa.log'

    # Attention!
    # All files and subdirectories older than "storage_period_days" in this folder will be deleted!
    storage_path = '/<path>'

    # {url} = cameras.hash.url
    # {cam_path} = storage_path/cameras.hash.folder
    # Remove "-c:a aac" option if the cameras don't support audio channels
    storage_command = (
        'ffmpeg -i {url} -c:v copy -c:a aac -v fatal -f segment -reset_timestamps 1 '
        '-strftime 1 {cam_path}/%Y-%m-%d/%H/%M/%S.mp4')

    storage_period_days = 14
    storage_enabled = False

    web_enabled = True
    debug = True
