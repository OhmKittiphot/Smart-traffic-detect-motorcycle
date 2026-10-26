"""Server-Sent Events: the server pushes to the page instead of the page asking every few seconds.

- records: new records as they are saved. The live view's table gets every record; the notifications of
  other pages only violations.
- status: the live view's engine state, playback position and today's counts, every second.

Each open stream holds one server thread (run.py), and ends once its session has expired or the user
logged out."""
import json
import time

from django.db.models import Max
from django.http import JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from ..engine import current_service, stopped_status
from ..models import VehicleRecord
from ..record_feed import saved_count, wait_for_record
from ..serializers import RecordEventsSerializer, VehicleRecordSerializer

# Rows sent per event: the live view's table, and the notifications (the other violations are only counted)
_MAX_ROWS = 200
_MAX_NOTIFY_ROWS = 3
# The live view's status is sent this often (its FPS chart takes one point per status)
_STATUS_EVERY_S = 1.0
# A comment line is sent this often while nothing happens: the server only notices a closed page when it
# writes, and some proxies close a quiet connection
_KEEPALIVE_S = 15
# How long the browser waits before connecting again after the stream dropped
_RETRY_MS = 3000


def _message(event: str, data, event_id=None) -> str:
    """One event; its id, if given, is where the browser resumes after reconnecting (Last-Event-ID)."""
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _session_ended(session) -> bool:
    return not session.exists(session.session_key) or session.get_expiry_date() <= timezone.now()


def _event_stream(events):
    response = StreamingHttpResponse(events, content_type="text/event-stream")
    response["Cache-Control"] = "no-store"
    response["X-Accel-Buffering"] = "no"   # a reverse proxy in front must not hold the events back
    return response


# ---------- records ----------

def _latest_id() -> int:
    return VehicleRecord.objects.aggregate(last=Max("id"))["last"] or 0


@require_GET
def record_events(request):
    """"records" events: the records saved after ?after=<id> (or the browser's Last-Event-ID); without it,
    from now on. ?violations=true sends only violations, as do accounts that see records but not the
    live view."""
    user = request.user
    violations_only = request.GET.get("violations") == "true" or not user.has_perm("dashboard.use_dashboard")
    if not user.has_perm("dashboard.view_records" if violations_only else "dashboard.use_dashboard"):
        return JsonResponse({"detail": "you do not have permission for this"}, status=403)
    query = RecordEventsSerializer(data={"after": request.headers.get("Last-Event-ID", request.GET.get("after"))})
    if not query.is_valid():
        return JsonResponse(query.errors, status=400)
    after = query.validated_data["after"]
    if after is None:
        after = _latest_id()

    records = VehicleRecord.objects.select_related("reviewed_by")
    if violations_only:
        records = records.filter(is_violation=True)
    limit = _MAX_NOTIFY_ROWS if violations_only else _MAX_ROWS
    session = request.session

    def events():
        nonlocal after
        # First an event with no rows: tells the page where its stream starts
        yield f"retry: {_RETRY_MS}\n" + _message("records", {"rows": [], "total": 0}, after)
        while not _session_ended(session):
            # Up to the newest record now: one saved meanwhile is counted, so the wait below returns at once
            seen = saved_count()
            latest = _latest_id()
            new = records.filter(id__gt=after, id__lte=latest)
            rows = VehicleRecordSerializer(new[:limit], many=True).data   # newest first
            if rows:
                total = new.count() if len(rows) == limit else len(rows)
                yield _message("records", {"rows": rows, "total": total}, latest)
            after = max(after, latest)
            if not wait_for_record(seen, _KEEPALIVE_S):
                yield ": keepalive\n\n"

    return _event_stream(events())


# ---------- live view status ----------

def _today_counts() -> dict:
    today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    records = VehicleRecord.objects.filter(detected_at__gte=today)
    total = records.count()
    violations = records.filter(is_violation=True).count()
    return {"total": total, "violation": violations, "normal": total - violations}


@require_GET
def status_events(request):
    """"status" events for the live view, every second: engine state, playback position and today's
    vehicle counts."""
    session = request.session

    def events():
        yield f"retry: {_RETRY_MS}\n\n"
        while not _session_ended(session):
            # Looked up every time, so it follows detection when it stops or starts (never started here:
            # only logging in does, see engine.py)
            service = current_service()
            status = service.status() if service else stopped_status()
            yield _message("status", {**status, "today": _today_counts()})
            time.sleep(_STATUS_EVERY_S)

    return _event_stream(events())
