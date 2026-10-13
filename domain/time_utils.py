"""Strict helpers for comparing persisted ISO-8601 instants."""

from __future__ import annotations

from datetime import datetime, timezone

from domain.errors import ValidationError


def parse_instant(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValidationError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"{field} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def is_after(left: str, right: str, *, left_field: str, right_field: str) -> bool:
    return parse_instant(left, left_field) > parse_instant(right, right_field)


def canonical_instant(value: str, field: str) -> str:
    return parse_instant(value, field).isoformat()
