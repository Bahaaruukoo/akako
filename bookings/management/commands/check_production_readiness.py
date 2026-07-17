from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Check Akako House production email, HTTPS, payment, and private-storage settings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit with an error when production requirements are not configured.",
        )

    def handle(self, *args, **options):
        findings = []
        if settings.DEBUG:
            findings.append("DEBUG is enabled.")
        if "console" in settings.EMAIL_BACKEND:
            findings.append("Email still uses the console backend; verification and reminders will not reach users.")
        if settings.PUBLIC_BASE_URL.startswith("http://127.0.0.1") or settings.PUBLIC_BASE_URL.startswith("http://localhost"):
            findings.append("PUBLIC_BASE_URL still points to local development.")
        if settings.PAYMENT_PROVIDER == "manual":
            findings.append("Online payment checkout is disabled (PAYMENT_PROVIDER=manual).")
        private_storage_backend = settings.STORAGES["private_documents"]["BACKEND"]
        if private_storage_backend == "django.core.files.storage.FileSystemStorage":
            findings.append("Partner documents still use local filesystem storage; configure private hosted storage.")
        if not settings.SECURE_SSL_REDIRECT or not settings.SESSION_COOKIE_SECURE or not settings.CSRF_COOKIE_SECURE:
            findings.append("HTTPS redirect and secure session/CSRF cookies are not fully enabled.")

        if findings:
            for finding in findings:
                self.stdout.write(self.style.WARNING(f"WARNING: {finding}"))
            self.stdout.write(
                "Schedule `python manage.py process_deadlines` at least every 15 minutes in production."
            )
            if options["strict"]:
                raise CommandError(f"Production readiness failed with {len(findings)} warning(s).")
        else:
            self.stdout.write(self.style.SUCCESS("Production configuration checks passed."))
