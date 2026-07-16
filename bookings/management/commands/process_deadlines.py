from django.core.management.base import BaseCommand

from bookings.services import process_workflow_deadlines


class Command(BaseCommand):
    help = "Process quote, payment, ceremony, and partner-document notification deadlines."

    def handle(self, *args, **options):
        expired_quotes, ceremonies_checked = process_workflow_deadlines()
        self.stdout.write(
            self.style.SUCCESS(
                f"Processed deadlines: {expired_quotes} quote(s) expired; "
                f"{ceremonies_checked} active ceremony record(s) checked."
            )
        )
