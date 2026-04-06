"""
service_defaults.py - Per-service recurring interval defaults

Used by mark_paid() to auto-set next_service_due_at when not explicitly provided.
"""

# Maps common service type keywords (lowercase) to recurring interval in days
SERVICE_INTERVALS: dict[str, int] = {
    "lawn care": 14,
    "lawn mowing": 14,
    "mowing": 14,
    "gutters": 180,
    "gutter cleaning": 180,
    "gutter guard": 365,
    "pressure washing": 365,
    "power washing": 365,
    "window cleaning": 90,
    "window washing": 90,
    "landscaping": 30,
    "pest control": 90,
    "hvac": 180,
    "air filter": 90,
    "roofing": 1825,  # 5 years
    "roof inspection": 365,
    "house cleaning": 14,
    "cleaning": 30,
    "pool cleaning": 14,
    "pool service": 14,
    "tree trimming": 365,
    "tree service": 365,
    "snow removal": 7,
    "painting": 1825,  # 5 years
    "deck staining": 730,  # 2 years
    "concrete": 3650,  # 10 years
    "fencing": 1825,  # 5 years
    "plumbing": 365,
    "electrical": 365,
    "carpet cleaning": 180,
}

DEFAULT_SERVICE_INTERVAL = 30  # fallback if service type is not found


def get_service_interval(service_type: str) -> int:
    """
    Return the recurring interval (days) for a service type.
    Case-insensitive lookup against SERVICE_INTERVALS.
    Returns DEFAULT_SERVICE_INTERVAL if not found.
    """
    if not service_type:
        return DEFAULT_SERVICE_INTERVAL
    lower = service_type.lower().strip()
    # Exact match first
    if lower in SERVICE_INTERVALS:
        return SERVICE_INTERVALS[lower]
    # Substring match (service type might be more specific)
    for key, days in SERVICE_INTERVALS.items():
        if key in lower or lower in key:
            return days
    return DEFAULT_SERVICE_INTERVAL
