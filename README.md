# Chatbot

This project runs locally with Ollama and reads configuration from `.env` when present.

## Requirements

- Python 3.13 or newer
- Ollama installed with a local model pulled
- `STOCK_API_KEY` in `.env` for Massive stock lookups

## Environment setup

Create a `.env` file in the project root with values like:

```powershell
OLLAMA_MODEL=llama2-uncensored:latest
STOCK_API_KEY=your_massive_api_key_here
```

The app reads `STOCK_API_KEY` from `.env`.

## Run locally with Ollama

1. Start Ollama if it is not already running.
2. Make sure a model exists, for example `llama2-uncensored:latest` or any model you have installed.
3. Run:

```powershell
.\.venv\Scripts\python.exe .\chatbot.py
```

The app will auto-use Ollama when the local server is reachable on `http://localhost:11434`.

## Run the web UI

Start the Flask app to use the browser interface:

```powershell
.\.venv\Scripts\python.exe .\web_app.py
```

Then open `http://127.0.0.1:5000` in your browser.

The web UI keeps the conversation in the browser and sends each turn to Flask, which routes it to Ollama or the stock lookup logic.

## Switch models

Set `OLLAMA_MODEL` before launching, or place it in `.env`.

Example:

```powershell
$env:OLLAMA_MODEL = "llama3.1:8b-instruct-q8_0"
.\.venv\Scripts\python.exe .\chatbot.py
```

## Massive stock lookups

The chatbot uses `STOCK_API_KEY` from `.env`. It only fetches from Massive when you ask for stock info, and then caches the response locally in `stock_cache.json`.

### Supported pattern

- `/stock AAPL`
- Any message that includes a ticker like `AAPL`

Stock responses now include:

- `Overview`
- `News` (latest headlines)
- `Risks` (concise risk bullets derived from fetched data)

### What is cached

- Ticker overview responses from Massive
- News responses per ticker
- Future requests for the same ticker will use the local cache instead of calling Massive again

### Notes

- The cache file is created only after the first stock request
- Delete `stock_cache.json` if you want to force a fresh fetch from Massive

## Conversation Memory

The chatbot now remembers the full conversation history within a session, up to ~1200 tokens.

### Commands

- `/history` - Show recent conversation turns
- `/clear` - Clear all history and start fresh
- `/tokens` - Display current token count and limit
- `/save` - Save session to `session.json` for reuse later
- `/load` - Restore previous session from `session.json`
- `exit` - Quit the chatbot

### How it works

1. Each turn adds your input + bot response to a message list.
2. The list is sent to the model on every request, so the bot sees prior context.
3. If history grows beyond ~1200 tokens, oldest exchanges are dropped to stay under the token limit.
4. Session files are plain JSON, so you can edit or inspect them.

### Examples

```
You: What is your name?
Bot: I'm your local Ollama chatbot.

You: Remember that?
Bot: Yes, I remember the earlier message in this session.

You: /tokens
Bot: Current history: ~150 tokens (limit: 1200).

You: /save
Bot: Session saved to session.json.
```

On next launch, use `/load` to restore the earlier conversation or start fresh with `/clear`.