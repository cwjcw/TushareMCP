from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass(frozen=True)
class SpecStore:
    apis: dict[str, dict[str, Any]]
    meta: dict[str, Any]

    @staticmethod
    def empty() -> "SpecStore":
        return SpecStore(apis={}, meta={"loaded_at": _now_iso(), "source": "empty"})

    @staticmethod
    def load(path: str | Path | None) -> "SpecStore":
        if not path:
            return SpecStore.empty()
        path = Path(path)
        if not path.exists():
            return SpecStore.empty()
        raw = json.loads(path.read_text(encoding="utf-8"))
        apis = raw.get("apis") or {}
        meta = raw.get("meta") or {}
        meta = {**meta, "loaded_at": _now_iso(), "spec_path": str(path)}
        return SpecStore(apis=apis, meta=meta)

    def get(self, api_name: str) -> dict[str, Any] | None:
        return self.apis.get(api_name)

    def search(self, keyword: str, *, limit: int = 10) -> list[dict[str, Any]]:
        kw = _normalize(keyword)
        if not kw:
            return []

        results: list[tuple[int, dict[str, Any]]] = []
        for api_name, spec in self.apis.items():
            title = str(spec.get("title") or "")
            desc = str(spec.get("description") or "")
            aliases = spec.get("aliases") or []
            haystack = " ".join(
                [
                    api_name,
                    title,
                    desc,
                    " ".join(map(str, aliases)),
                    " ".join((spec.get("input") or {}).get("required") or []),
                    " ".join(((spec.get("input") or {}).get("properties") or {}).keys()),
                    " ".join((f.get("name", "") for f in (spec.get("output") or {}).get("fields") or [])),
                ]
            )
            h = _normalize(haystack)
            if kw not in h:
                continue

            score = 1
            if kw in _normalize(api_name):
                score += 10
            if kw in _normalize(title):
                score += 5
            if kw in _normalize(desc):
                score += 3
            results.append((score, spec))

        results.sort(key=lambda x: x[0], reverse=True)
        return [spec for _, spec in results[: max(1, limit)]]

    @staticmethod
    def validate_params(spec: dict[str, Any] | None, params: dict[str, Any]) -> dict[str, Any]:
        if not spec:
            return {"ok": True, "missing_required": [], "unknown_params": []}

        input_schema = spec.get("input") or {}
        required = set(input_schema.get("required") or [])
        properties = set((input_schema.get("properties") or {}).keys())

        missing_required = sorted([k for k in required if k not in params or params.get(k) in (None, "")])
        unknown_params = sorted([k for k in params.keys() if properties and k not in properties])

        ok = not missing_required
        return {"ok": ok, "missing_required": missing_required, "unknown_params": unknown_params}

