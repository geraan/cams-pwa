import re
import subprocess
from datetime import datetime, timedelta
from typing import Tuple, List, Dict, Any
from _config import Config
from log import Log


class Files:
    MAX_RANGE = 2000
    DT_ROOT_FORMAT = '%Y-%m-%d'
    DT_FORMAT = '%Y-%m-%d/%H/%M'
    DT_WEB_FORMAT = '%Y%m%d%H%M%S'
    DEPTH = 3
    MIN_FILE_SIZE = 1000
    MD_AVERAGE_LEN = 10

    def __init__(self, cam_hash: str):
        self._hash = cam_hash
        self._cam_path = f'{Config.storage_path}/{Config.cameras[cam_hash]["folder"]}'
        self._range = self.MAX_RANGE
        self._root_folder = []
        self._date_time = ''

    def get_days(self):
        return round((datetime.now() - self._get_start_date()).total_seconds() / 86400)

    def get_live(self) -> Tuple[str, int]:
        self._range = self.MAX_RANGE + 1

        path, size = self._get_live_file()  # checks now and last minute folder
        if size:
            return path, size

        fallback = (datetime.now() - timedelta(minutes=1)).strftime(self.DT_FORMAT).split('/')
        return self._find_nearest_file('/'.join(fallback[0:-1]), fallback[-1], -1)

    def get_by_range(self, rng: int) -> Tuple[str, int]:
        rng = min(max(rng, 0), self.MAX_RANGE)

        start_date = self._get_start_date()
        time_range = datetime.now() - start_date
        delta_minutes = int(time_range.total_seconds() * rng / self.MAX_RANGE / 60)
        wd = (start_date + timedelta(minutes=delta_minutes)).strftime(self.DT_FORMAT)

        parts = wd.split('/')
        return self._find_nearest_file('/'.join(parts[0:-1]), parts[-1], -1)

    def get_next(self, raw_step: int, date_time: str, sensitivity: int) -> Tuple[str, int]:
        if not date_time:
            return self.get_live()

        self._date_time = date_time
        steps = [1, 60, 600, 3600]
        step = steps[abs(raw_step) - 1] if 1 <= abs(raw_step) <= len(steps) else 1
        step = step * -1 if raw_step < 0 else step

        if sensitivity >= 0:
            return self._get_next_motion(sensitivity, step)

        file_path = self._get_path_by_datetime(date_time)
        parts = file_path.split('/')
        wd = '/'.join(parts[0:-1])

        files = self._get_files(wd)
        if files and abs(step) == 1:
            arr = files if step > 0 else reversed(files)
            for file in arr:
                file_name = file.split()[1]
                if (step > 0 and file_name <= parts[-1]) or (step < 0 and file_name >= parts[-1]):
                    continue

                f = file.split()
                path = f'{self._cam_path}/{wd}/{f[1]}'
                if int(f[0]) > self.MIN_FILE_SIZE:
                    return path, int(f[0])

        sign = 1 if step > 0 else -1
        step = max(60, abs(step)) * sign
        folder = (
            datetime.strptime(wd, self.DT_FORMAT) + timedelta(seconds=abs(step)) * sign
        ).strftime(self.DT_FORMAT)

        if step > 0 and folder >= datetime.now().strftime(self.DT_FORMAT):
            return self.get_live()

        parts = folder.split('/')
        return self._find_nearest_file('/'.join(parts[0:-1]), parts[-1], sign)

    def get_datetime_by_path(self, path: str) -> str:
        return re.sub(r'(-|/|.mp4)', '', path[len(self._cam_path) + 1:])

    def get_range_by_path(self, path: str) -> str:
        if self._range > self.MAX_RANGE:
            return self._range
        start_date = self._get_start_date()
        delta_seconds = (
            datetime.strptime(self.get_datetime_by_path(path), self.DT_WEB_FORMAT) - start_date
        ).total_seconds()
        total_seconds = (datetime.now() - start_date).total_seconds()
        return str(round(self.MAX_RANGE * delta_seconds / total_seconds))

    def _get_start_date(self) -> datetime:
        return datetime.strptime(self._get_folders()[0], self.DT_ROOT_FORMAT)

    def _find_nearest_file(self, parent: str, folder: str, step: int) -> Tuple[str, int]:
        """ If folder is set, shift left (to parent folder); else shift right (to child folder) """
        parts = parent.split('/') if parent else []

        if (folder and len(parts) == self.DEPTH - 1) or (not folder and len(parts) == self.DEPTH):
            path = f'{parent}/{folder}'.rstrip('/')
            position = -1 if step < 0 else 0
            file, size = self._get_file(path, position)
            if size:
                return file, size

        folders = self._get_folders(parent)
        if not folders and len(parts) > 0:
            return self._find_nearest_file('/'.join(parts[0:-1]), parts[-1], step)  # shift left

        if folder:
            if step < 0:  # find the largest element of folders less than folder
                rest = [i for i in folders if i < folder]
            else:  # find the smallest element of folders greater than folder
                rest = [i for i in folders if i > folder]
            if rest:
                parts.append(max(rest) if step < 0 else min(rest))
                return self._find_nearest_file('/'.join(parts), '', step)  # shift right

            if len(parts) > 0:
                return self._find_nearest_file('/'.join(parts[0:-1]), parts[-1], step)  # shift left
            elif step < 0:
                return self._find_nearest_file('', '', 1)  # move to the beginning
            else:
                return self.get_live()  # move to the end

        if not folder and folders and len(parts) < self.DEPTH:
            parts.append(folders[-1]) if step < 0 else parts.append(folders[0])
            return self._find_nearest_file('/'.join(parts), '', step)  # shift right

        Log.print(f'find_nearest_file: not found: {parent}[/{folder}], step={step}')

        return '', 0

    def _get_next_motion(self, sensitivity: int, step: int) -> Tuple[str, int]:
        sign = 1 if step > 0 else -1
        if step >= 60 or step <= -60:
            folder = (
                datetime.strptime(self._date_time, self.DT_WEB_FORMAT) + timedelta(seconds=abs(step)) * sign
            ).strftime(self.DT_FORMAT)
        else:
            path = self._get_path_by_datetime(self._date_time)
            folder = '/'.join(path.split('/')[0:-1])

        last_files = {}
        prev_folder = (datetime.strptime(folder, self.DT_FORMAT) - timedelta(minutes=1) * sign).strftime(self.DT_FORMAT)
        files = self._get_files(prev_folder)
        if files:
            for file in files:
                f = file.split(' ')
                last_files[f'{prev_folder}/{f[1]}'] = int(f[0])

        return self._motion_detector(folder, last_files, 100 - max(0, min(90, sensitivity)), sign)

    def _motion_detector(self, folder: str, last_files: Dict[str, int], sensitivity: int, sign: int) -> Tuple[str, int]:
        requested_path = self._get_path_by_datetime(self._date_time)
        files = self._get_files(folder)
        if not files:
            if sign > 0 and folder >= self._get_folders()[-1]:
                return self.get_live()
            if sign < 0 and folder <= self._get_folders()[0]:
                return '', 0

            file = self._find_nearest_file(folder, '', sign)
            if file:
                next_folder = '/'.join(file[0][len(self._cam_path) + 1:].split('/')[0:-1])
                if (sign > 0 and next_folder <= folder) or (sign < 0 and next_folder >= folder):
                    return '', 0
                return self._motion_detector(next_folder, last_files, sensitivity, sign)

        sens = 1 + sensitivity / 100
        if sign < 0:
            files.reverse()
        for file in files:
            f = file.split(' ')
            if float(f[0]) < self.MIN_FILE_SIZE:  # exclude broken files
                continue
            average_size = sum(last_files.values()) / len(last_files) if last_files else 0

            last_files[f'{folder}/{f[1]}'] = int(f[0])
            if len(last_files) > self.MD_AVERAGE_LEN:
                first_key = next(iter(last_files))
                del last_files[first_key]

            path = f'{folder}/{f[1]}'

            if (sign > 0 and requested_path >= path) or (sign < 0 and requested_path <= path):
                continue  # don't detect the files before last motion & last motion itself

            if average_size and float(f[0]) > average_size * sens:
                return f'{self._cam_path}/{folder}/{f[1]}', int(f[0])

        if folder >= datetime.now().strftime(self.DT_FORMAT):
            return self.get_live()

        next_folder = (datetime.strptime(folder, self.DT_FORMAT) + timedelta(minutes=1) * sign).strftime(self.DT_FORMAT)
        return self._motion_detector(next_folder, last_files, sensitivity, sign)

    def _get_folders(self, folder: str = '') -> List[str]:
        if not folder and self._root_folder:
            return self._root_folder
        cmd = f'ls {self._cam_path}/{folder}'
        res = self._exec(cmd).splitlines()
        if not folder:
            self._root_folder = res
        return res

    def _get_files(self, folder: str) -> List[str]:
        wd = f"{self._cam_path}/{folder}"
        cmd = f'ls -l {wd} | awk ' + "'{print $5,$9}'"
        res = self._exec(cmd)
        if not res and folder < datetime.now().strftime(self.DT_FORMAT):
            self._exec(f'rmdir {self._cam_path}/{folder}')  # delete empty folder
        return res.splitlines()

    def _get_file(self, folder: str, position: int = 0) -> Tuple[str, int]:
        files = self._get_files(folder)
        if not files or len(files) <= position or len(files) < abs(position):
            return '', 0
        file = files[position].split()  # [size, file]
        path = f'{self._cam_path}/{folder}/{file[1]}'
        size = int(file[0])
        if size > self.MIN_FILE_SIZE:
            return path, size
        return '', 0

    def _get_live_file(self):
        folder = datetime.now().strftime(self.DT_FORMAT)  # Regular case
        files = self._get_files(folder)
        position = -2
        if len(files) > 1:
            file = files[position].split()  # [size, file]
            size = int(file[0])
            if size < self.MIN_FILE_SIZE:
                return '', 0

            path = f'{self._cam_path}/{folder}/{file[1]}'
            return path, size

        elif files:
            position = -1

        folder = (datetime.now() - timedelta(minutes=1)).strftime(self.DT_FORMAT)  # Possible case
        return self._get_file(folder, position)

    @staticmethod
    def _get_path_by_datetime(dt: str) -> str:
        if not re.match(r'^\d{14}$', dt):
            return ''
        return f'{dt[0:4]}-{dt[4:6]}-{dt[6:8]}/{dt[8:10]}/{dt[10:12]}/{dt[12:14]}.mp4'

    @staticmethod
    def _exec(cmd: str, default: Any = '') -> Any:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, universal_newlines=True, shell=True)
        stdout, _stderr = p.communicate()
        return stdout.strip() or default
