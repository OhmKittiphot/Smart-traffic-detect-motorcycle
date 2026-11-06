# Recorded vehicles: plate search, review, delete, statistics and photos.
# The records list and details are HTML for htmx; the rest is JSON for the page's JavaScript.
import calendar
from datetime import datetime, time, timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import Count, Max, Q
from django.db.models.functions import TruncDate
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import storage
from ..models import Review, VehicleRecord, Violation
from ..plate_search import group_plates, normalize_query, rank_plates, split_queries
from ..serializers import PlateSearchSerializer, ReviewSerializer, StatsQuerySerializer, VehicleDeleteSerializer
from .common import audit

# Plate search: lowest similarity shown, and how many matching plates / rows are returned
_SEARCH_MIN_SCORE = 0.6
_SEARCH_MAX_PLATES = 300
_SEARCH_MAX_ROWS = 300
# Rows per page when the violations page lists every record (no plate typed)
_BROWSE_PAGE_ROWS = 100
# Repeat offenders: how alike two plates must be to count as one motorcycle (one look-alike character
# passes, one different character does not), the most frequent plates compared, and the groups returned
_REPEAT_MIN_SCORE = 0.9
_REPEAT_MAX_PLATES = 300
_REPEAT_ROWS = 8


# ---------- finding records ----------

def _from_source(records, source):
    # records saved from `source` (validated, see serializers._known_source); "" keeps them all.
    if source.startswith("location"):
        _location, one_location, name = source.partition(":")
        return records.filter(location=name) if one_location else records.exclude(location="")
    if source:
        mode, one_source, name = source.partition(":")
        records = records.filter(source_mode=mode)
        if one_source:   # "<mode>:<name>" is one camera or file; "<mode>" alone is the whole group
            records = records.filter(source_name=name)
    return records


def _filtered(records, filters):
    # records narrowed by the list's filters (validated PlateSearchSerializer data).
    if filters["violations_only"]:
        records = records.filter(is_violation=True)
    if filters["province"]:
        records = records.filter(province_text=filters["province"])
    return _from_source(records, filters["source"])


def _plate_matches(q, filters) -> list:
    # Records whose plate looks like q, best match first (see plate_search.py).
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
    # Every record (unreadable plates too), newest first: the list shown before a plate is typed.
    return _filtered(VehicleRecord.objects.select_related("reviewed_by"), filters)


def _first_error(errors) -> str:
    # The first message of a serializer's errors ({"<field>": ["..."]}).
    return str(next(iter(errors.values()))[0])


@require_GET
def vehicle_rows(request):
    # The rows of the records list (htmx): vehicles whose plate looks like the query, best match first.
    # With no query, the violations page (browse) lists every record, newest first, a page at a time; the
    # plate search page lists nothing until a plate is typed.
    query = PlateSearchSerializer(data=request.GET)
    if not query.is_valid():
        # Answered with 200 so that htmx shows the message; HX-Reswap keeps the rows already listed
        response = render(request, "dashboard/_search_msg.html", {"error": _first_error(query.errors)})
        response["HX-Reswap"] = "none"
        return response
    filters = query.validated_data
    q, before = filters["q"], filters["before"]
    # The list's filters without the page: for "Load more", and for deleting every record of the list
    params = request.GET.copy()
    params.pop("before", None)
    context = {"params": params.urlencode(), "browse": filters["browse"], "query": "", "rows": [],
               "total": 0, "shown": 0, "next_before": None,
               "can_delete": filters["browse"] and request.user.has_perm("dashboard.delete_records")}

    if q:
        matches = _plate_matches(q, filters)
        rows = matches[:_SEARCH_MAX_ROWS]
        context.update(query=", ".join(normalize_query(plate) for plate in split_queries(q)),
                       rows=rows, total=len(matches), shown=len(rows))
    elif filters["browse"]:
        records = _all_records(filters)
        total = records.count()
        if before:
            records = records.filter(id__lt=before)
        page = list(records[:_BROWSE_PAGE_ROWS])
        not_shown = records.filter(id__lt=page[-1].id).count() if page else 0
        context.update(rows=page, total=total, shown=total - not_shown,
                       next_before=page[-1].id if not_shown else None)
    return render(request, "dashboard/_vehicle_rows.html", context)


@require_GET
def vehicle_detail(request, pk: int):
    # The details pop-up of one record (htmx), with the review buttons for staff.
    record = get_object_or_404(VehicleRecord.objects.select_related("reviewed_by"), pk=pk)
    return render(request, "dashboard/_vehicle_detail.html", {"record": record})


# ---------- changing records ----------

