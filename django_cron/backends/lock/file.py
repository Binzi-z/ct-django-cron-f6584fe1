import json
import os
import time

from django.conf import settings
from django.core.files import locks

from django_cron.backends.lock.base import DjangoCronJobLock


class FileLock(DjangoCronJobLock):
    """
    Lock backend using OS-level file locking (flock/LockFileEx).
    Writes owner metadata into the lock file for diagnostics
    and PID-based stale detection as a fallback.
    """

    __lock_fd = None

    def lock(self):
        lock_name = self.get_lock_name()
        try:
            self.__lock_fd = open(lock_name, 'w+b')
            locks.lock(self.__lock_fd, locks.LOCK_EX | locks.LOCK_NB)
        except (IOError, OSError):
            # Could not acquire flock — check if the existing lock is stale
            if self._is_stale_lock_file(lock_name):
                try:
                    os.unlink(lock_name)
                except OSError:
                    pass  # Another process may have already cleaned it
                try:
                    self.__lock_fd = open(lock_name, 'w+b')
                    locks.lock(self.__lock_fd, locks.LOCK_EX | locks.LOCK_NB)
                except (IOError, OSError):
                    return False
            else:
                return False

        # Write owner metadata for diagnostics and stale detection
        metadata = json.dumps({
            'owner': self.owner_id,
            'pid': os.getpid(),
            'timestamp': time.time(),
        }).encode('utf-8')
        self.__lock_fd.write(metadata)
        self.__lock_fd.flush()
        return True

    def release(self):
        if self.__lock_fd is None:
            return
        try:
            locks.unlock(self.__lock_fd)
        except (IOError, OSError):
            pass
        try:
            self.__lock_fd.close()
        except (IOError, OSError):
            pass
        self.__lock_fd = None

    def _is_stale_lock_file(self, lock_path):
        """
        Check if a lock file is stale by reading its PID metadata.
        Returns True if the PID recorded in the file is no longer running.
        """
        try:
            with open(lock_path, 'rb') as f:
                data = f.read()
            if not data:
                return False
            metadata = json.loads(data.decode('utf-8'))
            pid = metadata.get('pid')
            if pid is None:
                return False
            try:
                os.kill(pid, 0)  # Signal 0: check if process exists
                return False     # PID alive — lock is NOT stale
            except OSError:
                return True      # PID gone — lock IS stale
        except (json.JSONDecodeError, IOError, OSError, KeyError, ValueError):
            return False

    def get_lock_name(self):
        default_path = '/tmp'
        path = getattr(settings, 'DJANGO_CRON_LOCKFILE_PATH', default_path)
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
        filename = self.job_name + '.lock'
        return os.path.join(path, filename)
