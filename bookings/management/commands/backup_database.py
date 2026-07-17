import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Create a consistent timestamped backup of the local SQLite database."

    def add_arguments(self, parser):
        parser.add_argument("--directory", default=str(settings.BASE_DIR / "backups"))

    def handle(self, *args, **options):
        database = settings.DATABASES["default"]
        if database["ENGINE"] != "django.db.backends.sqlite3":
            raise CommandError("Use the managed database provider's native backup tooling.")
        source_path = Path(database["NAME"])
        backup_directory = Path(options["directory"])
        backup_directory.mkdir(parents=True, exist_ok=True)
        timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        destination = backup_directory / f"akako-{timestamp}.sqlite3"
        with sqlite3.connect(source_path) as source, sqlite3.connect(destination) as target:
            source.backup(target)
        self.stdout.write(self.style.SUCCESS(f"Database backup created: {destination}"))
