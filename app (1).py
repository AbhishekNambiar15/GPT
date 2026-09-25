"""
MiniGPT local UI backend.

Serves static/index.html and exposes two endpoints the frontend calls:
  GET  /api/stats  -> model vitals for the instrument panel
  POST /api/chat    -> { "prompt": "..." } -> { "reply": "..." }

Wire the two TODOs below to your actual model.py / generate.py.
Run with:  python app.py   then open http://localhost:5000
"""

from flask import Flask, request, jsonify, send_from_directory
import time

app = Flask(__name__, static_folder="static")

# ---------------------------------------------------------------------------
# TODO: import your real model here, e.g.
# from model import MiniGPT
# from generate import load_checkpoint, generate_text
# model, tokenizer = load_checkpoint("checkpoint.pt")
# ---------------------------------------------------------------------------

MODEL_INFO = {
    "name": "MiniGPT",
    "params": "n/a",       # TODO: sum(p.numel() for p in model.parameters())
    "vocab_size": "n/a",   # TODO: tokenizer.vocab_size
    "context_len": "n/a",  # TODO: model.block_size
    "device": "cpu",       # TODO: str(next(model.parameters()).device)
}


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


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

    # -----------------------------------------------------------------
    # TODO: replace this stub with a real call, e.g.
    # reply = generate_text(model, tokenizer, prompt, max_new_tokens=200)
    # -----------------------------------------------------------------
    reply = f"(stub) MiniGPT hasn't been wired up yet — echoing: {prompt}"

    return jsonify({
        "reply": reply,
        "latency_ms": round((time.time() - t0) * 1000, 1),
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
