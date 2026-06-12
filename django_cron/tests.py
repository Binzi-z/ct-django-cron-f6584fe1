import datetime
import threading
from time import sleep
from datetime import timedelta
from unittest import skip

from mock import patch
from freezegun import freeze_time

from django import db
from django.test import TransactionTestCase
from django.core.management import call_command
from django.test.utils import override_settings
from django.test.client import Client
from django.urls import reverse
from django.contrib.auth.models import User

from django_cron.helpers import humanize_duration
from django_cron.models import CronJobLog, CronJobLock
import test_crons


class OutBuffer(object):
    def __init__(self):
        self._str_cache = ''
        self.content = []
        self.modified = False

    def write(self, *args):
        self.content.extend(args)
        self.modified = True

    def str_content(self):
        if self.modified:
            self._str_cache = ''.join((str(x) for x in self.content))
            self.modified = False

        return self._str_cache


def call(command, *args, **kwargs):
    """
    Run the runcrons management command with a supressed output.
    """
    out_buffer = OutBuffer()
    call_command(command, *args, stdout=out_buffer, **kwargs)
    return out_buffer.str_content()


class TestRunCrons(TransactionTestCase):
    success_cron = 'test_crons.TestSuccessCronJob'
    error_cron = 'test_crons.TestErrorCronJob'
    five_mins_cron = 'test_crons.Test5minsCronJob'
    five_mins_with_tolerance_cron = 'test_crons.Test5minsWithToleranceCronJob'
    run_at_times_cron = 'test_crons.TestRunAtTimesCronJob'
    wait_3sec_cron = 'test_crons.Wait3secCronJob'
    run_on_wkend_cron = 'test_crons.RunOnWeekendCronJob'
    does_not_exist_cron = 'ThisCronObviouslyDoesntExist'
    no_code_cron = 'test_crons.NoCodeCronJob'
    test_failed_runs_notification_cron = (
        'django_cron.cron.FailedRunsNotificationCronJob'
    )
    run_on_month_days = 'test_crons.RunOnMonthDaysCronJob'
    run_and_remove_old_logs = 'test_crons.RunEveryMinuteAndRemoveOldLogs'

    def _call(self, *args, **kwargs):
        return call('runcrons', *args, **kwargs)

    def setUp(self):
        CronJobLog.objects.all().delete()

    def assertReportedRun(self, job_cls, response):
        expected_log = u"[\N{HEAVY CHECK MARK}] {0}".format(job_cls.code)
        self.assertIn(expected_log, response)

    def assertReportedNoRun(self, job_cls, response):
        expected_log = u"[ ] {0}".format(job_cls.code)
        self.assertIn(expected_log, response)

    def assertReportedFail(self, job_cls, response):
        expected_log = u"[\N{HEAVY BALLOT X}] {0}".format(job_cls.code)
        self.assertIn(expected_log, response)

    def test_success_cron(self):
        self._call(self.success_cron, force=True)
        self.assertEqual(CronJobLog.objects.all().count(), 1)

    def test_failed_cron(self):
        response = self._call(self.error_cron, force=True)
        self.assertReportedFail(test_crons.TestErrorCronJob, response)
        self.assertEqual(CronJobLog.objects.all().count(), 1)

    def test_not_exists_cron(self):
        response = self._call(self.does_not_exist_cron, force=True)
        self.assertIn('Make sure these are valid cron class names', response)
        self.assertIn(self.does_not_exist_cron, response)
        self.assertEqual(CronJobLog.objects.all().count(), 0)

    @patch('django_cron.core.logger')
    def test_requires_code(self, mock_logger):
        response = self._call(self.no_code_cron, force=True)
        self.assertIn('does not have a code attribute', response)
        mock_logger.info.assert_called()

    @override_settings(
        DJANGO_CRON_LOCK_BACKEND='django_cron.backends.lock.file.FileLock'
    )
    def test_file_locking_backend(self):
        self._call(self.success_cron, force=True)
        self.assertEqual(CronJobLog.objects.all().count(), 1)

    @override_settings(
        DJANGO_CRON_LOCK_BACKEND='django_cron.backends.lock.database.DatabaseLock'
    )
    def test_database_locking_backend(self):
        # TODO: to test it properly we would need to run multiple jobs at the same time
        cron_job_locks = CronJobLock.objects.all().count()
        for _ in range(3):
            self._call(self.success_cron, force=True)
        self.assertEqual(CronJobLog.objects.all().count(), 3)
        self.assertEqual(CronJobLock.objects.all().count(), cron_job_locks + 1)
        self.assertEqual(CronJobLock.objects.first().locked, False)

    @patch.object(test_crons.TestSuccessCronJob, 'do')
    def test_dry_run_does_not_perform_task(self, mock_do):
        response = self._call(self.success_cron, dry_run=True)
        self.assertReportedRun(test_crons.TestSuccessCronJob, response)
        mock_do.assert_not_called()
        self.assertFalse(CronJobLog.objects.exists())

    @patch.object(test_crons.TestSuccessCronJob, 'do')
    def test_non_dry_run_performs_task(self, mock_do):
        mock_do.return_value = 'message'
        response = self._call(self.success_cron)
        self.assertReportedRun(test_crons.TestSuccessCronJob, response)
        mock_do.assert_called_once()
        self.assertEqual(1, CronJobLog.objects.count())
        log = CronJobLog.objects.get()
        self.assertEqual(
            'message', log.message.strip()
        )  # CronJobManager adds new line at the end of each message
        self.assertTrue(log.is_success)

    def test_runs_every_mins(self):
        with freeze_time("2014-01-01 00:00:00"):
            response = self._call(self.five_mins_cron)
        self.assertReportedRun(test_crons.Test5minsCronJob, response)
        self.assertEqual(CronJobLog.objects.all().count(), 1)

        with freeze_time("2014-01-01 00:04:59"):
            response = self._call(self.five_mins_cron)
        self.assertReportedNoRun(test_crons.Test5minsCronJob, response)
        self.assertEqual(CronJobLog.objects.all().count(), 1)

        with freeze_time("2014-01-01 00:05:01"):
            response = self._call(self.five_mins_cron)
        self.assertReportedRun(test_crons.Test5minsCronJob, response)
        self.assertEqual(CronJobLog.objects.all().count(), 2)

    def test_runs_every_mins_with_tolerance(self):
        with freeze_time("2014-01-01 00:00:00"):
            call_command('runcrons', self.five_mins_with_tolerance_cron)
        self.assertEqual(CronJobLog.objects.all().count(), 1)

        with freeze_time("2014-01-01 00:04:59"):
            call_command('runcrons', self.five_mins_with_tolerance_cron)
        self.assertEqual(CronJobLog.objects.all().count(), 2)

        with freeze_time("2014-01-01 00:05:01"):
            call_command('runcrons', self.five_mins_with_tolerance_cron)
        self.assertEqual(CronJobLog.objects.all().count(), 2)

        with freeze_time("2014-01-01 00:09:40"):
            call_command('runcrons', self.five_mins_with_tolerance_cron)
        self.assertEqual(CronJobLog.objects.all().count(), 2)

        with freeze_time("2014-01-01 00:09:54"):
            call_command('runcrons', self.five_mins_with_tolerance_cron)
        self.assertEqual(CronJobLog.objects.all().count(), 2)

        with freeze_time("2014-01-01 00:09:55"):
            call_command('runcrons', self.five_mins_with_tolerance_cron)
        self.assertEqual(CronJobLog.objects.all().count(), 3)

    def test_runs_at_time(self):
        with freeze_time("2014-01-01 00:00:01"):
            response = self._call(self.run_at_times_cron)
        self.assertReportedRun(test_crons.TestRunAtTimesCronJob, response)
        self.assertEqual(CronJobLog.objects.all().count(), 1)

        with freeze_time("2014-01-01 00:04:50"):
            response = self._call(self.run_at_times_cron)
        self.assertReportedNoRun(test_crons.TestRunAtTimesCronJob, response)
        self.assertEqual(CronJobLog.objects.all().count(), 1)

        with freeze_time("2014-01-01 00:05:01"):
            response = self._call(self.run_at_times_cron)
        self.assertReportedRun(test_crons.TestRunAtTimesCronJob, response)
        self.assertEqual(CronJobLog.objects.all().count(), 2)

    def test_run_on_weekend(self):
        for test_date in ("2017-06-17", "2017-06-18"):  # Saturday and Sunday
            logs_count = CronJobLog.objects.all().count()
            with freeze_time(test_date):
                call_command('runcrons', self.run_on_wkend_cron)
            self.assertEqual(CronJobLog.objects.all().count(), logs_count + 1)

        for test_date in (
                "2017-06-19",
                "2017-06-20",
                "2017-06-21",
                "2017-06-22",
                "2017-06-23",
        ):  # Mon-Fri
            logs_count = CronJobLog.objects.all().count()
            with freeze_time(test_date):
                call_command('runcrons', self.run_on_wkend_cron)
            self.assertEqual(CronJobLog.objects.all().count(), logs_count)

    def test_run_on_month_days(self):
        for test_date in ("2010-10-1", "2010-10-10", "2010-10-20"):
            logs_count = CronJobLog.objects.all().count()
            with freeze_time(test_date):
                call_command('runcrons', self.run_on_month_days)
            self.assertEqual(CronJobLog.objects.all().count(), logs_count + 1)

        for test_date in (
                "2010-10-2",
                "2010-10-9",
                "2010-10-11",
                "2010-10-19",
                "2010-10-21",
        ):
            logs_count = CronJobLog.objects.all().count()
            with freeze_time(test_date):
                call_command('runcrons', self.run_on_month_days)
            self.assertEqual(CronJobLog.objects.all().count(), logs_count)

    def test_silent_produces_no_output_success(self):
        response = self._call(self.success_cron, silent=True)
        self.assertEqual(1, CronJobLog.objects.count())
        self.assertEqual('', response)

    def test_silent_produces_no_output_no_run(self):
        with freeze_time("2014-01-01 00:00:00"):
            response = self._call(self.run_at_times_cron, silent=True)
        self.assertEqual(1, CronJobLog.objects.count())
        self.assertEqual('', response)

        with freeze_time("2014-01-01 00:00:01"):
            response = self._call(self.run_at_times_cron, silent=True)
        self.assertEqual(1, CronJobLog.objects.count())
        self.assertEqual('', response)

    def test_silent_produces_no_output_failure(self):
        response = self._call(self.error_cron, silent=True)
        self.assertEqual('', response)

    def test_admin(self):
        password = 'test'
        user = User.objects.create_superuser('test', 'test@tivix.com', password)
        self.client = Client()
        self.client.login(username=user.username, password=password)

        # edit CronJobLog object
        self._call(self.success_cron, force=True)
        log = CronJobLog.objects.all()[0]
        url = reverse('admin:django_cron_cronjoblog_change', args=(log.id,))
        response = self.client.get(url)
        self.assertIn('Cron job logs', str(response.content))

    def run_cronjob_in_thread(self, logs_count):
        self._call(self.wait_3sec_cron)
        self.assertEqual(CronJobLog.objects.all().count(), logs_count + 1)
        db.close_old_connections()

    def test_cache_locking_backend(self):
        """
        with cache locking backend
        """
        t = threading.Thread(target=self.run_cronjob_in_thread, args=(0,))
        t.daemon = True
        t.start()
        # this shouldn't get running
        sleep(0.1)  # to avoid race condition
        self._call(self.wait_3sec_cron)
        t.join(10)
        self.assertEqual(CronJobLog.objects.all().count(), 1)

    # TODO: this test doesn't pass - seems that second cronjob is locking file
    # however it should throw an exception that file is locked by other cronjob
    # @override_settings(
    #     DJANGO_CRON_LOCK_BACKEND='django_cron.backends.lock.file.FileLock',
    #     DJANGO_CRON_LOCKFILE_PATH=os.path.join(os.getcwd())
    # )
    # def test_file_locking_backend_in_thread(self):
    #     """
    #     with file locking backend
    #     """
    #     logs_count = CronJobLog.objects.all().count()
    #     t = threading.Thread(target=self.run_cronjob_in_thread, args=(logs_count,))
    #     t.daemon = True
    #     t.start()
    #     # this shouldn't get running
    #     sleep(1)  # to avoid race condition
    #     self._call(self.wait_3sec_cron)
    #     t.join(10)
    #     self.assertEqual(CronJobLog.objects.all().count(), logs_count + 1)

    @skip  # TODO check why the test is failing
    def test_failed_runs_notification(self):
        CronJobLog.objects.all().delete()

        for i in range(10):
            self._call(self.error_cron, force=True)
        self._call(self.test_failed_runs_notification_cron)

        self.assertEqual(CronJobLog.objects.all().count(), 11)

    def test_humanize_duration(self):
        test_subjects = (
            (
                timedelta(days=1, hours=1, minutes=1, seconds=1),
                '1 day, 1 hour, 1 minute, 1 second',
            ),
            (timedelta(days=2), '2 days'),
            (timedelta(days=15, minutes=4), '15 days, 4 minutes'),
            (timedelta(), '< 1 second'),
        )

        for duration, humanized in test_subjects:
            self.assertEqual(humanize_duration(duration), humanized)

    def test_remove_old_succeeded_job_logs(self):
        mock_date = datetime.datetime(2022, 5, 1, 12, 0, 0)
        for _ in range(5):
            with freeze_time(mock_date):
                call_command('runcrons', self.run_and_remove_old_logs)
            self.assertEqual(CronJobLog.objects.all().count(), 1)
            self.assertEqual(CronJobLog.objects.all().first().end_time, mock_date)

    def test_run_job_with_logs_in_future(self):
        mock_date_in_future = datetime.datetime(2222, 5, 1, 12, 0, 0)
        with freeze_time(mock_date_in_future):
            call_command('runcrons', self.five_mins_cron)
            self.assertEqual(CronJobLog.objects.all().count(), 1)
            self.assertEqual(CronJobLog.objects.all().first().end_time, mock_date_in_future)

        mock_date_in_past = mock_date_in_future - timedelta(days=1000)
        with freeze_time(mock_date_in_past):
            call_command('runcrons', self.five_mins_cron)
            self.assertEqual(CronJobLog.objects.all().count(), 2)
            self.assertEqual(CronJobLog.objects.all().earliest('start_time').end_time, mock_date_in_past)

        mock_date_in_past_plus_one_min = mock_date_in_future + timedelta(minutes=1)
        with freeze_time(mock_date_in_past_plus_one_min):
            call_command('runcrons', self.five_mins_cron)
            self.assertEqual(CronJobLog.objects.all().count(), 2)
            self.assertEqual(CronJobLog.objects.all().earliest('start_time').end_time, mock_date_in_past)

    # ---- Concurrency & Lock Correctness Tests ----

    @override_settings(
        DJANGO_CRON_LOCK_BACKEND='django_cron.backends.lock.cache.CacheLock',
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_cache_lock_atomic_concurrent_acquisition(self):
        """Two threads try to lock simultaneously. Exactly one succeeds."""
        from django_cron.backends.lock.cache import CacheLock
        from django.core.cache import caches
        # Clear the LocMemCache to ensure fresh state
        caches['default'].clear()

        results = []
        barrier = threading.Barrier(2)

        def try_acquire(thread_id):
            lock = CacheLock(test_crons.SlowSuccessCronJob, silent=True)
            barrier.wait()
            acquired = lock.lock()
            results.append((thread_id, acquired))
            if acquired:
                sleep(0.5)
                lock.release()
            db.close_old_connections()

        t1 = threading.Thread(target=try_acquire, args=(1,))
        t2 = threading.Thread(target=try_acquire, args=(2,))
        t1.start(); t2.start()
        t1.join(10); t2.join(10)

        acquired_count = sum(1 for _, a in results if a)
        self.assertEqual(acquired_count, 1,
                         "Exactly one thread should acquire the lock")

    @override_settings(
        DJANGO_CRON_LOCK_BACKEND='django_cron.backends.lock.database.DatabaseLock'
    )
    def test_database_lock_atomic_concurrent_acquisition(self):
        """Two threads try to lock simultaneously. Exactly one succeeds."""
        from django_cron.backends.lock.database import DatabaseLock

        results = []
        barrier = threading.Barrier(2)

        def try_acquire(thread_id):
            try:
                lock = DatabaseLock(test_crons.SlowSuccessCronJob, silent=True)
                barrier.wait()
                acquired = lock.lock()
                results.append((thread_id, acquired))
                if acquired:
                    sleep(0.5)
                    lock.release()
            except Exception:
                # SQLite may raise OperationalError on concurrent writes;
                # this is expected in test environments and not a bug.
                results.append((thread_id, False))
            finally:
                db.close_old_connections()

        t1 = threading.Thread(target=try_acquire, args=(1,))
        t2 = threading.Thread(target=try_acquire, args=(2,))
        t1.start(); t2.start()
        t1.join(10); t2.join(10)

        acquired_count = sum(1 for _, a in results if a)
        # At least one should acquire, at most one should acquire
        self.assertGreaterEqual(acquired_count, 1,
                                "At least one thread should acquire the lock")
        self.assertLessEqual(acquired_count, 1,
                             "At most one thread should acquire the lock")

    @override_settings(
        DJANGO_CRON_LOCK_BACKEND='django_cron.backends.lock.cache.CacheLock',
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_cache_lock_release_ownership(self):
        """Instance B cannot release a lock held by instance A."""
        from django_cron.backends.lock.cache import CacheLock
        from django.core.cache import caches
        caches['default'].clear()

        lock_a = CacheLock(test_crons.TestSuccessCronJob, silent=True)
        lock_b = CacheLock(test_crons.TestSuccessCronJob, silent=True)

        self.assertTrue(lock_a.lock())
        lock_b.release()  # Should be a no-op (different owner_id)

        # Verify A's lock still held — B should fail to acquire
        self.assertFalse(lock_b.lock())

        # A releases its own lock
        lock_a.release()

        # Now B can acquire
        self.assertTrue(lock_b.lock())
        lock_b.release()

    @override_settings(
        DJANGO_CRON_LOCK_BACKEND='django_cron.backends.lock.database.DatabaseLock'
    )
    def test_database_lock_release_ownership(self):
        """Instance B cannot release a lock held by instance A."""
        from django_cron.backends.lock.database import DatabaseLock

        lock_a = DatabaseLock(test_crons.TestSuccessCronJob, silent=True)
        lock_b = DatabaseLock(test_crons.TestSuccessCronJob, silent=True)

        self.assertTrue(lock_a.lock())
        lock_b.release()  # No-op (different owner)

        self.assertFalse(lock_b.lock())

        lock_a.release()
        self.assertTrue(lock_b.lock())
        lock_b.release()

    @override_settings(
        DJANGO_CRON_LOCK_BACKEND='django_cron.backends.lock.database.DatabaseLock'
    )
    def test_database_lock_stale_recovery(self):
        """Lock with locked_at older than timeout can be reclaimed."""
        from django_cron.backends.lock.database import DatabaseLock
        from django.utils import timezone as tz

        job_name = '.'.join([
            test_crons.ShortTimeoutCronJob.__module__,
            test_crons.ShortTimeoutCronJob.__name__,
        ])
        # Simulate a crashed process: locked=True, locked_at is old
        CronJobLock.objects.create(
            job_name=job_name,
            locked=True,
            locked_at=tz.now() - timedelta(seconds=10),  # past 3s timeout
            owner='dead-machine:99999:abc123def456',
        )

        lock = DatabaseLock(test_crons.ShortTimeoutCronJob, silent=True)
        self.assertTrue(lock.lock(), "Should reclaim stale lock")
        lock.release()

        # Verify: row updated, not duplicated
        self.assertEqual(CronJobLock.objects.filter(job_name=job_name).count(), 1)

    @override_settings(
        DJANGO_CRON_LOCK_BACKEND='django_cron.backends.lock.cache.CacheLock',
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_cache_lock_expires_after_timeout(self):
        """After cache TTL expires, a new process can acquire the lock."""
        from django_cron.backends.lock.cache import CacheLock
        from django.core.cache import caches
        caches['default'].clear()

        lock_a = CacheLock(test_crons.ShortTimeoutCronJob, silent=True)
        self.assertTrue(lock_a.lock())
        # Don't release — simulate process death

        sleep(4)  # Wait for 3s timeout + 1s buffer

        lock_b = CacheLock(test_crons.ShortTimeoutCronJob, silent=True)
        self.assertTrue(lock_b.lock(), "Should acquire after TTL expiry")
        lock_b.release()

    def test_no_phantom_log_on_interrupted_execution(self):
        """Exception during do() produces failure log, not phantom success log."""
        CronJobLog.objects.all().delete()

        with patch.object(
            test_crons.TestSuccessCronJob, 'do',
            side_effect=Exception("simulated crash")
        ):
            self._call(self.success_cron, force=True)

        logs = CronJobLog.objects.all()
        self.assertEqual(logs.count(), 1)
        self.assertFalse(logs[0].is_success, "Interrupted job must not be success")
        self.assertNotIn('Job in progress', logs[0].message)

    @override_settings(
        DJANGO_CRON_LOCK_BACKEND='django_cron.backends.lock.cache.CacheLock',
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_concurrent_runcrons_only_one_executes(self):
        """Two concurrent runcrons for the same job: do() called exactly once."""
        from django.core.cache import caches
        caches['default'].clear()
        CronJobLog.objects.all().delete()

        do_call_count = {'n': 0}
        counter_lock = threading.Lock()
        barrier = threading.Barrier(2)

        original_do = test_crons.SlowSuccessCronJob.do

        def counting_do(cron_self):
            with counter_lock:
                do_call_count['n'] += 1
            return original_do(cron_self)

        def run_cron():
            barrier.wait()
            self._call('test_crons.SlowSuccessCronJob')
            db.close_old_connections()

        with patch.object(test_crons.SlowSuccessCronJob, 'do', counting_do):
            t1 = threading.Thread(target=run_cron)
            t2 = threading.Thread(target=run_cron)
            t1.start(); t2.start()
            t1.join(15); t2.join(15)

        self.assertEqual(do_call_count['n'], 1, "do() must only be called once")
        self.assertEqual(CronJobLog.objects.count(), 1)

    def test_sigterm_during_execution_releases_lock(self):
        """GracefulShutdown during cron execution releases the lock cleanly."""
        from django_cron.core import GracefulShutdown

        CronJobLog.objects.all().delete()

        def interrupt_do(cron_self):
            raise GracefulShutdown("simulated SIGTERM")

        with patch.object(test_crons.SlowSuccessCronJob, 'do', interrupt_do):
            self._call('test_crons.SlowSuccessCronJob', force=True)

        # Lock should be released. Verify by running again successfully.
        self._call('test_crons.SlowSuccessCronJob', force=True)
        self.assertEqual(CronJobLog.objects.count(), 2)


class TestCronLoopShutdown(TransactionTestCase):
    success_cron = 'test_crons.TestSuccessCronJob'

    def test_cronloop_stops_on_shutdown_flag(self):
        """cronloop exits when _shutdown_requested is set."""
        from django_cron.management.commands.cronloop import Command as CronloopCommand
        import time as _time

        cmd = CronloopCommand()
        cmd._shutdown_requested = False

        iterations = {'n': 0}

        original_run_once = cmd._run_once

        def counting_run_once(classes, s):
            iterations['n'] += 1
            if iterations['n'] >= 2:
                cmd._shutdown_requested = True
            return original_run_once(classes, s)

        cmd._run_once = counting_run_once

        start = _time.time()
        # Use sleep=0 for fast test execution
        cmd.handle(
            sleep=0,
            cron_classes=[self.success_cron],
            repeat=None,
        )
        elapsed = _time.time() - start

        self.assertEqual(iterations['n'], 2, "Should have run exactly 2 iterations")
        self.assertLess(elapsed, 30, "cronloop should stop promptly")


class TestCronLoop(TransactionTestCase):
    success_cron = 'test_crons.TestSuccessCronJob'

    def _call(self, *args, **kwargs):
        return call('cronloop', *args, **kwargs)

    def test_repeat_twice(self):
        self._call(
            cron_classes=[self.success_cron, self.success_cron], repeat=2, sleep=1
        )
        self.assertEqual(CronJobLog.objects.all().count(), 4)
