"""Completed years from a received birth date and an explicit reference date.

Missing, invalid and future birth dates stay unknown. A February 29 birthday
advances on March 1 in a non-leap year. Reported feed ages are never a fallback.
"""
from datetime import date, datetime

VERSION = 'birth-date-age/1'


def age_at(birth_date, as_of):
    def day(value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(value)
    try:
        born, cutoff = day(birth_date), day(as_of)
        if born > cutoff:
            return None
        return cutoff.year - born.year - ((cutoff.month, cutoff.day) < (born.month, born.day))
    except (TypeError, ValueError):
        return None


def register(db):
    """Register the very same implementation on a governed SQLite connection."""
    db.create_function('age_at', 2, age_at, deterministic=True)
    return db
