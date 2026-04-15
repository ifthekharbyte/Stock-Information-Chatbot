from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


DEFAULT_OLLAMA_MODEL = "llama2-uncensored:latest"
SYSTEM_PROMPT = "You are a helpful assistant. Keep answers concise and practical."
MAX_HISTORY_TOKENS = 1200
SESSION_FILE = "session.json"
DOTENV_FILE = ".env"


def load_dotenv_file(path: str = DOTENV_FILE) -> None:
    file_path = Path(path)
    if not file_path.exists():
        return

    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv_file()


def load_stock_api_key() -> str:
    env_key = os.environ.get("STOCK_API_KEY", "").strip()
    if env_key:
        return env_key

    raise ValueError("Set STOCK_API_KEY in .env before starting the app.")


def ollama_server_ready(base_url: str = "http://localhost:11434") -> bool:
    request = Request(f"{base_url}/api/tags", method="GET")
    try:
        with urlopen(request, timeout=2):
            return True
    except Exception:
        return False


def ollama_chat(messages: list[dict], model: str) -> str:
    """Send message history to Ollama and get response."""
    print("Bot: ", end="", flush=True)
    chunks: list[str] = []
    for piece in ollama_chat_stream(messages, model):
        chunks.append(piece)
        print(piece, end="", flush=True)
    print()
    return "".join(chunks).strip()


def ollama_chat_stream(messages: list[dict], model: str):
    """Yield response chunks from Ollama as they arrive."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    request = Request(
        "http://localhost:11434/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urlopen(request, timeout=120) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            data = json.loads(line)
            piece = (data.get("message") or {}).get("content", "")
            if piece:
                yield piece
            if data.get("done"):
                break


def count_tokens(messages: list[dict]) -> int:
    """Rough token estimate: ~4 chars per token."""
    total_chars = sum(len(msg.get("content", "")) for msg in messages)
    return max(1, total_chars // 4)


def trim_messages(messages: list[dict], max_tokens: int) -> list[dict]:
    """Keep system prompt and trim oldest user-assistant pairs if over limit."""
    if not messages or len(messages) < 2:
        return messages

    system = [messages[0]]
    recent = messages[1:]

    while len(recent) > 0 and count_tokens(system + recent) > max_tokens:
        recent = recent[2:]

    return system + recent


def save_session(messages: list[dict], filename: str = SESSION_FILE) -> None:
    """Save chat history to JSON file."""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(messages, f, indent=2)


def load_session(filename: str = SESSION_FILE) -> list[dict]:
    """Load chat history from JSON file or return fresh system prompt."""
    if Path(filename).exists():
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    return [{"role": "system", "content": SYSTEM_PROMPT}]
