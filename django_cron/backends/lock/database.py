from django.conf import settings
from django.db import transaction
from django.utils import timezone

from django_cron.backends.lock.base import DjangoCronJobLock
from django_cron.models import CronJobLock


class DatabaseLock(DjangoCronJobLock):
    """
    Locking cron jobs with database row-level locking via SELECT FOR UPDATE.
    Prevents TOCTOU races by holding an exclusive row lock for the
    duration of the transaction. Supports stale lock recovery via
    locked_at timestamp comparison against the configured timeout.
    """

    def __init__(self, cron_class, *args, **kwargs):
        super().__init__(cron_class, *args, **kwargs)
        self._cron_class = cron_class

    def get_lock_timeout(self):
        # Check cron class first, then settings, then default.
        timeout = getattr(self._cron_class, 'DJANGO_CRON_LOCK_TIME', None)
        if timeout is None:
            timeout = getattr(settings, 'DJANGO_CRON_LOCK_TIME', None)
        if timeout is None:
            timeout = self.DEFAULT_LOCK_TIMEOUT
        return timeout

    @transaction.atomic
    def lock(self):
        # SELECT FOR UPDATE places an exclusive row lock.
        # Concurrent lock() calls BLOCK here until this transaction commits.
        lock = CronJobLock.objects.select_for_update().filter(
            job_name=self.job_name
        ).first()

        if lock is None:
            # First time this job is being locked — create the row.
            CronJobLock.objects.create(
                job_name=self.job_name,
                locked=True,
                locked_at=timezone.now(),
                owner=self.owner_id,
            )
            return True

        if not lock.locked:
            # Lock row exists but is not held — acquire it.
            lock.locked = True
            lock.locked_at = timezone.now()
            lock.owner = self.owner_id
            lock.save()
            return True

        # Lock is held by someone — check if it's stale.
        if lock.locked_at is not None:
            elapsed = (timezone.now() - lock.locked_at).total_seconds()
            if elapsed > self.get_lock_timeout():
                # Stale lock: previous holder likely crashed.
                lock.locked = True
                lock.locked_at = timezone.now()
                lock.owner = self.owner_id
                lock.save()
                return True

        # Lock is held and not stale — cannot proceed.
        return False

    @transaction.atomic
    def release(self):
        lock = CronJobLock.objects.select_for_update().filter(
            job_name=self.job_name
        ).first()

        if lock is None or not lock.locked:
            return

        # OWNERSHIP CHECK: only release if WE own the lock.
        if lock.owner != self.owner_id:
            return

        lock.locked = False
        lock.locked_at = None
        lock.owner = ''
        lock.save()
