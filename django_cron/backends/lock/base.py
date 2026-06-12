import os
import socket
import uuid


class DjangoCronJobLock(object):
    """
    Base lock class with ownership tracking and stale detection.

    Subclasses MUST implement:
      - lock()     -> bool (True = acquired, False = failed)
      - release()  -> None

    Subclasses MAY implement:
      - is_stale()          -> bool (default: False)
      - get_lock_timeout()  -> int seconds (default: 86400)
    """

    class LockFailedException(Exception):
        pass

    DEFAULT_LOCK_TIMEOUT = 24 * 60 * 60  # 24 hours

    def __init__(self, cron_class, silent, *args, **kwargs):
        """
        This method inits the class.
        Base class processes
            * self.job_name
            * self.job_code
            * self.parallel
            * self.silent
            * self.lock_acquired
            * self.owner_id
        for you. The rest is backend-specific.
        """
        self.job_name = '.'.join([cron_class.__module__, cron_class.__name__])
        self.job_code = cron_class.code
        self.parallel = getattr(cron_class, 'ALLOW_PARALLEL_RUNS', False)
        self.silent = silent
        self.lock_acquired = False
        # Unique owner identity: hostname:pid:uuid
        # - hostname distinguishes machines
        # - pid distinguishes processes on same machine
        # - uuid distinguishes restarts (handles PID reuse)
        self.owner_id = "{}:{}:{}".format(
            socket.gethostname(), os.getpid(), uuid.uuid4().hex[:12]
        )

    def lock(self):
        """
        This method called to acquire lock. Typically. it will
        be called from __enter__ method.
        Return True is success,
        False if fail.
        Here you can optionally call self.notice_lock_failed().
        """
        raise NotImplementedError(
            'You have to implement lock(self) method for your class'
        )

    def release(self):
        """
        This method called to release lock.
        Tipically called from __exit__ method.
        No need to return anything currently.
        """
        raise NotImplementedError(
            'You have to implement release(self) method for your class'
        )

    def is_stale(self):
        """Return True if a held lock is stale. Override per backend."""
        return False

    def get_lock_timeout(self):
        """Return lock timeout in seconds."""
        return self.DEFAULT_LOCK_TIMEOUT

    def lock_failed_message(self):
        return "%s: lock found. Will try later." % self.job_name

    def __enter__(self):
        if not self.parallel:
            self.lock_acquired = self.lock()
            if not self.lock_acquired:
                raise self.LockFailedException(self.lock_failed_message())
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self.parallel and self.lock_acquired:
            try:
                self.release()
            finally:
                self.lock_acquired = False
