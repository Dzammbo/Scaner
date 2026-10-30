"""Distinguish a newly computed report from fresh acquisition."""
import datetime as dt


def acquisition_health(receipts, now, maximum_age_seconds=900):
    timestamps = []
    failed = 0
    for row in receipts:
        if row.get('failed'):
            failed += 1
            continue
        stamp = (row.get('request') or {}).get('requested_at_utc')
        if not stamp:
            continue
        try:
            when = dt.datetime.fromisoformat(stamp.replace('Z','+00:00'))
            if when.tzinfo is not None and when <= now:
                timestamps.append(when)
        except (TypeError, ValueError):
            continue
    last = max(timestamps) if timestamps else None
    age = max(0,(now-last).total_seconds()) if last else None
    return dict(status='fresh' if age is not None and age <= maximum_age_seconds else 'stale',
                last_successful_request_at_utc=last.isoformat() if last else None,
                age_seconds=round(age) if age is not None else None,
                maximum_age_seconds=maximum_age_seconds,
                successful_receipts=len(timestamps), failed_receipts=failed)
