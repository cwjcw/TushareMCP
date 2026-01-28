from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RateLimitConfig:
    max_rows: int | None
    min_interval_seconds: float
    source: str


def _parse_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _load_limits(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def resolve_rate_limits(
    *,
    max_rows_env: str | None,
    min_interval_env: str | None,
    points_env: str | None,
    limits_path: str | Path | None,
    default_max_rows: int,
    default_min_interval_seconds: float,
) -> RateLimitConfig:
    max_rows = _parse_int(max_rows_env)
    min_interval = _parse_float(min_interval_env)
    if max_rows is not None and min_interval is not None:
        return RateLimitConfig(max_rows=max_rows, min_interval_seconds=min_interval, source="env:explicit")

    limits = _load_limits(limits_path)
    points = _parse_int(points_env)
    if limits and points is not None:
        tiers = limits.get("tiers") or []
        selected = None
        for tier in sorted(tiers, key=lambda t: int(t.get("min_points", 0))):
            try:
                min_points = int(tier.get("min_points", 0))
            except Exception:
                continue
            if points >= min_points:
                selected = tier
        if selected:
            max_rows_value = selected.get("max_rows", default_max_rows)
            max_rows_value = None if max_rows_value is None else int(max_rows_value)
            return RateLimitConfig(
                max_rows=max_rows_value,
                min_interval_seconds=float(selected.get("min_interval_seconds", default_min_interval_seconds)),
                source=f"limits:{limits_path}",
            )

        default = limits.get("default") or {}
        if default:
            max_rows_value = default.get("max_rows", default_max_rows)
            max_rows_value = None if max_rows_value is None else int(max_rows_value)
            return RateLimitConfig(
                max_rows=max_rows_value,
                min_interval_seconds=float(default.get("min_interval_seconds", default_min_interval_seconds)),
                source=f"limits:{limits_path}",
            )

    # Partial env overrides
    if max_rows is not None or min_interval is not None:
        return RateLimitConfig(
            max_rows=max_rows if max_rows is not None else default_max_rows,
            min_interval_seconds=min_interval if min_interval is not None else default_min_interval_seconds,
            source="env:partial",
        )

    return RateLimitConfig(
        max_rows=default_max_rows,
        min_interval_seconds=default_min_interval_seconds,
        source="default",
    )
