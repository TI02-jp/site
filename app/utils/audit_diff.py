"""Helpers for building compact audit snapshots and field-level diffs."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any


def normalize_audit_value(value: Any) -> Any:
    """Convert values to JSON-friendly primitives for audit payloads."""

    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [normalize_audit_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): normalize_audit_value(val)
            for key, val in value.items()
        }
    return value


def build_field_diff(
    old_values: dict[str, Any] | None,
    new_values: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return only changed fields between two snapshots."""

    old_map = old_values or {}
    new_map = new_values or {}
    changed_old: dict[str, Any] = {}
    changed_new: dict[str, Any] = {}

    all_keys = set(old_map.keys()) | set(new_map.keys())
    for key in all_keys:
        old_val = normalize_audit_value(old_map.get(key))
        new_val = normalize_audit_value(new_map.get(key))
        if old_val == new_val:
            continue
        changed_old[key] = old_val
        changed_new[key] = new_val

    return changed_old, changed_new
