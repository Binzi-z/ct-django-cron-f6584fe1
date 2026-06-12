import uuid

from django.conf import settings
from django.core.cache import caches
from django.utils import timezone

from django_cron.backends.lock.base import DjangoCronJobLock


class CacheLock(DjangoCronJobLock):
    """
    One of simplest lock backends, uses django cache to
    prevent parallel runs of commands.
    """

    DEFAULT_LOCK_TIME = 24 * 60 * 60  # 24 hours

    def __init__(self, cron_class, *args, **kwargs):
        super().__init__(cron_class, *args, **kwargs)

        self.cache = self.get_cache_by_name()
        self.lock_name = self.get_lock_name()
        self.timeout = self.get_cache_timeout(cron_class)
        # Unique per acquisition so release() only ever clears our own lock.
        self.token = None

    def lock(self):
        """
        This method sets a cache variable to mark current job as "already running".

        ``cache.add`` is atomic: it only stores the value if the key is not
        already present and reports whether it did. This prevents two processes
        from both believing they acquired the lock (which a get-then-set would
        allow). The cache timeout means a process that dies while holding the
        lock will not keep it forever.
        """
        self.token = uuid.uuid4().hex
        payload = {'token': self.token, 'time': timezone.now()}
        return bool(self.cache.add(self.lock_name, payload, self.timeout))

    def release(self):
        """
        Release the lock only if we are still the owner.

        Without the token check a process finishing late could delete a lock
        that another process has since acquired and is still running under.
        """
        payload = self.cache.get(self.lock_name)
        if isinstance(payload, dict) and payload.get('token') == self.token:
            self.cache.delete(self.lock_name)

    def lock_failed_message(self):
        started = self.get_running_lock_date()
        msgs = [
            "%s: lock has been found. Other cron started at %s"
            % (self.job_name, started),
            "Current timeout for job %s is %s seconds (cache key name is '%s')."
            % (self.job_name, self.timeout, self.lock_name),
        ]
        return msgs

    def get_cache_by_name(self):
        """
        Gets a specified cache (or the `default` cache if CRON_CACHE is not set)
        """
        default_cache = "default"
        cache_name = getattr(settings, "DJANGO_CRON_CACHE", default_cache)

        # Allow the possible InvalidCacheBackendError to happen here
        # instead of allowing unexpected parallel runs of cron jobs
        return caches[cache_name]

    def get_lock_name(self):
        return self.job_name

    def get_cache_timeout(self, cron_class):
        try:
            timeout = getattr(
                cron_class, 'DJANGO_CRON_LOCK_TIME', settings.DJANGO_CRON_LOCK_TIME
            )
        except:
            timeout = self.DEFAULT_LOCK_TIME
        return timeout

    def get_running_lock_date(self):
        payload = self.cache.get(self.lock_name)
        date = payload.get('time') if isinstance(payload, dict) else payload
        if date and not timezone.is_aware(date):
            tz = timezone.get_current_timezone()
            date = timezone.make_aware(date, tz)
        return date
