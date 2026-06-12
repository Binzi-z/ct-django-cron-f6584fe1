import signal
from time import sleep

from django.core.management import BaseCommand, call_command

from django_cron.core import GracefulShutdown


class Command(BaseCommand):
    help = 'Run cronjobs in loop.'

    def add_arguments(self, parser):
        parser.add_argument(
            '-s',
            '--sleep',
            dest='sleep',
            type=int,
            help="Sleep interval in seconds.",
            default=5 * 60,
        )
        parser.add_argument(
            '--cron_classes',
            dest='cron_classes',
            nargs='+',
            help="List of cron classes to run.",
        )
        parser.add_argument(
            '--repeat',
            dest='repeat',
            type=int,
            help="Repeat only X times.",
        )

    def handle(self, *args, **options):
        s = options['sleep']
        classes = options['cron_classes']
        if not classes:
            classes = []
        repeat = options["repeat"]

        self._shutdown_requested = False
        previous_handler = signal.signal(
            signal.SIGTERM, self._handle_sigterm
        )

        try:
            if repeat:
                for _ in range(repeat):
                    if self._run_once(classes, s):
                        break
            else:
                while not self._shutdown_requested:
                    if self._run_once(classes, s):
                        break
        finally:
            signal.signal(signal.SIGTERM, previous_handler)

    def _handle_sigterm(self, signum, frame):
        """Set flag so the loop exits cleanly after current iteration."""
        self._shutdown_requested = True

    def _run_once(self, classes, s):
        """Run one iteration. Returns True if loop should stop."""
        try:
            call_command('runcrons', *classes)
            sleep(s)
        except (KeyboardInterrupt, GracefulShutdown):
            return True
        return False
