from django.core.management.base import BaseCommand

from dashboard import storage


class Command(BaseCommand):
    help = "Delete old records and photos now, as run.py does every few hours (see dashboard/storage.py)."

    def handle(self, *args, **options):
        result = storage.clean_up()
        usage = storage.usage()
        self.stdout.write(f"Deleted {result['records_deleted']} old records; removed the photos of "
                          f"{result['photos_removed']} records ({result['bytes_freed'] / 1024 / 1024:.1f} MB freed).")
        self.stdout.write(f"Photos now use {usage['photos'] / 1024 / 1024:.1f} MB "
                          f"of {usage['photos_limit'] / 1024 / 1024:.0f} MB.")
