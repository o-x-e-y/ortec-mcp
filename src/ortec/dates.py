import datetime as dt
import re
from zoneinfo import ZoneInfo

from .errors import OrtecError

TZ = ZoneInfo("Europe/Amsterdam")

_MOMENT = re.compile(r"^(?P<date>\S+?)(?:[ T@](?P<time>\d{1,2}:\d{2}))?$")


def today() -> dt.date:
    return dt.datetime.now(TZ).date()


def tomorrow() -> dt.date:
    return today() + dt.timedelta(days=1)


def parse_date(value: str, *, now: dt.date | None = None) -> dt.date:
    """`YYYY-MM-DD`, `MM-DD`, `DD`, `today`/`vandaag`, `tomorrow`/`morgen` or `+N`.
    A short date is the next such date, today included."""
    now = now or today()
    value = value.strip().lower()
    try:
        return _date_from(value, now)
    except ValueError:
        pass
    message = (f"bad date {value!r}: expected YYYY-MM-DD, MM-DD, DD, today, tomorrow or +N")
    try:
        message += f"; did you mean {_date_from('-'.join(reversed(value.split('-'))), now)}?"
    except ValueError:
        pass
    raise OrtecError(message)


def _date_from(value: str, now: dt.date) -> dt.date:
    if value in ("today", "vandaag"):
        return now
    if value in ("tomorrow", "morgen"):
        return now + dt.timedelta(days=1)
    if value.startswith("+") and value[1:].isdigit():
        return now + dt.timedelta(days=int(value[1:]))
    if m := re.fullmatch(r"(\d{1,2})", value):
        date = now.replace(day=int(m[1]))
        if date < now:
            year, month = divmod(now.year * 12 + now.month, 12)
            date = dt.date(year, month + 1, int(m[1]))
        return date
    if m := re.fullmatch(r"(\d{1,2})-(\d{1,2})", value):
        date = dt.date(now.year, int(m[1]), int(m[2]))
        return date if date >= now else date.replace(year=now.year + 1)
    return dt.date.fromisoformat(value)


def parse_time(value: str) -> dt.time:
    try:
        hour, minute = value.split(":")
        return dt.time(int(hour), int(minute))
    except ValueError:
        raise OrtecError(f"bad time {value!r}: expected HH:MM") from None


def parse_moment(value: str, *, now: dt.date | None = None) -> tuple[dt.date, dt.time | None]:
    """`DATE` or `DATE HH:MM` (also `DATETHH:MM`, `DATE@HH:MM`)."""
    match = _MOMENT.match(value.strip())
    if not match:
        raise OrtecError(f"bad date/time {value!r}: expected DATE or 'DATE HH:MM'")
    time = parse_time(match["time"]) if match["time"] else None
    return parse_date(match["date"], now=now), time


def at(day: dt.date, time: dt.time = dt.time()) -> dt.datetime:
    return dt.datetime.combine(day, time, tzinfo=TZ)


def local(iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso).astimezone(TZ)


def span(start: str | None, end: str | None, days: int, *,
         first: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """No dates: `first` (today by default) and the `days` days after it. One
    date: that day."""
    if start is None:
        first = first or today()
        return first, first + dt.timedelta(days=days)
    first = parse_date(start)
    return first, parse_date(end) if end else first
