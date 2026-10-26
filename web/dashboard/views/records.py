"""Recorded vehicles: plate search, reviewing and deleting records, violation statistics, evidence photos
and the notifications of new violations."""
from datetime import datetime, time, timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import Count, Max, Q
from django.db.models.functions import ExtractHour, ExtractIsoWeekDay, TruncDate
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import storage
from ..models import Review, VehicleRecord, Violation
from ..plate_search import group_plates, normalize_query, rank_plates, split_queries
from ..serializers import (PlateSearchSerializer, ReviewSerializer, StatsQuerySerializer, VehicleDeleteSerializer,
                           VehicleQuerySerializer, VehicleRecordSerializer)
from .common import audit

# Plate search: lowest similarity shown, and how many matching plates / rows are returned
_SEARCH_MIN_SCORE = 0.6
_SEARCH_MAX_PLATES = 300
_SEARCH_MAX_ROWS = 300
# Rows per page when the violations page lists every record (no plate typed)
_BROWSE_PAGE_ROWS = 100
# Violations sent per poll for the in-page notifications (the others are only counted)
_MAX_NOTIFY_ROWS = 3
# Repeat offenders: how alike two plates must be to count as one motorcycle (one look-alike character
# passes, one different character does not), the most frequent plates compared, and the groups returned
_REPEAT_MIN_SCORE = 0.9
_REPEAT_MAX_PLATES = 300
_REPEAT_ROWS = 8


# ---------- finding records ----------

def _filtered(records, filters):
    """records narrowed by the list's filters (validated PlateSearchSerializer data)."""
    if filters["violations_only"]:
        records = records.filter(is_violation=True)
    if filters["province"]:
        records = records.filter(province_text=filters["province"])
    if filters["source"]:
        mode, one_source, name = filters["source"].partition(":")
        records = records.filter(source_mode=mode)
        if one_source:   # "<mode>:<name>" is one camera or file; "<mode>" alone is the whole group
            records = records.filter(source_name=name)
    return records


def _plate_matches(q, filters) -> list:
    """Records whose plate looks like q, best match first (see plate_search.py)."""
    records = _filtered(VehicleRecord.objects.exclude(plate_text="").select_related("reviewed_by"), filters)
    # Score each distinct plate once; a plate seen many times is still one comparison
    plates = records.order_by().values_list("plate_text", flat=True).distinct()
    scores = rank_plates(q, plates, _SEARCH_MIN_SCORE)
    best = sorted(scores, key=scores.get, reverse=True)[:_SEARCH_MAX_PLATES]

    matches = []
    for i in range(0, len(best), 500):   # stay under SQLite's limit on query parameters
        matches += records.filter(plate_text__in=best[i:i + 500])
    matches.sort(key=lambda r: (-scores[r.plate_text], -r.id))
    return matches


def _all_records(filters):
    """Every record (unreadable plates too), newest first: the list shown before a plate is typed."""
    return _filtered(VehicleRecord.objects.select_related("reviewed_by"), filters)


class PlateSearchView(APIView):
    def get(self, request):
        """Vehicles whose plate looks like the query, best match first. With no query, every record,
        newest first, a page at a time (next_before: the "before" of the next page, or null)."""
        query = PlateSearchSerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        q, before = filters["q"], filters["before"]
        if not q:
            records = _all_records(filters)
            total = records.count()
            if before:
                records = records.filter(id__lt=before)
            page = list(records[:_BROWSE_PAGE_ROWS])
            next_before = page[-1].id if len(page) == _BROWSE_PAGE_ROWS else None
            return Response({"query": "", "total": total, "next_before": next_before,
                             "rows": VehicleRecordSerializer(page, many=True).data})

        matches = _plate_matches(q, filters)
        rows = VehicleRecordSerializer(matches[:_SEARCH_MAX_ROWS], many=True).data
        query = ", ".join(normalize_query(plate) for plate in split_queries(q))
        return Response({"query": query, "total": len(matches), "next_before": None, "rows": rows})


class NewViolationsView(APIView):
    def get(self, request):
        """Violations recorded after ?after=<id>, for the in-page notifications on every page: the newest few
        as rows, and how many there are. With after=0 only the id to start from is sent, so violations
        from before the page opened are not announced."""
        query = VehicleQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        after = query.validated_data["after"]
        last_id = VehicleRecord.objects.aggregate(last=Max("id"))["last"] or 0
        if after == 0:
            return Response({"last_id": last_id, "total": 0, "rows": []})
        new = VehicleRecord.objects.filter(id__gt=after, is_violation=True)
        return Response({"last_id": last_id, "total": new.count(),
                         "rows": VehicleRecordSerializer(new[:_MAX_NOTIFY_ROWS], many=True).data})


# ---------- changing records ----------

class VehicleReviewView(APIView):
    def post(self, request, pk: int):
        """Confirm or reject one recorded event (or undo the review), with an optional note."""
        serializer = ReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = get_object_or_404(VehicleRecord, pk=pk)
        record.review = serializer.validated_data["review"]
        reviewed = record.review != Review.PENDING
        record.review_note = serializer.validated_data["note"] if reviewed else ""
        record.reviewed_at = timezone.now() if reviewed else None
        record.reviewed_by = request.user if reviewed else None
        record.save(update_fields=["review", "review_note", "reviewed_at", "reviewed_by"])
        audit(request, f"review_{record.review}", f"#{record.pk} {record.plate_text or '-'}")
        return Response(VehicleRecordSerializer(record).data)