@require_POST
def review_vehicle(request, pk: int):
    # Confirm or reject one recorded event (or undo the review), with an optional note (htmx). Answers
    # with the details pop-up again, and the record's new review tag for its row in the list.
    record = get_object_or_404(VehicleRecord.objects.select_related("reviewed_by"), pk=pk)
    serializer = ReviewSerializer(data=request.POST)
    if not serializer.is_valid():
        return render(request, "dashboard/_vehicle_detail.html",
                      {"record": record, "error": _first_error(serializer.errors)})
    record.review = serializer.validated_data["review"]
    reviewed = record.review != Review.PENDING
    record.review_note = serializer.validated_data["note"] if reviewed else ""
    record.reviewed_at = timezone.now() if reviewed else None
    record.reviewed_by = request.user if reviewed else None
    record.save(update_fields=["review", "review_note", "reviewed_at", "reviewed_by"])
    audit(request, f"review_{record.review}", f"#{record.pk} {record.plate_text or '-'}")
    return render(request, "dashboard/_vehicle_detail.html", {"record": record, "saved": True})


class VehicleDeleteView(APIView):
    def post(self, request):
        # Admin: delete the selected records, or every record of the list as filtered on the
        # violations page (all matches of a plate search, not only the rows shown).
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

def _in_days(first, last, source=""):
    # Records of `source` detected from the start of local day `first` to the end of local day `last`.
    def midnight(day):
        return timezone.make_aware(datetime.combine(day, time.min))
    return _from_source(VehicleRecord.objects, source).filter(detected_at__gte=midnight(first),
                                                              detected_at__lt=midnight(last + timedelta(days=1)))


def _per_day(first, last, source="") -> list:
    # Motorcycles and violations of each day from first to last, days without any included.
    counts = {row["day"]: row for row in _in_days(first, last, source)
              .annotate(day=TruncDate("detected_at", tzinfo=timezone.get_current_timezone()))
              .values("day")
              .annotate(total=Count("id"), violation=Count("id", filter=Q(is_violation=True)))}
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    return [{"date": d.strftime("%d/%m"),
             "total": counts.get(d, {}).get("total", 0),
             "violation": counts.get(d, {}).get("violation", 0)} for d in days]


def _calendar(month, source="") -> dict:
    # The calendar of `month` (its first day): motorcycles and violations of each of its days up to today.
    today = timezone.localdate()
    last = min(month.replace(day=calendar.monthrange(month.year, month.month)[1]), today)
    return {"month": month.strftime("%Y-%m"), "this_month": today.strftime("%Y-%m"),
            "days": _per_day(month, last, source)}


def _stats_query(request) -> dict:
    query = StatsQuerySerializer(data=request.query_params)
    query.is_valid(raise_exception=True)
    return query.validated_data


class StatsView(APIView):
    def get(self, request):
        # Violation statistics for the violations page. The last 7 days always end today; the rest covers
        # the chosen date range: totals, violations by type and per day. The calendar shows the chosen month.
        # All of it counts only the records of the chosen video source, if one is chosen.
        query = _stats_query(request)
        first, last, source = query["start"], query["end"], query["source"]
        today = timezone.localdate()
        # Every total in one pass over the records (the page asks again every few seconds)
        violation = Q(is_violation=True)
        counts = _in_days(first, last, source).aggregate(
            total=Count("id"),
            violation=Count("id", filter=violation),
            pending_review=Count("id", filter=violation & Q(review=Review.PENDING)),
            confirmed=Count("id", filter=violation & Q(review=Review.CONFIRMED)),
            **{v.value: Count("id", filter=violation & Q(violations__contains=v.value)) for v in Violation},
        )
        return Response({
            "start": first.isoformat(),
            "end": last.isoformat(),
            "days": (last - first).days + 1,
            "last_week": _per_day(today - timedelta(days=6), today, source),
            "violation": counts["violation"],
            "total": counts["total"],
            "pending_review": counts["pending_review"],
            "confirmed": counts["confirmed"],
            "by_type": [{"label": v.label, "count": counts[v.value]} for v in Violation],
            "per_day": _per_day(first, last, source),
            "calendar": _calendar(query["month"], source),
        })


class RepeatOffendersView(APIView):
    def get(self, request):
        # Plates with more than one violation in the chosen date range and source, most violations first.
        # Plates OCR read slightly differently count as one motorcycle (plate_search.group_plates); rejected
        # events are left out.
        query = _stats_query(request)
        violations = (_in_days(query["start"], query["end"], query["source"]).filter(is_violation=True)
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
    # An evidence photo: the motorcycle (field "snapshot") or its licence plate ("plate_snapshot").
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
