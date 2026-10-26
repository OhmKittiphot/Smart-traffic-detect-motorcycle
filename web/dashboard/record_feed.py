"""Wakes the pages' event streams (views/events.py) when a record is saved, so they wait here instead of
asking the database every second. Records are saved in this process (recorder.py), so a condition is enough."""
import threading

_saved = threading.Condition()
_count = 0   # records saved since the server started


def record_saved(sender, instance, created, **kwargs):
    """post_save of VehicleRecord (connected in apps.py)."""
    global _count
    if created:
        with _saved:
            _count += 1
            _saved.notify_all()


def saved_count() -> int:
    return _count


def wait_for_record(seen: int, timeout: float) -> bool:
    """Block until a record is saved after saved_count() was `seen`: True, or False after `timeout` seconds
    without one."""
    with _saved:
        return _saved.wait_for(lambda: _count > seen, timeout)
