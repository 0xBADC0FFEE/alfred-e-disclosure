"""Pure relative-time formatter in Russian."""
from datetime import datetime


def format(now: datetime, then: datetime) -> str:
    if then > now:
        return "только что"
    diff = now - then
    secs = int(diff.total_seconds())

    if secs < 60:
        return "только что"
    if secs < 3600:
        return f"{secs // 60} мин назад"
    if secs < 86400:
        return f"{secs // 3600} ч назад"

    # Calendar-day diff: "вчера" only if exactly previous calendar day.
    day_diff = (now.date() - then.date()).days
    if day_diff == 1:
        return "вчера"
    return f"{day_diff} дн назад"
