"""The system status page (Admin): detection, cameras, video files, storage and records.
Detection is only checked from here, never started."""
from datetime import datetime

from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import calibrations, storage, system_health
from ..engine import current_service, engine_health
from ..models import Review, SpeedCalibration, VehicleRecord


def _local_time(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.get_current_timezone())


class CameraStatusView(APIView):
    def get(self, request):
        """Admin: only whether each live camera answers, for the camera notifications (notify.js).
        Uses the same check as the system status page, so it is cheap to ask often."""
        from app_config import AppConfig

        cameras, checked_at = system_health.cameras(AppConfig.from_env().rtsp_url)
        return Response({
            "cameras": [{"key": c["key"], "label": c["label"], "state": c["state"], "detail": c["detail"]}
                        for c in cameras],
            "checked_at": _local_time(checked_at),
        })


class SystemStatusView(APIView):
    def get(self, request):
        """Admin: detection engine, cameras, video files and stored records (polled every few seconds)."""
        from app_config import AppConfig

        config = AppConfig.from_env()
        service = current_service()
        running = service is not None and service.state == service.RUNNING
        in_use = service.source.key if running else None   # the source is only known while detection runs
        saved = {c.source_key: c for c in SpeedCalibration.objects.all()}

        def source_info(key):
            # Calibrated when one was saved for it; the IP camera may also use the one in .env (not editable here)
            calibration = calibrations.to_dict(saved.get(key))
            from_env = calibrations.uses_env_fallback(key, saved.get(key), config)
            return {"source_key": key, "in_use": key == in_use, "speed_calibrated": bool(calibration) or from_env,
                    "calibration": calibration, "calibration_from_env": from_env}

        cameras, checked_at = system_health.cameras(config.rtsp_url)
        cameras = [{**c, **source_info(f"live:{c['key'] or 'ip'}")} for c in cameras]
        files = [{**f, **source_info(f"file:{f['name']}"), "modified": _local_time(f["modified"])}
                 for f in system_health.video_files()]

        today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        records = VehicleRecord.objects.all()
        return Response({
            "engine": {"health": engine_health(), "status": service.status() if running else None},
            "cameras": cameras,
            "cameras_checked_at": _local_time(checked_at),
            "video_files": files,
            "storage": storage.usage(),
            "records": {
                "total": records.count(),
                "today": records.filter(detected_at__gte=today).count(),
                "violations_today": records.filter(detected_at__gte=today, is_violation=True).count(),
                "pending_review": records.filter(is_violation=True, review=Review.PENDING).count(),
                "last_detected_at": records.order_by("-detected_at").values_list("detected_at", flat=True).first(),
            },
        })
