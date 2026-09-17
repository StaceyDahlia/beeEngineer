"""Pure helpers for future planner integration; no external requests."""
import math
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def configured_timezone(name):
    """Use the IANA zone when available; keep the dataset portable on Windows/PyPy."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == 'Europe/Moscow':
            return timezone(timedelta(hours=3), name='Europe/Moscow')
        raise


def consume_equipment(inventory, required):
    """Return new inventory; on any shortage raise without mutating the original."""
    if any(type(v) is not int or v < 0 for v in (*inventory.values(), *required.values())):
        raise ValueError('Inventory and requirements must be nonnegative integer quantities')
    missing = {k: v-inventory.get(k, 0) for k, v in required.items() if v > inventory.get(k, 0)}
    if missing:
        raise ValueError(f'Equipment shortage: {missing}')
    return {k: v-required.get(k, 0) for k, v in inventory.items()}


def travel_minutes(base_minutes, departure, mode, config, profile=None, base_includes_traffic=False):
    """Integrate hourly speed changes, avoiding arrival-time jumps at hour boundaries."""
    if not math.isfinite(base_minutes) or base_minutes < 0 or departure.utcoffset() is None:
        raise ValueError('Expected finite nonnegative time and timezone-aware departure')
    if mode not in config['road_modes'] + config['unaffected_modes']:
        raise ValueError('Unknown/mixed mode; split transit into explicit legs')
    if mode in config['unaffected_modes'] or base_includes_traffic:
        return base_minutes
    schedule = config['profiles'][profile or config['default_profile']]
    for values in schedule.values():
        if len(values) != 24 or any(not math.isfinite(v) or v < 1 for v in values):
            raise ValueError('Expected 24 finite coefficients >= 1')
    cursor = departure.astimezone(configured_timezone(config['timezone']))
    remaining, elapsed = base_minutes, 0.0
    while remaining > 1e-9:
        factor = schedule['weekday' if cursor.weekday() < 5 else 'weekend'][cursor.hour]
        boundary = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        available = (boundary-cursor).total_seconds()/60
        used = min(available, remaining*factor)
        remaining -= used/factor
        elapsed += used
        cursor += timedelta(minutes=used)
    return elapsed
