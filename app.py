from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from dotenv import load_dotenv
import time
import os
import json
from flask import Flask, request, jsonify, send_from_directory
"""MiniGPT Flask backend using the Gemini API."""

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

    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        return jsonify({
            "error": "No Gemini API key is configured. Add GEMINI_API_KEY to the project's .env file or set it in the same terminal before starting the app."
        }), 503

    model_name = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
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
