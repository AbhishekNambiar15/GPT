"""MiniGPT Flask backend with Gemini AI, YouTube player, and API usage management."""

import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

from api_manager import api_cache, api_tracker

load_dotenv()

app = Flask(__name__, static_folder=".", static_url_path="")

# ---------------------------------------------------------------------------
MODEL_INFO = {
    "name": os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
    "params": "hosted",
    "vocab_size": "n/a",
    "context_len": "n/a",
    "device": "api",
}


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "shell.html")


@app.route("/api/stats")
def stats():
    return jsonify(MODEL_INFO)


@app.route("/api/usage")
def usage():
    return jsonify(api_tracker.get_usage_summary())


@app.route("/api/youtube/search")
def youtube_search():
    query = request.args.get("q", "").strip()
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
        "key": api_key,
        "part": "snippet",
        "q": query,
        "type": "video",
        "videoEmbeddable": "true",
        "maxResults": 12,
    })
    http_request = Request(
        f"https://www.googleapis.com/youtube/v3/search?{params}",
        headers={"Accept": "application/json"},
    )
    try:
        with urlopen(http_request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
            api_tracker.record_usage("youtube", endpoint="search", count=1)
    except TimeoutError:
        return jsonify({"error": "YouTube search timed out. Try again."}), 504
    except HTTPError as error:
        try:
            details = json.loads(
                error.read().decode("utf-8", errors="replace"))
            reason = details.get("error", {}).get(
                "errors", [{}])[0].get("reason", "")
        except (json.JSONDecodeError, IndexError, AttributeError):
            reason = ""
        if error.code == 403 and reason in {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
            return jsonify({"error": "YouTube API quota reached. Try again later or check your Google Cloud quota."}), 429
        return jsonify({"error": f"YouTube search failed (HTTP {error.code}). Check the API key and YouTube Data API settings."}), 502
    except (URLError, json.JSONDecodeError) as error:
        return jsonify({"error": f"Could not reach YouTube search: {error}"}), 502

    videos = []
    for item in result.get("items", []):
        video_id = (item.get("id") or {}).get("videoId")
        snippet = item.get("snippet") or {}
        if not video_id:
            continue
        thumbnails = snippet.get("thumbnails") or {}
        thumbnail = (thumbnails.get("medium") or thumbnails.get(
            "default") or {}).get("url", "")
        videos.append({
            "videoId": video_id,
            "title": snippet.get("title", "Untitled video"),
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


def _normalize_song_title(title):
    """Collapse a video title down to something comparable across uploads,
    stripping bracketed text, common upload-junk words, and punctuation so
    'Old Town Road (Official Video)' and 'Old Town Road - Lyrics' match."""
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


def _youtube_get(url, endpoint="api", ttl=3600):
    """GET a YouTube Data API URL. Checks cache first, enforces usage limits,
    records actual external requests, and caches responses."""
    cache_key = f"yt_url:{url}"
    cached = api_cache.get(cache_key)
    if cached is not None:
        return cached, None

    if api_tracker.is_limit_reached("youtube"):
        return None, (jsonify(api_tracker.get_limit_reached_response("youtube")), 429)

    http_request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(http_request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
            api_tracker.record_usage("youtube", endpoint=endpoint, count=1)
            api_cache.set(cache_key, data, ttl_seconds=ttl)
            return data, None
    except TimeoutError:
        return None, (jsonify({"error": "YouTube request timed out. Try again."}), 504)
    except HTTPError as error:
        try:
            details = json.loads(
                error.read().decode("utf-8", errors="replace"))
            reason = details.get("error", {}).get(
                "errors", [{}])[0].get("reason", "")
        except (json.JSONDecodeError, IndexError, AttributeError):
            reason = ""
        if error.code == 403 and reason in {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
            return None, (jsonify({"error": "YouTube API quota reached. Try again later or check your Google Cloud quota."}), 429)
        return None, (jsonify({"error": f"YouTube request failed (HTTP {error.code})."}), 502)
    except (URLError, json.JSONDecodeError) as error:
        return None, (jsonify({"error": f"Could not reach YouTube: {error}"}), 502)


def _youtube_search_videos(api_key, query, max_results=10):
    params = urlencode({
        "key": api_key,
        "part": "snippet",
        "q": query,
        "type": "video",
        "videoEmbeddable": "true",
        "maxResults": max_results,
    })
    data, error = _youtube_get(
        f"https://www.googleapis.com/youtube/v3/search?{params}",
        endpoint="related_search",
        ttl=3600
    )
    if error:
        return [], error
    videos = []
    for item in data.get("items", []):
        video_id = (item.get("id") or {}).get("videoId")
        snippet = item.get("snippet") or {}
        if not video_id:
            continue
        thumbnails = snippet.get("thumbnails") or {}
        thumbnail = (thumbnails.get("medium") or thumbnails.get(
            "default") or {}).get("url", "")
        videos.append({
            "videoId": video_id,
            "title": snippet.get("title", "Untitled video"),
            "channelTitle": snippet.get("channelTitle", "YouTube"),
            "thumbnail": thumbnail,
        })
    return videos, None


@app.route("/api/youtube/related")
def youtube_related():
    """Suggest a song related in artist/genre/vibe to the given video, for
    the music page's autoplay feature. Uses the playing video's own metadata
    (channel, tags, title keywords) rather than a hardcoded song list, and
    filters out the source video and anything the client marks as already
    played so autoplay moves forward instead of looping the same track."""
    video_id = request.args.get("videoId", "").strip()
    if not video_id:
        return jsonify({"error": "A videoId is required."}), 400

    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        return jsonify({
            "error": "YouTube search is not configured. Add YOUTUBE_API_KEY to the project's .env file and restart the app."
        }), 503

    fallback_title = request.args.get("title", "").strip()
    fallback_channel = request.args.get("channelTitle", "").strip()
    exclude_ids = {v for v in request.args.get(
        "excludeIds", "").split(",") if v}
    exclude_ids.add(video_id)
    exclude_titles = {t for t in request.args.get(
        "excludeTitles", "").split("|") if t}

    candidate_cache_key = f"yt_related_candidates:{video_id}"
    candidates = api_cache.get(candidate_cache_key)

    if candidates is None:
        # Look up the currently playing video so recommendations can be based on
        # its real artist/genre metadata instead of just the raw search term.
        details_params = urlencode(
            {"key": api_key, "part": "snippet", "id": video_id})
        details, error = _youtube_get(
            f"https://www.googleapis.com/youtube/v3/videos?{details_params}",
            endpoint="video_details",
            ttl=7200
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
        if channel_title:
            queries.append(channel_title)
        if tags:
            queries.append(" ".join(tags[:4]))
        if title:
            words = [w for w in re.sub(
                r"[^\w\s]", " ", title).split() if len(w) > 2]
            if words:
                queries.append(" ".join(words[:6]))
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
        if avoid_duplicate_titles and _normalize_song_title(video["title"]) in exclude_titles:
            return False
        return True

    filtered = [v for v in candidates if keep(v, avoid_duplicate_titles=True)]
    if not filtered:
        # Every candidate looked like a duplicate/recently-played title —
        # relax that check but still never replay an exact video ID.
        filtered = [v for v in candidates if keep(
            v, avoid_duplicate_titles=False)]

    return jsonify({"videos": filtered[:8]})


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    prompt = (data or {}).get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "empty prompt"}), 400

    t0 = time.time()
    messages = (data or {}).get("messages") or []
    messages = [
        {"role": message["role"], "content": str(message["content"])}
        for message in messages[-12:]
        if message.get("role") in {"user", "assistant"} and message.get("content")
    ]
    if not messages or messages[-1]["content"] != prompt:
        messages.append({"role": "user", "content": prompt})

    model_name = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
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
            "error": "No Gemini API key is configured. Add GEMINI_API_KEY to the project's .env file or set it in the same terminal before starting the app."
        }), 503

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_name}:generateContent?key={gemini_key}"
    )
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
    http_request = Request(endpoint, data=payload, headers={
        "Content-Type": "application/json",
    }, method="POST")
    try:
        with urlopen(http_request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
        candidates = result.get("candidates") or []
        if not candidates:
            feedback = result.get("promptFeedback") or {}
            reason = feedback.get("blockReason") or "no candidate was returned"
            raise ValueError(f"Gemini returned no text: {reason}")
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        reply = "".join(part.get("text", "") for part in parts).strip()
        if not reply:
            reason = candidate.get("finishReason") or "empty response"
            raise ValueError(f"Gemini returned no text: {reason}")
        api_tracker.record_usage("gemini", endpoint="chat", count=1)
        api_cache.set(cache_key, reply, ttl_seconds=1800)
    except TimeoutError:
        return jsonify({"error": "Gemini did not respond within 120 seconds."}), 504
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        if error.code == 429:
            return jsonify({
                "error": "Gemini API quota or rate limit reached. Check your Google AI Studio usage and billing limits."
            }), 429
        return jsonify({"error": f"Gemini API error: {details or error}"}), 502
    except ValueError as error:
        return jsonify({"error": str(error)}), 502
    except (URLError, KeyError, IndexError, json.JSONDecodeError) as error:
        return jsonify({"error": f"Gemini could not answer: {error}"}), 502
    return jsonify({
        "reply": reply,
        "latency_ms": round((time.time() - t0) * 1000, 1),
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
