from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class RateLimiter:
    min_interval_seconds: float = 0.35
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _last_ts: float = 0.0

    def wait(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        with self._lock:
            now = time.time()
            sleep_s = self.min_interval_seconds - (now - self._last_ts)
            if sleep_s > 0:
                time.sleep(sleep_s)
            self._last_ts = time.time()


class TushareClient:
    def __init__(self, *, token: str | None, min_interval_seconds: float = 0.35):
        self._token = token
        self._rate_limiter = RateLimiter(min_interval_seconds=min_interval_seconds)
        self._pro = None

    @staticmethod
    def from_env(*, min_interval_seconds: float = 0.35) -> "TushareClient":
        token = os.environ.get("TUSHARE_TOKEN") or os.environ.get("TS_TOKEN")
        return TushareClient(token=token, min_interval_seconds=min_interval_seconds)

    def _get_pro(self):
        if self._pro is not None:
            return self._pro
        if not self._token:
            raise RuntimeError("Missing Tushare token. Set env `TUSHARE_TOKEN` (or `TS_TOKEN`).")
        try:
            import tushare as ts
        except Exception as e:  # pragma: no cover
            raise RuntimeError(f"Failed to import `tushare`: {e}") from e
        self._pro = ts.pro_api(self._token)
        return self._pro

    def get_api(self, api_name: str) -> Callable[..., Any]:
        pro = self._get_pro()
        fn = getattr(pro, api_name, None)
        if fn is None or not callable(fn):
            raise AttributeError(f"Unknown Tushare API: {api_name}")
        return fn

    def call(self, api_name: str, params: dict[str, Any]) -> Any:
        self._rate_limiter.wait()
        fn = self.get_api(api_name)
        return fn(**params)
