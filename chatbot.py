from __future__ import annotations

import os
from urllib.error import HTTPError, URLError

from chat_core import (
    DEFAULT_OLLAMA_MODEL,
    MAX_HISTORY_TOKENS,
    SESSION_FILE,
    SYSTEM_PROMPT,
    count_tokens,
    load_session,
    load_stock_api_key,
    ollama_chat,
    save_session,
    trim_messages,
)
from stock_data import load_cache
from stock_routing import (
    build_non_stock_reply,
    load_company_aliases,
    looks_like_stock_request,
    maybe_handle_stock_request,
    summarize_stock_memory_entry,
)


def main() -> None:
    ollama_model = os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip() or DEFAULT_OLLAMA_MODEL
    stock_api_key = load_stock_api_key()
    stock_cache = load_cache()
    company_aliases = load_company_aliases()

    print(f"Local Ollama chatbot ready with model: {ollama_model}. Type 'exit' to quit.")
    
    print("Commands: /clear, /history, /save, /load, /tokens")
    print("Stock requests: mention a ticker like AAPL or use /stock AAPL")

    messages = load_session()
    
    while True:
        user_input = input("You: ").strip()
        
        # Handle exit
        if user_input.lower() == "exit":
            print("Goodbye.")
            break
        
        # Handle commands
        if user_input.lower() == "/clear":
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("History cleared.")
            continue
        
        if user_input.lower() == "/history":
            if len(messages) <= 1:
                print("No history yet.")
            else:
                for msg in messages[1:]:
                    role = msg["role"].upper()
                    content = msg["content"][:80]
                    print(f"{role}: {content}..." if len(msg["content"]) > 80 else f"{role}: {content}")
            continue
        
        if user_input.lower() == "/save":
            save_session(messages)
            print(f"Session saved to {SESSION_FILE}.")
            continue
        
        if user_input.lower() == "/load":
            messages = load_session()
            print(f"Session loaded from {SESSION_FILE}.")
            continue
        
        if user_input.lower() == "/tokens":
            tokens = count_tokens(messages)
            print(f"Current history: ~{tokens} tokens (limit: {MAX_HISTORY_TOKENS}).")
            continue

        if user_input.lower().startswith("/stock"):
            parts = user_input.split(maxsplit=1)
            if len(parts) < 2:
                print("Usage: /stock TICKER_OR_COMPANY")
                continue

            stock_reply = maybe_handle_stock_request(
                parts[1].strip(),
                stock_api_key,
                stock_cache,
                company_aliases,
                ollama_model,
            )
            if stock_reply is None:
                print("No matching stock ticker found.")
            else:
                print(stock_reply)
            continue

        if not user_input:
            continue

        stock_reply = maybe_handle_stock_request(
            user_input,
            stock_api_key,
            stock_cache,
            company_aliases,
            ollama_model,
        )
        if stock_reply is not None:
            print(stock_reply)
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": summarize_stock_memory_entry(stock_reply)})
            messages = trim_messages(messages, MAX_HISTORY_TOKENS)
            continue

        if not looks_like_stock_request(user_input):
            refusal = build_non_stock_reply()
            print(refusal)
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": refusal})
            messages = trim_messages(messages, MAX_HISTORY_TOKENS)
            continue

        # Append user message
        messages.append({"role": "user", "content": user_input})

        try:
            reply = ollama_chat(messages, ollama_model)
            
            # Append assistant reply
            messages.append({"role": "assistant", "content": reply})
            
            # Trim history if needed
            messages = trim_messages(messages, MAX_HISTORY_TOKENS)

        except HTTPError as exc:
            # Remove failed user message
            messages.pop()
            print(f"Request failed: Ollama HTTP error {exc.code}.")
        except URLError as exc:
            messages.pop()
            print(f"Request failed: Ollama server is unavailable at localhost:11434 ({exc}).")
        except Exception as exc:
            messages.pop()
            print(f"Request failed: {exc}")


if __name__ == "__main__":
    main()
