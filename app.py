"""MiniGPT Flask backend with Gemini AI, YouTube player, and comprehensive security hardening."""

import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory, abort

from api_manager import (
    api_cache,
    api_tracker,
    rate_limiter,
    redact_secrets,
    sanitize_text,
    is_valid_video_id,
)

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=None)
# Prevent large payload DoS attacks by capping maximum request body to 1MB
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024

# Allowed static web files that are safe for clients to view
ALLOWED_STATIC_EXTENSIONS = {
    ".html",
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".ico",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
}

# Explicitly forbidden filenames regardless of extension
BLOCKED_EXACT_FILES = {
    ".env",
    ".env.example",
    "api_usage.json",
    "api_usage.json.tmp",
    "requirements.txt",
    "app.py",
    "api_manager.py",
}

MODEL_INFO = {
    "name": os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
    "params": "hosted",
    "vocab_size": "n/a",
    "context_len": "n/a",
    "device": "api",
}


# ---------------------------------------------------------------------------
# Security Middleware & HTTP Response Headers
# ---------------------------------------------------------------------------

def get_client_ip() -> str:
    """Safely extract the client IP address."""
    # When behind a trusted proxy, X-Forwarded-For could be used, but default to remote_addr
    return request.headers.get("X-Forwarded-For", request.remote_addr or "127.0.0.1").split(",")[0].strip()


def check_csrf_origin() -> bool:
    """Verify Origin / Referer for mutating requests (POST) to prevent CSRF."""
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if not origin:
        # Same-origin requests without Origin/Referer (e.g. standard local fetch) are allowed
        return True
    parsed = urlparse(origin)
    host = parsed.netloc or parsed.path
    # Allow localhost, 127.0.0.1, and matching Host header
    req_host = request.headers.get("Host", "").split(":")[0]
    allowed_hosts = {"localhost", "127.0.0.1", "0.0.0.0", req_host}
    parsed_hostname = parsed.hostname or host.split(":")[0]
    return parsed_hostname in allowed_hosts


