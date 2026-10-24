"""Orologio del centralino: UTC, iniettabile dai test e dal banco di prova."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

_orologio: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


def now() -> datetime:
    return _orologio()


def imposta(fn: Callable[[], datetime] | None) -> None:
    """Sostituisce l'orologio (None = orologio vero)."""
    global _orologio
    _orologio = fn or (lambda: datetime.now(timezone.utc))


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_now() -> str:
    return iso(now())


def parse_iso(s: str | None) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def age_s(s: str | None) -> float | None:
    dt = parse_iso(s)
    return None if dt is None else (now() - dt).total_seconds()
