"""Local-only backend for calendarmate-demo.html.

Exposes a single endpoint, POST /chat, that hands the message straight
to live_assistant.handle_request -- the exact same real-account
pipeline (real Google Calendar, real Gmail, real OpenAI calls) the CLI
uses. There is no mocked-fixture mode here: every message sent to this
server reaches your real account, the same as running
`python live_assistant.py "..."` yourself.

Binds to 127.0.0.1 only, never 0.0.0.0 -- this must never be reachable
from outside this machine. CORS is enabled for the /chat route only,
since calendarmate-demo.html is opened as a local file (file://) rather
than served, and browsers send Origin: null for local-file fetches.

Usage:
    pip install flask flask-cors   # one-time, not part of the core project deps
    python demo_server.py
    -- then open calendarmate-demo.html directly in a browser
"""

from __future__ import annotations

from flask import Flask, jsonify, request
from flask_cors import CORS

from live_assistant import handle_request

app = Flask(__name__)
CORS(app, resources={r"/chat": {"origins": "*"}})


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400
    response = handle_request(message)
    return jsonify({"response": response})


if __name__ == "__main__":
    print("CalendarMate demo server -- real account, real calendar, real inbox.")
    print("Listening on http://127.0.0.1:8787 (localhost only)")
    app.run(host="127.0.0.1", port=8787)
