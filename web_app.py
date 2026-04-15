from __future__ import annotations

import json
import os

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from chat_core import (
    DEFAULT_OLLAMA_MODEL,
    MAX_HISTORY_TOKENS,
    SYSTEM_PROMPT,
    count_tokens,
    load_stock_api_key,
    ollama_chat,
    ollama_chat_stream,
    trim_messages,
)
from stock_data import load_cache
from stock_routing import build_non_stock_reply, load_company_aliases, looks_like_stock_request, maybe_handle_stock_request


app = Flask(__name__)
ollama_model = os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip() or DEFAULT_OLLAMA_MODEL
try:
    stock_api_key = load_stock_api_key()
except Exception:
    stock_api_key = ""
stock_cache = load_cache()
company_aliases = load_company_aliases()


@app.get("/")
def index():
    return render_template("index.html", model_name=ollama_model, SYSTEM_PROMPT=SYSTEM_PROMPT)


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "model": ollama_model})


@app.post("/api/chat")
def chat():
    payload = request.get_json(silent=True) or {}
    messages = payload.get("messages") or []
    user_input = (payload.get("input") or "").strip()

    if not user_input:
        return jsonify({"ok": False, "error": "Message text is required."}), 400

    if not messages:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    elif messages[0].get("role") != "system":
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    messages = [
        {"role": str(message.get("role", "")).strip() or "user", "content": str(message.get("content", ""))}
        for message in messages
        if isinstance(message, dict)
    ]

    if not messages or messages[-1].get("role") != "user" or messages[-1].get("content", "") != user_input:
        messages.append({"role": "user", "content": user_input})

    if stock_api_key:
        stock_reply = maybe_handle_stock_request(
            user_input,
            stock_api_key,
            stock_cache,
            company_aliases,
            ollama_model,
        )
    elif looks_like_stock_request(user_input):
        return jsonify(
            {
                "ok": False,
                "error": "Stock lookups are not configured. Set STOCK_API_KEY in .env.",
            }
        ), 400
    else:
        stock_reply = build_non_stock_reply()

    if stock_reply is not None:
        messages.append({"role": "assistant", "content": stock_reply})
        messages = trim_messages(messages, MAX_HISTORY_TOKENS)
        return jsonify(
            {
                "ok": True,
                "reply": stock_reply,
                "messages": messages,
                "tokens": count_tokens(messages),
                "mode": "stock",
            }
        )

    try:
        reply = ollama_chat(messages, ollama_model)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    messages.append({"role": "assistant", "content": reply})
    messages = trim_messages(messages, MAX_HISTORY_TOKENS)

    return jsonify(
        {
            "ok": True,
            "reply": reply,
            "messages": messages,
            "tokens": count_tokens(messages),
            "mode": "chat",
        }
    )


def _sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=True)}\n\n"


@app.post("/api/chat/stream")
def chat_stream():
    payload = request.get_json(silent=True) or {}
    messages = payload.get("messages") or []
    user_input = (payload.get("input") or "").strip()

    if not user_input:
        return jsonify({"ok": False, "error": "Message text is required."}), 400

    if not messages:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    elif messages[0].get("role") != "system":
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    messages = [
        {"role": str(message.get("role", "")).strip() or "user", "content": str(message.get("content", ""))}
        for message in messages
        if isinstance(message, dict)
    ]

    if not messages or messages[-1].get("role") != "user" or messages[-1].get("content", "") != user_input:
        messages.append({"role": "user", "content": user_input})

    @stream_with_context
    def event_stream():
        if stock_api_key:
            stock_reply = maybe_handle_stock_request(
                user_input,
                stock_api_key,
                stock_cache,
                company_aliases,
                ollama_model,
            )
        elif looks_like_stock_request(user_input):
            yield _sse_data({"type": "error", "error": "Stock lookups are not configured. Set STOCK_API_KEY in .env."})
            return
        else:
            stock_reply = build_non_stock_reply()

        if stock_reply is not None:
            final_messages = messages + [{"role": "assistant", "content": stock_reply}]
            final_messages = trim_messages(final_messages, MAX_HISTORY_TOKENS)
            yield _sse_data({"type": "chunk", "delta": stock_reply, "mode": "stock"})
            yield _sse_data(
                {
                    "type": "done",
                    "reply": stock_reply,
                    "messages": final_messages,
                    "tokens": count_tokens(final_messages),
                    "mode": "stock",
                }
            )
            return

        full_reply = ""
        try:
            for piece in ollama_chat_stream(messages, ollama_model):
                if piece:
                    full_reply += piece
                    yield _sse_data({"type": "chunk", "delta": piece, "mode": "chat"})
        except Exception as exc:
            yield _sse_data({"type": "error", "error": str(exc)})
            return

        final_messages = messages + [{"role": "assistant", "content": full_reply}]
        final_messages = trim_messages(final_messages, MAX_HISTORY_TOKENS)
        yield _sse_data(
            {
                "type": "done",
                "reply": full_reply,
                "messages": final_messages,
                "tokens": count_tokens(final_messages),
                "mode": "chat",
            }
        )

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return Response(event_stream(), mimetype="text/event-stream", headers=headers)


SESSION_FILE = "Session.json"


@app.post("/api/session/save")
def save_session():
    payload = request.get_json(silent=True) or {}
    messages = payload.get("messages") or []

    if not isinstance(messages, list):
        return jsonify({"ok": False, "error": "Messages must be a list"}), 400

    try:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump({"messages": messages}, f, indent=2, ensure_ascii=False)
        return jsonify({"ok": True, "saved": len(messages)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/session/load")
def load_session():
    try:
        if not os.path.exists(SESSION_FILE):
            return jsonify({"ok": True, "messages": []})
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            messages = data.get("messages", []) if isinstance(data, dict) else []
            return jsonify({"ok": True, "messages": messages})
    except Exception as exc:
        return jsonify({"ok": True, "messages": []})


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