@app.after_request
def apply_security_headers(response):
    """Add defensive cybersecurity HTTP headers to all outgoing responses."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=(), payment=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://www.youtube.com https://s.ytimg.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https: blob:; "
        "media-src 'self' https: blob: data:; "
        "frame-src 'self' https://www.youtube.com https://www.youtube-nocookie.com; "
        "connect-src 'self' https://archive.org https://lrclib.net https://*.googleapis.com;"
    )
    return response


# ---------------------------------------------------------------------------
# Custom HTTP Error Handlers
# ---------------------------------------------------------------------------

@app.errorhandler(400)
def handle_bad_request(e):
    return jsonify({"error": "Bad Request", "details": redact_secrets(str(getattr(e, "description", e)))}), 400


@app.errorhandler(403)
def handle_forbidden(e):
    return jsonify({"error": "Forbidden"}), 403


@app.errorhandler(404)
def handle_not_found(e):
    return jsonify({"error": "Resource not found"}), 404


@app.errorhandler(405)
def handle_method_not_allowed(e):
    return jsonify({"error": "Method Not Allowed"}), 405


@app.errorhandler(413)
def handle_payload_too_large(e):
    return jsonify({"error": "Payload too large. Maximum allowed size is 1MB."}), 413


@app.errorhandler(429)
def handle_rate_limit(e):
    return jsonify({"error": "Too Many Requests. Please slow down."}), 429


@app.errorhandler(500)
def handle_server_error(e):
    return jsonify({"error": "Internal Server Error"}), 500


# ---------------------------------------------------------------------------
# Safe Static File Routing (Path Traversal & Secret Disclosure Protection)
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "shell.html")


@app.route("/<path:filename>")
def serve_static(filename: str):
    """Securely serve only whitelisted static files, blocking source code, dotfiles, and data."""
    # Clean filename and check for directory traversal attempts
    cleaned_name = os.path.normpath(filename).replace("\\", "/")
    if cleaned_name.startswith("../") or "/../" in cleaned_name or cleaned_name.startswith("/"):
        abort(404)

    # Strictly block dotfiles (.env, .git, etc.)
    base_name = os.path.basename(cleaned_name).lower()
    if base_name.startswith(".") or base_name in BLOCKED_EXACT_FILES:
        abort(404)

    # Check extension
    _, ext = os.path.splitext(base_name)
    if ext not in ALLOWED_STATIC_EXTENSIONS:
        abort(404)

    # Resolve safe path within BASE_DIR
    target_path = os.path.abspath(os.path.join(BASE_DIR, cleaned_name))
    if os.path.commonpath([BASE_DIR, target_path]) != BASE_DIR:
        abort(404)

    if not os.path.isfile(target_path):
        abort(404)

    rel_dir = os.path.relpath(os.path.dirname(target_path), BASE_DIR)
    dir_to_serve = os.path.join(BASE_DIR, rel_dir) if rel_dir != "." else BASE_DIR
    return send_from_directory(dir_to_serve, os.path.basename(target_path))


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.route("/api/stats", methods=["GET"])
def stats():
    return jsonify(MODEL_INFO)


@app.route("/api/usage", methods=["GET"])
def usage():
    return jsonify(api_tracker.get_usage_summary())


@app.route("/api/youtube/search", methods=["GET"])
def youtube_search():
    client_ip = get_client_ip()
    allowed, retry_after = rate_limiter.is_allowed(client_ip, "yt_search", max_requests=60, window_seconds=60)
    if not allowed:
        return jsonify({"error": "Rate limit exceeded. Try again shortly.", "retry_after": retry_after}), 429

    raw_query = request.args.get("q", "")
    query = sanitize_text(raw_query, max_length=120)
    if not query:
        return jsonify({"error": "Enter a song or artist to search YouTube."}), 400

    cache_key = f"yt_search:{query.lower()}"
    cached_videos = api_cache.get(cache_key)
    if cached_videos is not None:
        return jsonify({"videos": cached_videos, "cached": True})

    if api_tracker.is_limit_reached("youtube"):
        return jsonify(api_tracker.get_limit_reached_response("youtube")), 429

    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        return jsonify({
            "error": "YouTube search is not configured. Add YOUTUBE_API_KEY to the project's .env file and restart the app."
        }), 503

    params = urlencode({
        "part": "snippet",
        "q": query,
        "type": "video",
        "videoCategoryId": "10",  # Restrict to YouTube Music category
        "videoEmbeddable": "true",
        "maxResults": 15,
    })
    # Secure transmission: Send API key in X-Goog-Api-Key HTTP header
    http_request = Request(
        f"https://www.googleapis.com/youtube/v3/search?{params}",
        headers={
            "Accept": "application/json",
            "X-Goog-Api-Key": api_key,
        },
    )
    try:
        with urlopen(http_request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
            api_tracker.record_usage("youtube", endpoint="search", count=1)
    except TimeoutError:
        return jsonify({"error": "YouTube search timed out. Try again."}), 504
    except HTTPError as error:
        try:
            details = json.loads(error.read().decode("utf-8", errors="replace"))
            reason = details.get("error", {}).get("errors", [{}])[0].get("reason", "")
        except (json.JSONDecodeError, IndexError, AttributeError):
            reason = ""
        if error.code == 403 and reason in {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
            return jsonify({"error": "YouTube API quota reached. Try again later or check your Google Cloud quota."}), 429
        return jsonify({"error": f"YouTube search failed (HTTP {error.code}). Check the API key and YouTube Data API settings."}), 502
    except (URLError, json.JSONDecodeError) as error:
        return jsonify({"error": f"Could not reach YouTube search: {redact_secrets(str(error))}"}), 502

    videos = []
    for item in result.get("items", []):
        video_id = (item.get("id") or {}).get("videoId")
        snippet = item.get("snippet") or {}
        if not video_id or not is_valid_video_id(video_id):
            continue
        title = snippet.get("title", "Untitled video")
        if _is_non_music_title(title):
            continue
        thumbnails = snippet.get("thumbnails") or {}
        thumbnail = (thumbnails.get("medium") or thumbnails.get("default") or {}).get("url", "")
        videos.append({
            "videoId": video_id,
            "title": title,
            "channelTitle": snippet.get("channelTitle", "YouTube"),
            "thumbnail": thumbnail,
        })
    api_cache.set(cache_key, videos, ttl_seconds=3600)
    return jsonify({"videos": videos})


_TITLE_JUNK_RE = re.compile(
    r"\b(official\s*(music\s*)?video|official\s*audio|official|lyrics?|"
    r"lyric\s*video|audio|video|hd|hq|4k|remaster(ed)?|live|explicit|clean|"
    r"visualizer|full\s*version|mv)\b"
)

# Comprehensive filter for podcasts, interviews, reviews, reactions, vlogs, and non-music content
_NON_MUSIC_RE = re.compile(
    r"\b("
    r"podcast|full\s*podcast|"
    r"interview|full\s*interview|"
    r"episode\s*\d+|ep\s*\.?\s*\d+|full\s*episode|"
    r"reacts?|reacting|reaction\s*video|"
    r"album\s*review|song\s*review|track\s*review|music\s*review|"
    r"documentary|docuseries|"
    r"behind\s*the\s*scenes|making\s*of|"
    r"speaks\s*on|talks\s*about|"
    r"q&a|vlog|daily\s*vlog|"
    r"livestream|live\s*stream|stream\s*highlight|"
    r"tutorial|how\s*to\s*play|guitar\s*lesson|piano\s*lesson|drum\s*lesson|"
    r"unboxing|parody|audiobook"
    r")\b",
    re.IGNORECASE,
)


def _is_non_music_title(title: str) -> bool:
    """Detect if a title belongs to a podcast, interview, reaction, or non-song video."""
    if not title:
        return False
    return bool(_NON_MUSIC_RE.search(title))


def _normalize_song_title(title: str) -> str:
    """Normalize song title for comparison across video uploads."""
    if not title:
        return ""
    t = title.lower()
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"\[[^\]]*\]", " ", t)
    t = _TITLE_JUNK_RE.sub(" ", t)
    t = re.sub(r"\bft\.?\b|\bfeat\.?\b", " ", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _youtube_get(url: str, api_key: str, endpoint: str = "api", ttl: int = 3600):
    """GET a YouTube Data API URL securely with API key header, cache, and error masking."""
    cache_key = f"yt_url:{url}"
    cached = api_cache.get(cache_key)
    if cached is not None:
        return cached, None

    if api_tracker.is_limit_reached("youtube"):
        return None, (jsonify(api_tracker.get_limit_reached_response("youtube")), 429)

    http_request = Request(
        url,
        headers={
            "Accept": "application/json",
            "X-Goog-Api-Key": api_key,
        },
    )
    try:
        with urlopen(http_request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            api_tracker.record_usage("youtube", endpoint=endpoint, count=1)
            api_cache.set(cache_key, data, ttl_seconds=ttl)
            return data, None
    except TimeoutError:
        return None, (jsonify({"error": "YouTube request timed out. Try again."}), 504)
    except HTTPError as error:
        try:
            details = json.loads(error.read().decode("utf-8", errors="replace"))
            reason = details.get("error", {}).get("errors", [{}])[0].get("reason", "")
        except (json.JSONDecodeError, IndexError, AttributeError):
            reason = ""
        if error.code == 403 and reason in {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
            return None, (jsonify({"error": "YouTube API quota reached. Try again later or check your Google Cloud quota."}), 429)
        return None, (jsonify({"error": f"YouTube request failed (HTTP {error.code})."}), 502)
    except (URLError, json.JSONDecodeError) as error:
        return None, (jsonify({"error": f"Could not reach YouTube: {redact_secrets(str(error))}"}), 502)


def _youtube_search_videos(api_key: str, query: str, max_results: int = 10, category_music: bool = True):
    search_params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "videoEmbeddable": "true",
        "maxResults": max_results,
    }
    if category_music:
        search_params["videoCategoryId"] = "10"  # Music category
    params = urlencode(search_params)
    data, error = _youtube_get(
        f"https://www.googleapis.com/youtube/v3/search?{params}",
        api_key=api_key,
        endpoint="related_search",
        ttl=3600,
    )
    if error:
        return [], error
    videos = []
    for item in (data or {}).get("items", []):
        video_id = (item.get("id") or {}).get("videoId")
        snippet = item.get("snippet") or {}
        if not video_id or not is_valid_video_id(video_id):
            continue
        title = snippet.get("title", "Untitled video")
        if _is_non_music_title(title):
            continue
        thumbnails = snippet.get("thumbnails") or {}
        thumbnail = (thumbnails.get("medium") or thumbnails.get("default") or {}).get("url", "")
        videos.append({
            "videoId": video_id,
            "title": title,
            "channelTitle": snippet.get("channelTitle", "YouTube"),
            "thumbnail": thumbnail,
        })
    return videos, None


@app.route("/api/youtube/related", methods=["GET"])
def youtube_related():
    """Autoplay recommendation endpoint with strict ID validation and rate limiting."""
    client_ip = get_client_ip()
    allowed, retry_after = rate_limiter.is_allowed(client_ip, "yt_related", max_requests=60, window_seconds=60)
    if not allowed:
        return jsonify({"error": "Rate limit exceeded. Try again shortly.", "retry_after": retry_after}), 429

    raw_video_id = request.args.get("videoId", "")
    video_id = sanitize_text(raw_video_id, max_length=20)
    if not is_valid_video_id(video_id):
        return jsonify({"error": "A valid 11-character YouTube videoId is required."}), 400

    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        return jsonify({
            "error": "YouTube search is not configured. Add YOUTUBE_API_KEY to the project's .env file and restart the app."
        }), 503

    fallback_title = sanitize_text(request.args.get("title", ""), max_length=200)
    fallback_channel = sanitize_text(request.args.get("channelTitle", ""), max_length=100)

    # Validate exclude IDs
    exclude_ids = set()
    for vid in request.args.get("excludeIds", "").split(",")[:50]:
        v = vid.strip()
        if is_valid_video_id(v):
            exclude_ids.add(v)
    exclude_ids.add(video_id)

    exclude_titles = set()
    for t in request.args.get("excludeTitles", "").split("|")[:50]:
        cleaned_title = sanitize_text(t, max_length=200)
        if cleaned_title:
            exclude_titles.add(_normalize_song_title(cleaned_title))

    candidate_cache_key = f"yt_related_candidates:{video_id}"
    candidates = api_cache.get(candidate_cache_key)

    if candidates is None:
        details_params = urlencode({"part": "snippet", "id": video_id})
        details, error = _youtube_get(
            f"https://www.googleapis.com/youtube/v3/videos?{details_params}",
            api_key=api_key,
            endpoint="video_details",
            ttl=7200,
        )
        if error:
            return error
        items = (details or {}).get("items") or []
        snippet = (items[0].get("snippet") if items else {}) or {}
        title = snippet.get("title") or fallback_title
        channel_title = snippet.get("channelTitle") or fallback_channel
        tags = snippet.get("tags") or []

        exclude_titles.add(_normalize_song_title(title))

        queries = []
        clean_title = re.sub(r"[^\w\s]", " ", title).strip()
        title_words = [w for w in clean_title.split() if len(w) > 2]
        if title_words and channel_title:
            queries.append(f"{' '.join(title_words[:4])} {channel_title}")
        elif title_words:
            queries.append(f"{' '.join(title_words[:5])} song")
        if channel_title:
            queries.append(f"{channel_title} music")
        if tags:
            queries.append(" ".join(tags[:4]))
        if not queries:
            queries.append("popular music")

        seen_ids = set()
        candidates = []
        last_error = None
        for query in queries:
            videos, error = _youtube_search_videos(api_key, query, max_results=10)
            if error:
                last_error = error
                continue
            for video in videos:
                if video["videoId"] in seen_ids:
                    continue
                seen_ids.add(video["videoId"])
                candidates.append(video)
            if len(candidates) >= 24:
                break

        if not candidates:
            if last_error:
                return last_error
            return jsonify({"videos": [], "error": "No related videos were found."})

        api_cache.set(candidate_cache_key, candidates, ttl_seconds=3600)
    else:
        if fallback_title:
            exclude_titles.add(_normalize_song_title(fallback_title))

    def keep(video, avoid_duplicate_titles):
        if video["videoId"] in exclude_ids:
            return False
        if _is_non_music_title(video.get("title", "")):
            return False
        if avoid_duplicate_titles and _normalize_song_title(video["title"]) in exclude_titles:
            return False
        return True

    filtered = [v for v in candidates if keep(v, avoid_duplicate_titles=True)]
    if not filtered:
        filtered = [v for v in candidates if keep(v, avoid_duplicate_titles=False)]

    if not filtered:
        fallback_query = f"{fallback_channel or fallback_title or 'trending'} song audio"
        fallback_vids, _ = _youtube_search_videos(api_key, fallback_query, max_results=10)
        filtered = [v for v in fallback_vids if v["videoId"] not in exclude_ids and not _is_non_music_title(v.get("title", ""))]

    return jsonify({"videos": filtered[:8]})


@app.route("/api/chat", methods=["POST"])
def chat():
    """Chat endpoint with origin verification, rate limiting, payload validation, and secure headers."""
    # CSRF Origin validation for POST requests
    if not check_csrf_origin():
        return jsonify({"error": "Cross-Origin request blocked."}), 403

    client_ip = get_client_ip()
    allowed, retry_after = rate_limiter.is_allowed(client_ip, "gemini_chat", max_requests=30, window_seconds=60)
    if not allowed:
        return jsonify({"error": "Rate limit exceeded. Please wait before sending another message.", "retry_after": retry_after}), 429

    if not request.is_json:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request payload format."}), 400

    raw_prompt = data.get("prompt", "")
    prompt = sanitize_text(str(raw_prompt), max_length=4000)
    if not prompt:
        return jsonify({"error": "Prompt cannot be empty."}), 400

    t0 = time.time()
    raw_messages = data.get("messages")
    messages = []
    if isinstance(raw_messages, list):
        for msg in raw_messages[-12:]:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = sanitize_text(str(msg.get("content", "")), max_length=4000)
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})

    if not messages or messages[-1]["content"] != prompt:
        messages.append({"role": "user", "content": prompt})

    model_name = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    # Sanitize model name to prevent path/URL manipulation
    model_name = re.sub(r"[^a-zA-Z0-9.-]", "", model_name)

    cache_key = "gemini_chat:" + api_cache.hash_key(model_name, messages)
    cached_reply = api_cache.get(cache_key)
    if cached_reply is not None:
        return jsonify({
            "reply": cached_reply,
            "latency_ms": 0.0,
            "cached": True,
        })

    if api_tracker.is_limit_reached("gemini"):
        return jsonify(api_tracker.get_limit_reached_response("gemini")), 429

    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        return jsonify({
            "error": "No Gemini API key is configured. Add GEMINI_API_KEY to the project's .env file and restart the app."
        }), 503

    # Secure transmission: API key passed in x-goog-api-key header, not in query string
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"

    contents = [
        {
            "role": "model" if message["role"] == "assistant" else "user",
            "parts": [{"text": message["content"]}],
        }
        for message in messages
    ]
    payload = json.dumps({
        "contents": contents,
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 512,
        },
    }).encode("utf-8")

    http_request = Request(
        endpoint,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": gemini_key,
        },
        method="POST",
    )
    try:
        with urlopen(http_request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
        candidates = result.get("candidates") or []
        if not candidates:
            feedback = result.get("promptFeedback") or {}
            reason = feedback.get("blockReason") or "no candidate was returned"
            raise ValueError(f"Gemini returned no response: {reason}")
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        reply = "".join(part.get("text", "") for part in parts).strip()
        if not reply:
            reason = candidate.get("finishReason") or "empty response"
            raise ValueError(f"Gemini returned no response: {reason}")
        api_tracker.record_usage("gemini", endpoint="chat", count=1)
        api_cache.set(cache_key, reply, ttl_seconds=1800)
    except TimeoutError:
        return jsonify({"error": "Gemini request timed out. Try again."}), 504
    except HTTPError as error:
        if error.code == 429:
            return jsonify({
                "error": "Gemini API quota or rate limit reached. Check your Google AI Studio usage and billing limits."
            }), 429
        return jsonify({"error": f"Gemini API error (HTTP {error.code})."}), 502
    except ValueError as error:
        return jsonify({"error": redact_secrets(str(error))}), 502
    except (URLError, KeyError, IndexError, json.JSONDecodeError) as error:
        return jsonify({"error": f"Gemini could not answer: {redact_secrets(str(error))}"}), 502

    return jsonify({
        "reply": reply,
        "latency_ms": round((time.time() - t0) * 1000, 1),
    })


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")
    app.run(host="127.0.0.1", port=5000, debug=debug_mode)