class VehicleDeleteView(APIView):
    def post(self, request):
        """Admin: delete the selected records, or every record of the list as filtered on the
        violations page (all matches of a plate search, not only the rows shown)."""
        serializer = VehicleDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not data["all"]:
            ids = data["ids"]
        elif data["q"]:
            ids = [r.id for r in _plate_matches(data["q"], data)]
        else:
            ids = _all_records(data).values_list("id", flat=True)
        deleted = storage.delete_records(ids)
        which = "whole list" if data["all"] else "selected"
        audit(request, "delete_vehicles", f"{deleted} records ({which})")
        return Response({"deleted": deleted})


# ---------- statistics and photos ----------

def _in_days(first, last):
    """Records detected from the start of local day `first` to the end of local day `last`."""
    def midnight(day):
        return timezone.make_aware(datetime.combine(day, time.min))
    return VehicleRecord.objects.filter(detected_at__gte=midnight(first),
                                        detected_at__lt=midnight(last + timedelta(days=1)))


def _per_day(first, last) -> list:
    """Motorcycles and violations of each day from first to last, days without any included."""
    counts = {row["day"]: row for row in _in_days(first, last)
              .annotate(day=TruncDate("detected_at", tzinfo=timezone.get_current_timezone()))
              .values("day")
              .annotate(total=Count("id"), violation=Count("id", filter=Q(is_violation=True)))}
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    return [{"date": d.strftime("%d/%m"),
             "total": counts.get(d, {}).get("total", 0),
             "violation": counts.get(d, {}).get("violation", 0)} for d in days]


def _by_hour(violations) -> list:
    """Violations by weekday (Monday first) and hour of the day: 7 rows of 24 counts."""
    tz = timezone.get_current_timezone()
    grid = [[0] * 24 for _ in range(7)]
    for row in (violations.annotate(weekday=ExtractIsoWeekDay("detected_at", tzinfo=tz),
                                    hour=ExtractHour("detected_at", tzinfo=tz))
                .values("weekday", "hour").annotate(n=Count("id"))):
        grid[row["weekday"] - 1][row["hour"]] = row["n"]
    return grid


def _stats_range(request):
    query = StatsQuerySerializer(data=request.query_params)
    query.is_valid(raise_exception=True)
    return query.validated_data["start"], query.validated_data["end"]


class StatsView(APIView):
    def get(self, request):
        """Violation statistics for the violations page. The last 7 days always end today; the rest covers
        the chosen date range: totals, violations by type, per day and by time of day."""
        first, last = _stats_range(request)
        today = timezone.localdate()
        violations = _in_days(first, last).filter(is_violation=True)
        return Response({
            "start": first.isoformat(),
            "end": last.isoformat(),
            "days": (last - first).days + 1,
            "last_week": _per_day(today - timedelta(days=6), today),
            "violation": violations.count(),
            "total": _in_days(first, last).count(),
            "pending_review": violations.filter(review=Review.PENDING).count(),
            "confirmed": violations.filter(review=Review.CONFIRMED).count(),
            "by_type": [{"label": v.label, "count": violations.filter(violations__contains=v.value).count()}
                        for v in Violation],
            "per_day": _per_day(first, last),
            "by_hour": _by_hour(violations),
        })


class RepeatOffendersView(APIView):
    def get(self, request):
        """Plates with more than one violation in the chosen date range, most violations first. Plates OCR
        read slightly differently count as one motorcycle (plate_search.group_plates); rejected events
        are left out."""
        first, last = _stats_range(request)
        violations = (_in_days(first, last).filter(is_violation=True)
                      .exclude(review=Review.REJECTED).exclude(plate_text=""))
        counts = dict(violations.values("plate_text").annotate(n=Count("id")).order_by("-n")
                      .values_list("plate_text", "n")[:_REPEAT_MAX_PLATES])
        groups = [g for g in group_plates(counts, _REPEAT_MIN_SCORE) if sum(counts[p] for p in g) > 1]
        groups.sort(key=lambda g: -sum(counts[p] for p in g))

        rows = []
        for plates in groups[:_REPEAT_ROWS]:
            seen = violations.filter(plate_text__in=plates)
            province = (seen.exclude(province_text="").values("province_text")
                        .annotate(n=Count("id")).order_by("-n").first())
            rows.append({
                "plate": plates[0],
                "also_read_as": plates[1:],
                "province": province["province_text"] if province else "",
                "count": sum(counts[p] for p in plates),
                "types": [v.label for v in Violation if seen.filter(violations__contains=v.value).exists()],
                "last_seen": timezone.localtime(seen.aggregate(last=Max("detected_at"))["last"])
                                     .strftime("%d/%m %H:%M"),
            })
        return Response({"rows": rows})


@require_GET
def snapshot(request, pk: int, field: str = "snapshot"):
    """An evidence photo: the motorcycle (field "snapshot") or its licence plate ("plate_snapshot")."""
    record = VehicleRecord.objects.filter(pk=pk).only(field).first()
    photo = getattr(record, field) if record is not None else ""
    if not photo:
        raise Http404
    media_root = Path(settings.MEDIA_ROOT).resolve()
    path = (media_root / photo).resolve()
    # Paths come from our own DB, but never serve anything outside MEDIA_ROOT
    if media_root not in path.parents or not path.is_file():
        raise Http404
    return FileResponse(open(path, "rb"), content_type="image/jpeg")
