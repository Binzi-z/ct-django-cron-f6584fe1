from django.conf import settings
from django.core.cache import caches

from django_cron.backends.lock.base import DjangoCronJobLock


class CacheLock(DjangoCronJobLock):
    """
    One of simplest lock backends, uses django cache to
    prevent parallel runs of commands.

    Uses cache.add() for atomic lock acquisition — this is guaranteed
    to be atomic across all built-in Django cache backends
    (Memcached 'add', Redis 'SET NX', file-based cache with file lock).
    """

    DEFAULT_LOCK_TIME = 24 * 60 * 60  # 24 hours

    def __init__(self, cron_class, *args, **kwargs):
        super().__init__(cron_class, *args, **kwargs)

        self.cache = self.get_cache_by_name()
        self.lock_name = self.get_lock_name()
        self.timeout = self.get_cache_timeout(cron_class)

    def lock(self):
        """
        Atomically attempt to set the lock key.
        cache.add() returns True ONLY if the key did not exist.
        This eliminates the TOCTOU race present in get()+set().
        The stored value is the owner_id for ownership verification.
        """
        if self.cache.add(self.lock_name, self.owner_id, self.timeout):
            return True
        return False

    def release(self):
        """
        Release the lock only if we are the owner.
        This prevents one machine from clearing another's lock.
        """
        stored_owner = self.cache.get(self.lock_name)
        if stored_owner == self.owner_id:
            self.cache.delete(self.lock_name)

    def lock_failed_message(self):
        stored = self.cache.get(self.lock_name)
        msgs = [
            "%s: lock has been found. Owner: %s"
            % (self.job_name, stored),
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
        # Check cron class first, then settings, then default.
        # Use explicit None checks to avoid eager evaluation of
        # settings.DJANGO_CRON_LOCK_TIME (which may not exist).
        timeout = getattr(cron_class, 'DJANGO_CRON_LOCK_TIME', None)
        if timeout is None:
            timeout = getattr(settings, 'DJANGO_CRON_LOCK_TIME', None)
        if timeout is None:
            timeout = self.DEFAULT_LOCK_TIME
        return timeout
