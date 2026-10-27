"""Start the CCTV admin dashboard (web server + detection) with one command:

    venv\Scripts\python web\run.py

Then open http://127.0.0.1:8000 and log in with an admin account
(create one first with: venv\Scripts\python web\manage.py createsuperuser).
Detection (models, video stream) runs only while staff are logged in.
Always use the project's venv: the detection models and Django are installed there.
"""
import multiprocessing as mp
import os
import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
PROJECT_DIR = WEB_DIR.parent


def main():
    # Model paths in AppConfig are relative to the project root
    os.chdir(PROJECT_DIR)
    sys.path.insert(0, str(WEB_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "admin.settings")
    for stream in (sys.stdout, sys.stderr):
        try:
            # Line-buffered so pipeline messages show up immediately when output is piped to a log
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass

    import django
    from django.core.management import call_command

    django.setup()
    call_command("migrate", interactive=False, verbosity=0)
    call_command("collectstatic", interactive=False, verbosity=0)

    from django.contrib.auth import get_user_model

    if not get_user_model().objects.filter(is_staff=True, is_active=True).exists():
        print("\nNo admin account yet. Create one first, then start again:\n"
              "    venv\\Scripts\\python web\\manage.py createsuperuser\n")
        sys.exit(1)

    from django.conf import settings
    from waitress import serve

    from admin.wsgi import application
    from dashboard.engine import resume_logged_in_staff, stop_service
    from dashboard.storage import start_cleanup_thread

    # Old records and photos are removed now and every few hours (limits in .env, see dashboard/storage.py)
    start_cleanup_thread()
    # Detection starts when staff log in; those still logged in from before the restart count too
    resume_logged_in_staff()

    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.getenv("DASHBOARD_PORT", "8000"))
    # Each open video stream and event stream (views/events.py) holds one thread while its tab is shown:
    # up to 4 for a live view, at most 1 for the other pages. The rest answer API requests
    threads = int(os.getenv("DASHBOARD_THREADS", "48"))
    if host not in ("127.0.0.1", "localhost") and not os.getenv("DJANGO_ALLOWED_HOSTS"):
        print(f"[Warning] Listening on {host} but DJANGO_ALLOWED_HOSTS is not set; other hosts will get 400.")
    if settings.DEBUG:
        print("[Warning] DJANGO_DEBUG is on. Never use it on a shared network.")

    print(f"\nCCTV dashboard: http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}\n")
    try:
        serve(application, host=host, port=port, threads=threads, ident="cctv",
              channel_timeout=60, connection_limit=200)
    finally:
        stop_service()


if __name__ == "__main__":
    mp.freeze_support()
    main()
