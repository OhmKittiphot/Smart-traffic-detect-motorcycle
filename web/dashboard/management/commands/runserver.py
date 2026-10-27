from django.contrib.staticfiles.management.commands.runserver import Command as StaticfilesRunserver
from django.db import DatabaseError

from dashboard.engine import resume_logged_in_staff


class Command(StaticfilesRunserver):
    """Django's development server, which also resumes detection for staff still logged in, as run.py does.
    Without it, a browser still logged in from before a restart (or an autoreload after a code change) sees
    the live view with detection stopped until it logs in again. ("dashboard" comes before
    "django.contrib.staticfiles" in INSTALLED_APPS so this command replaces that one.)"""

    def inner_run(self, *args, **options):
        # Runs in the process that serves (the autoreloader's child), each time it starts
        try:
            resume_logged_in_staff()
        except DatabaseError as e:
            self.stderr.write(f"Could not resume detection ({e}); run: venv\\Scripts\\python web\\manage.py migrate")
        super().inner_run(*args, **options)
