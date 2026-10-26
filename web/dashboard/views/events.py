"""Server-Sent Events: new records are pushed to the page as they are saved, instead of the page asking
every few seconds. The live view's table gets every record; the notifications of other pages only
violations. Each open stream holds one server thread (run.py)."""
import json

from django.db.models import Max
from django.http import JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from ..models import VehicleRecord
from ..record_feed import saved_count, wait_for_record
from ..serializers import RecordEventsSerializer, VehicleRecordSerializer

# Rows sent per event: the live view's table, and the notifications (the other violations are only counted)
_MAX_ROWS = 200
_MAX_NOTIFY_ROWS = 3
# A comment line is sent this often while nothing happens: the server only notices a closed page when it
# writes, and some proxies close a quiet connection
_KEEPALIVE_S = 15
# How long the browser waits before connecting again after the stream dropped
_RETRY_MS = 3000


def _latest_id() -> int:
    return VehicleRecord.objects.aggregate(last=Max("id"))["last"] or 0


def _event(rows, total, last_id) -> str:
    """One "records" event. Its id, the newest record checked, is where the browser resumes after reconnecting."""
    data = json.dumps({"rows": rows, "total": total}, ensure_ascii=False)
    return f"id: {last_id}\nevent: records\ndata: {data}\n\n"


@require_GET
def record_events(request):
    """text/event-stream of the records saved after ?after=<id> (or the browser's Last-Event-ID); without
    it, from now on. ?violations=true sends only violations, as do accounts that see records but not the
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
        yield f"retry: {_RETRY_MS}\n" + _event([], 0, after)
        while True:
            # A stream outlives its page; stop it once the session has expired or the user logged out
            if not session.exists(session.session_key) or session.get_expiry_date() <= timezone.now():
                return
            # Up to the newest record now: one saved meanwhile is counted, so the wait below returns at once
            seen = saved_count()
            latest = _latest_id()
            new = records.filter(id__gt=after, id__lte=latest)
            rows = VehicleRecordSerializer(new[:limit], many=True).data   # newest first
            if rows:
                total = new.count() if len(rows) == limit else len(rows)
                yield _event(rows, total, latest)
            after = max(after, latest)
            if not wait_for_record(seen, _KEEPALIVE_S):
                yield ": keepalive\n\n"

    response = StreamingHttpResponse(events(), content_type="text/event-stream")
    response["Cache-Control"] = "no-store"
    response["X-Accel-Buffering"] = "no"   # a reverse proxy in front must not hold the events back
    return response
