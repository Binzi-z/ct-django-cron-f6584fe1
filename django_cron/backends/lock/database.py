import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from django_cron.backends.lock.base import DjangoCronJobLock
from django_cron.models import CronJobLock


class DatabaseLock(DjangoCronJobLock):
    """
    Locking cron jobs with database. Its good when you have not parallel run and want to make sure 2 jobs won't be
    fired at the same time - which may happened when job execution is longer that job interval.

    The lock is acquired atomically (via ``select_for_update``), released only
    by its owner (matched on a per-acquisition ``token``) and self-heals: a lock
    left behind by a process that died is reclaimed once it is older than the
    configured lock time.
    """

    DEFAULT_LOCK_TIME = 24 * 60 * 60  # 24 hours

    def __init__(self, cron_class, *args, **kwargs):
        super().__init__(cron_class, *args, **kwargs)
        # Unique per acquisition so release() only ever clears our own lock.
        self.token = None
        self.lock_time = self.get_lock_time(cron_class)

    def get_lock_time(self, cron_class):
        if hasattr(cron_class, 'DJANGO_CRON_LOCK_TIME'):
            return cron_class.DJANGO_CRON_LOCK_TIME
        return getattr(settings, 'DJANGO_CRON_LOCK_TIME', self.DEFAULT_LOCK_TIME)

    @transaction.atomic
    def lock(self):
        self.token = uuid.uuid4().hex
        now = timezone.now()
        CronJobLock.objects.get_or_create(job_name=self.job_name)
        # select_for_update serialises concurrent acquirers on databases that
        # support row locking (e.g. Postgres, MySQL), so only one of two
        # machines sharing the database can win the lock at a time.
        lock = CronJobLock.objects.select_for_update().get(job_name=self.job_name)
        if lock.locked and not self._is_expired(lock, now):
            return False
        lock.locked = True
        lock.locked_at = now
        lock.token = self.token
        lock.save()
        return True

    @transaction.atomic
    def release(self):
        # Owner-aware and crash-safe: only the row we still own (matching token)
        # is updated, and it is a no-op if the lock is gone or has been taken
        # over by someone else - so we never clear another process's lock.
        CronJobLock.objects.filter(
            job_name=self.job_name, token=self.token
        ).update(locked=False, locked_at=None, token=None)

    def _is_expired(self, lock, now):
        """
        A held lock is treated as stale (and may be reclaimed) once it is older
        than the configured lock time, which self-heals locks left behind by a
        process that died without releasing.
        """
        if lock.locked_at is None:
            return True
        return (now - lock.locked_at) > timedelta(seconds=self.lock_time)
