"""Centralized API usage tracking, rate limiting, and response caching for MiniGPT."""

import os
import json
import time
import hashlib
from datetime import datetime, timezone
from threading import Lock

USAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_usage.json")


class ResponseCache:
    """Thread-safe in-memory cache with TTL support."""

    def __init__(self):
        self._cache = {}
        self._lock = Lock()

    def get(self, key: str):
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            data, expire_at = entry
            if time.time() > expire_at:
                del self._cache[key]
                return None
            return data

    def set(self, key: str, data, ttl_seconds: int = 3600):
        with self._lock:
            expire_at = time.time() + ttl_seconds
            self._cache[key] = (data, expire_at)

    def clear(self):
        with self._lock:
            self._cache.clear()

    @staticmethod
    def hash_key(*parts) -> str:
        h = hashlib.sha256()
        for p in parts:
            if isinstance(p, (dict, list, tuple)):
                h.update(json.dumps(p, sort_keys=True).encode("utf-8"))
            else:
                h.update(str(p).encode("utf-8"))
        return h.hexdigest()


class UsageTracker:
    """Thread-safe API usage tracker with persistent daily storage and threshold monitoring."""

    def __init__(self, usage_file: str = USAGE_FILE):
        self._file = usage_file
        self._lock = Lock()
        self._data = self._load()

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _get_limit(self, service: str) -> int:
        if service == "gemini":
            return int(os.getenv("GEMINI_DAILY_LIMIT", "1500"))
        if service == "youtube":
            return int(os.getenv("YOUTUBE_DAILY_LIMIT", "10000"))
        return int(os.getenv(f"{service.upper()}_DAILY_LIMIT", "1000"))

    def _get_thresholds(self) -> tuple[float, float]:
        warning = float(os.getenv("API_WARNING_THRESHOLD", "80"))
        critical = float(os.getenv("API_CRITICAL_THRESHOLD", "90"))
        return warning, critical

    def _load(self) -> dict:
        today = self._today()
        default_data = {
            "date": today,
            "services": {
                "gemini": {"count": 0, "endpoints": {}},
                "youtube": {"count": 0, "endpoints": {}}
            },
            "history": []
        }
        if os.path.exists(self._file):
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if loaded.get("date") == today:
                        loaded.setdefault("services", {})
                        loaded["services"].setdefault("gemini", {"count": 0, "endpoints": {}})
                        loaded["services"].setdefault("youtube", {"count": 0, "endpoints": {}})
                        loaded.setdefault("history", [])
                        return loaded
            except Exception:
                pass
        return default_data

    def _save(self):
        try:
            temp_file = f"{self._file}.tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(temp_file, self._file)
        except Exception:
            pass

    def _check_rollover(self):
        """Rollover counts if the day has changed."""
        today = self._today()
        if self._data.get("date") != today:
            self._data = {
                "date": today,
                "services": {
                    "gemini": {"count": 0, "endpoints": {}},
                    "youtube": {"count": 0, "endpoints": {}}
                },
                "history": []
            }
            self._save()

    def is_limit_reached(self, service: str, count: int = 1) -> bool:
        """Check if an API call would exceed the configured limit."""
        with self._lock:
            self._check_rollover()
            limit = self._get_limit(service)
            svc_data = self._data["services"].setdefault(service, {"count": 0, "endpoints": {}})
            return (svc_data["count"] + count) > limit

    def record_usage(self, service: str, endpoint: str = "default", count: int = 1):
        """Record an actual external API request."""
        with self._lock:
            self._check_rollover()
            svc_data = self._data["services"].setdefault(service, {"count": 0, "endpoints": {}})
            svc_data["count"] += count
            svc_data["endpoints"][endpoint] = svc_data["endpoints"].get(endpoint, 0) + count

            self._data["history"].append({
                "service": service,
                "endpoint": endpoint,
                "count": count,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            if len(self._data["history"]) > 50:
                self._data["history"] = self._data["history"][-50:]

            self._save()

    def get_limit_reached_response(self, service: str) -> dict:
        """Standardized backend limit-reached response."""
        with self._lock:
            self._check_rollover()
            svc_data = self._data["services"].setdefault(service, {"count": 0, "endpoints": {}})
            used = svc_data["count"]
            limit = self._get_limit(service)
        return {
            "error": "API usage limit reached",
            "service": service,
            "used": used,
            "limit": limit
        }

    def get_usage_summary(self) -> dict:
        """Dynamic summary of all monitored APIs with percentages, limits, and status."""
        with self._lock:
            self._check_rollover()
            warning_th, critical_th = self._get_thresholds()
            summary = {}
            for service in ["gemini", "youtube"]:
                limit = self._get_limit(service)
                svc_data = self._data["services"].setdefault(service, {"count": 0, "endpoints": {}})
                used = svc_data["count"]
                remaining = max(0, limit - used)
                pct = round((used / limit * 100), 2) if limit > 0 else 0.0

                if used >= limit:
                    status = "exhausted"
                elif pct >= critical_th:
                    status = "critical"
                elif pct >= warning_th:
                    status = "warning"
                else:
                    status = "normal"

                summary[service] = {
                    "used": used,
                    "limit": limit,
                    "remaining": remaining,
                    "percentage": pct,
                    "status": status,
                    "thresholds": {
                        "warning": warning_th,
                        "critical": critical_th
                    }
                }
            return summary


# Global singleton instances for the application
api_cache = ResponseCache()
api_tracker = UsageTracker()
