import asyncio
from datetime import datetime, timedelta
from _config import Config
from files import Files
from share import Share
from log import Log


class Storage:
    CLEANUP_HOUR_MINUTE = '0000'

    def __init__(self, camera_hash):
        self._hash = camera_hash
        self._cam_path = f'{Config.storage_path}/{Config.cameras[self._hash]["path"]}'
        self._start_time = None

    async def run(self) -> None:
        """ Start fragments saving
        """
        try:
            await self._start_saving()
        except Exception as e:
            Log.write(f'Storage: ERROR: can\'t start saving "{self._hash}" ({repr(e)})')

    async def _start_saving(self, caller: str = '') -> None:
        """ We'll use system (linux) commands for this job
        """
        await self._mkdir(datetime.now().strftime('%Y%m%d/%H/%M'))

        cfg = Config.cameras[self._hash]
        cmd = Config.storage_command.replace('{url}', cfg['url']).replace('{cam_path}', f'{self._cam_path}')

        # Run given command in background
        # Important: don't use create_subprocess_SHELL for this command!
        #
        self.main_process = await asyncio.create_subprocess_exec(*cmd.split())
        self._start_time = datetime.now()

        Log.write(f'Storage:{caller} start main process {self.main_process.pid} for "{self._hash}"')

    async def _mkdir(self, folder: str) -> None:
        """ Create storage folder if not exists
        """
        cmd = f'mkdir -p {self._cam_path}/{folder}'
        p = await asyncio.create_subprocess_shell(cmd)
        await p.wait()

    async def watchdog(self) -> None:
        """ Infinite loop for checking camera(s) availability
        """
        while True:
            await asyncio.sleep(Config.min_segment_duration)
            try:
                await self._watchdog()
            except Exception as e:
                Log.print(f'Storage: watchdog ERROR: can\'t check the storage "{self._hash}" ({repr(e)})')

    async def _watchdog(self) -> None:
        """ Extremely important piece.
            Checks if saving is frozen and creates next working directory.
            Cameras can turn off on power loss, or external commands can freeze.
        """
        if not self._start_time:
            return

        files = Files(self._hash)

        prev_dir = f'{self._cam_path}/{(datetime.now() - timedelta(minutes=1)).strftime(files.DT_FORMAT)}'
        working_dir = f'{self._cam_path}/{datetime.now().strftime(files.DT_FORMAT)}'
        cmd = f'ls -l {prev_dir}/* {working_dir}/* | awk ' + "'{print $5,$9}'"
        p = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        stdout, _stderr = await p.communicate()
        res = stdout.decode().strip().splitlines()[-10:]

        await self._mkdir((datetime.now() + timedelta(minutes=1)).strftime('%Y%m%d/%H/%M'))
        await self._cleanup()

        self._live_motion_detector(res[:-1])

        if res or not self._start_time or (datetime.now() - self._start_time).total_seconds() < 60.0:
            return  # normal case

        Log.print(f'Storage: FREEZE detected for "{self._hash}"')

        # Freeze detected, restart
        try:
            self._start_time = None
            self.main_process.kill()
        except Exception as e:
            Log.print(f'Storage: watchdog: kill {self.main_process.pid} ERROR "{self._hash}" ({repr(e)})')

        await self._start_saving('watchdog: ')

        # Remove previous folders if empty
        prev_min = datetime.now() - timedelta(minutes=1)
        await self._remove_folder_if_empty(prev_min.strftime('%Y%m%d/%H/%M'))
        await self._remove_folder_if_empty(prev_min.strftime('%Y%m%d/%H'))
        await self._remove_folder_if_empty(prev_min.strftime('%Y%m%d'))

    def _live_motion_detector(self, file_list) -> None:
        cfg = Config.cameras[self._hash]
        if 'sensitivity' not in cfg or not cfg['sensitivity'] or cfg['sensitivity'] <= 1 or len(file_list) < 2:
            return
        files = Files(self._hash)
        total_size = 0
        cnt = 0
        for file in file_list[:-1]:
            f = file.split(' ')
            if int(f[0]) <= files.MIN_FILE_SIZE:
                continue
            total_size += int(f[0])
            cnt += 1
        if not cnt:
            return

        last_file = file_list[-1].split(' ')
        if float(last_file[0]) > total_size / cnt * cfg['sensitivity']:
            date_time = files.get_datetime_by_path(last_file[1])
            if self._hash in Share.cam_motions and Share.cam_motions[self._hash] == date_time:
                return
            Share.cam_motions[self._hash] = date_time
            Log.write(f'Storage: motion detected: {date_time} {self._hash}')

    async def _remove_folder_if_empty(self, folder) -> None:
        path = f'{Config.storage_path}/{Config.cameras[self._hash]["path"]}/{folder}'
        cmd = f'rmdir {path}'
        p = await asyncio.create_subprocess_shell(cmd)
        res = await p.wait()  # returns 0 if success, else 1
        if res == 0:
            Log.print(f'Storage: watchdog: folder removed: "{self._hash}" {folder}')

    async def _cleanup(self) -> None:
        """ Cleanup (5 times per day - 00:00 ... 04:00)
        """
        if datetime.now().strftime('%M') != '00' or datetime.now().strftime('%H') > '04':
            return

        cmd = f'ls -d {self._cam_path}/*'
        p = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        stdout, _stderr = await p.communicate()
        if not stdout:
            return

        oldest_dir_name = (datetime.now() - timedelta(days=Config.storage_period_days + 1)).strftime('%Y%m%d')

        for row in stdout.decode().strip().split('\n'):
            wd = row.split('/')[-1]
            if wd >= oldest_dir_name or not wd:
                break
            cmd = f'rm -rf {self._cam_path}/{wd}'
            p = await asyncio.create_subprocess_shell(cmd)
            await p.wait()

            Log.write(f'Storage: cleanup: remove {self._hash} {wd}')
