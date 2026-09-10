"""Gateway retry hints are minimum delays, not suggestions to retry immediately."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math

TRANSIENT_HTTP = {408, 409, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


def retry_delay(status, headers, body, attempt, now=None):
    values = []
    def number(value):
        if isinstance(value, bool):
            return
        try:
            seconds = float(value)
            if math.isfinite(seconds) and seconds >= 0:
                values.append(seconds)
        except (TypeError, ValueError):
            pass
    for key, value in (headers or {}).items():
        if key.lower() != 'retry-after':
            continue
        number(value)
        try:
            date = parsedate_to_datetime(str(value))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            number((date - (now or datetime.now(timezone.utc))).total_seconds())
        except (ValueError, TypeError, OverflowError):
            pass
    if isinstance(body, dict):
        number(body.get('retry_after'))
        if isinstance(body.get('error'), dict):
            number(body['error'].get('retry_after'))
    baseline = min(2 ** attempt, 8)
    if status in {502, 503, 504, 522, 524}:
        baseline = max(30, baseline)
    return max([baseline, *values])
