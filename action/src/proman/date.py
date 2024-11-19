import datetime as _dt


OUTPUT_FORMAT = "%Y-%m-%d"


def from_github_to_string(date: str) -> str:
    return to_string(from_github(date))


def now() -> _dt.datetime:
    return _dt.datetime.now(tz=_dt.UTC)


def from_github(date: str) -> _dt.datetime:
    return _dt.datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ").astimezone(_dt.UTC)


def to_string(date: _dt.datetime) -> str:
    return date.astimezone(_dt.UTC).strftime(OUTPUT_FORMAT)

