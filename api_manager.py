"""Centralized API usage tracking, rate limiting, security validation, and response caching for MiniGPT."""

import os
import re
import json
import time
import hashlib
from datetime import datetime, timezone
from threading import Lock

USAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_usage.json")

# Standard YouTube 11-char base64url-like ID regex
YOUTUBE_VIDEO_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{11}$")
# Google API key pattern (e.g. AIzaSy...)
GOOGLE_API_KEY_PATTERN = re.compile(r"AIza[0-9A-Za-z\-_]{20,50}")
PARAM_KEY_PATTERN = re.compile(r"([?&]key=)[^&\s'\"]+", re.IGNORECASE)
HEADER_KEY_PATTERN = re.compile(r"(x-goog-api-key:\s*)[^\s,'\"]+", re.IGNORECASE)


def redact_secrets(text: str) -> str:
    """Mask known API keys, tokens, query parameters, and Google API key patterns from error messages or logs."""
    if not isinstance(text, str):
        text = str(text)
    for env_var in ("GEMINI_API_KEY", "YOUTUBE_API_KEY", "SECRET_KEY"):
        val = os.getenv(env_var)
        if val and len(val) >= 4:
            text = text.replace(val, "[REDACTED_API_KEY]")
    # Mask potential Google API key patterns and query param values
    text = GOOGLE_API_KEY_PATTERN.sub("[REDACTED_API_KEY]", text)
    text = PARAM_KEY_PATTERN.sub(r"\1[REDACTED_API_KEY]", text)
    text = HEADER_KEY_PATTERN.sub(r"\1[REDACTED_API_KEY]", text)
    return text


def sanitize_text(text: str, max_length: int = 4000) -> str:
    """Strip dangerous control characters, null bytes, and bound string length."""
    if not isinstance(text, str):
        return ""
    # Remove null bytes and non-printable control characters except standard whitespace
    cleaned = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)
    return cleaned.strip()[:max_length]


def is_valid_video_id(video_id: str) -> bool:
    """Check if the string is a valid YouTube video ID."""
    if not isinstance(video_id, str):
        return False
    return bool(YOUTUBE_VIDEO_ID_PATTERN.match(video_id.strip()))


class IPRateLimiter:
    """Thread-safe sliding-window rate limiter per client IP and endpoint category."""

    def __init__(self):
        self._requests = {}
        self._lock = Lock()
        self._last_cleanup = time.time()

    def is_allowed(self, client_ip: str, endpoint: str, max_requests: int = 30, window_seconds: int = 60) -> tuple[bool, int]:
        """Check if request from client_ip is allowed under (max_requests / window_seconds).
        Returns: (allowed: bool, retry_after_seconds: int)
        """
        now = time.time()
        ip_safe = client_ip or "127.0.0.1"
        key = f"{ip_safe}:{endpoint}"

        with self._lock:
            # Periodic cleanup of expired records every 5 minutes
            if now - self._last_cleanup > 300:
                self._cleanup(now)
                self._last_cleanup = now

            timestamps = self._requests.get(key, [])
            cutoff = now - window_seconds
            valid_timestamps = [t for t in timestamps if t > cutoff]

            if len(valid_timestamps) >= max_requests:
                oldest = valid_timestamps[0]
                retry_after = max(1, int(oldest + window_seconds - now))
                self._requests[key] = valid_timestamps
                return False, retry_after

            valid_timestamps.append(now)
            self._requests[key] = valid_timestamps
            return True, 0

    def _cleanup(self, now: float):
        cutoff = now - 3600
        keys_to_del = []
        for k, timestamps in self._requests.items():
            fresh = [t for t in timestamps if t > cutoff]
            if not fresh:
                keys_to_del.append(k)
            else:
                self._requests[k] = fresh
        for k in keys_to_del:
            del self._requests[k]


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
rate_limiter = IPRateLimiter()
