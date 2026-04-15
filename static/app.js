const boot = window.CHATBOT_BOOT || {};
const STORAGE_KEY = boot.storageKey || "chatbot-ui-history";
const SESSION_MARKER = "chatbot-session-active";
const SYSTEM_PROMPT = boot.systemPrompt || "You are a helpful assistant. Keep answers concise and practical.";

const chatWindow = document.getElementById("chatWindow");
const messageInput = document.getElementById("messageInput");
const sendButton = document.getElementById("sendButton");
const clearButton = document.getElementById("clearButton");
const copyButton = document.getElementById("copyButton");
const backendStatus = document.getElementById("backendStatus");
const chipButtons = Array.from(document.querySelectorAll(".chip"));

let messages = (() => {
  // Check if this is a new app instance (sessionStorage cleared)
  if (!sessionStorage.getItem(SESSION_MARKER)) {
    // New app instance - start fresh
    sessionStorage.setItem(SESSION_MARKER, "true");
    return [{ role: "system", content: SYSTEM_PROMPT }];
  }
  // Same session (page refresh) - load from localStorage
  return loadMessages();
})();
let lastAssistantReply = "";
let isSending = false;
let transientNotice = "";

function loadMessages() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      const parsed = JSON.parse(saved);
      if (Array.isArray(parsed) && parsed.length > 0) {
        return parsed;
      }
    }
  } catch (error) {
    console.warn("Failed to load chat history", error);
  }
  return [{ role: "system", content: SYSTEM_PROMPT }];
}

function saveMessages() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
  
  // Also save to server Session.json
  fetch("/api/session/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  }).catch((error) => console.warn("Failed to save session to server", error));
}

function escapeText(value) {
  return String(value ?? "");
}

function createTypingBubble() {
  const wrapper = document.createElement("div");
  wrapper.className = "message assistant";
  wrapper.dataset.typing = "true";

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = "AI";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = '<span class="typing" aria-label="Assistant is typing"><span></span><span></span><span></span></span>';

  wrapper.append(avatar, bubble);
  return wrapper;
}

function renderMessages() {
  chatWindow.innerHTML = "";

  const visibleMessages = messages.filter((message) => message.role !== "system");
  if (visibleMessages.length === 0) {
    const welcome = document.createElement("div");
    welcome.className = "message assistant";
    welcome.innerHTML = `
      <div class="avatar">AI</div>
      <div class="bubble">
        Ask for an overview, risk, volatility, or news about a company like Apple. You can also compare companies.
        <div class="meta">Use the quick prompts above or type your own.</div>
      </div>
    `;
    chatWindow.appendChild(welcome);
  } else {
    for (const message of visibleMessages) {
      const row = document.createElement("div");
      row.className = `message ${message.role}`;

      const avatar = document.createElement("div");
      avatar.className = "avatar";
      avatar.textContent = message.role === "user" ? "You" : "AI";

      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.textContent = escapeText(message.content);

      row.append(avatar, bubble);
      chatWindow.appendChild(row);
    }
  }

  if (transientNotice) {
    const notice = document.createElement("div");
    notice.className = "message assistant";
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = "AI";

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = transientNotice;

    notice.append(avatar, bubble);
    chatWindow.appendChild(notice);
  }

  scrollToBottom();
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    chatWindow.scrollTop = chatWindow.scrollHeight;
  });
}

function setBackendStatus(text, ok = true) {
  backendStatus.textContent = text;
  backendStatus.style.setProperty("--status-color", ok ? "var(--accent-strong)" : "#ff7f7f");
}

async function checkBackend() {
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    if (data.ok) {
      setBackendStatus(`Backend ready · ${data.model}`);
      return;
    }
    setBackendStatus("Backend unavailable", false);
  } catch (error) {
    setBackendStatus("Backend unavailable", false);
  }
}

function setSending(state) {
  isSending = state;
  sendButton.disabled = state;
  clearButton.disabled = state;
  chipButtons.forEach((button) => (button.disabled = state));
  messageInput.disabled = state;
  sendButton.textContent = state ? "Sending..." : "Send";
}

function appendLocalMessage(role, content) {
  messages.push({ role, content });
  saveMessages();
  renderMessages();
}

function updateAssistantReply(content) {
  lastAssistantReply = content || "";
}

function showNotice(message) {
  transientNotice = message;
  renderMessages();
}

function clearNotice() {
  transientNotice = "";
}

function countHistoryTokens() {
  return messages.reduce((total, message) => total + Math.max(1, String(message.content || "").length), 0) / 4;
}

function formatHistorySnapshot() {
  const visibleMessages = messages.filter((message) => message.role !== "system");
  if (visibleMessages.length === 0) {
    return "No history yet.";
  }

  return visibleMessages
    .map((message) => `${message.role === "user" ? "You" : "AI"}: ${String(message.content || "").slice(0, 120)}`)
    .join("\n");
}

function handleAppCommand(text) {
  const command = text.toLowerCase().trim();

  if (command === "/clear") {
    messages = [{ role: "system", content: SYSTEM_PROMPT }];
    lastAssistantReply = "";
    transientNotice = "";
    saveMessages();
    renderMessages();
    showNotice("History cleared.");
    return true;
  }

  if (command === "/save") {
    saveMessages();
    showNotice("Session saved in your browser.");
    return true;
  }

  if (command === "/load") {
    messages = loadMessages();
    saveMessages();
    renderMessages();
    showNotice("Session loaded from your browser.");
    return true;
  }

  if (command === "/history") {
    showNotice(formatHistorySnapshot());
    return true;
  }

  if (command === "/tokens" || command === "/token") {
    showNotice(`Current history: ~${Math.round(countHistoryTokens())} tokens (limit: 1200).`);
    return true;
  }

  return false;
}

async function sendMessage(text) {
  if (!text || isSending) {
    return;
  }

  clearNotice();

  if (handleAppCommand(text)) {
    messageInput.value = "";
    messageInput.style.height = "auto";
    messageInput.focus();
    return;
  }

  const requestMessages = messages.concat({ role: "user", content: text });

  messages.push({ role: "user", content: text });
  messages.push({ role: "assistant", content: "" });
  saveMessages();
  renderMessages();

  setSending(true);

  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: requestMessages, input: text }),
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `Request failed with status ${response.status}`);
    }

    if (!response.body) {
      throw new Error("Streaming not supported by this browser.");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let streamBuffer = "";
    let assistantText = "";
    let doneReceived = false;

    const updateAssistantBubble = () => {
      if (messages.length === 0) {
        return;
      }
      messages[messages.length - 1] = { role: "assistant", content: assistantText };
      const bubble = chatWindow.querySelector(".message.assistant:last-child .bubble");
      if (bubble) {
        bubble.textContent = assistantText;
      } else {
        renderMessages();
      }
      scrollToBottom();
    };

    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }

      streamBuffer += decoder.decode(value, { stream: true });

      let boundary = streamBuffer.indexOf("\n\n");
      while (boundary !== -1) {
        const rawEvent = streamBuffer.slice(0, boundary).trim();
        streamBuffer = streamBuffer.slice(boundary + 2);

        if (rawEvent.startsWith("data:")) {
          const payloadText = rawEvent.slice(5).trim();
          if (payloadText) {
            const event = JSON.parse(payloadText);
            if (event.type === "chunk") {
              assistantText += event.delta || "";
              updateAssistantBubble();
            } else if (event.type === "error") {
              throw new Error(event.error || "Streaming failed.");
            } else if (event.type === "done") {
              doneReceived = true;
              assistantText = event.reply || assistantText;
              messages = Array.isArray(event.messages) ? event.messages : messages;
              saveMessages();
              renderMessages();
              updateAssistantReply(assistantText);
            }
          }
        }

        boundary = streamBuffer.indexOf("\n\n");
      }
    }

    if (!doneReceived) {
      messages[messages.length - 1] = { role: "assistant", content: assistantText };
      saveMessages();
      renderMessages();
      updateAssistantReply(assistantText);
    }
  } catch (error) {
    if (messages.length > 0 && messages[messages.length - 1].role === "assistant" && messages[messages.length - 1].content === "") {
      messages.pop();
    }
    appendLocalMessage("assistant", `Request failed: ${error.message}`);
  } finally {
    setSending(false);
    messageInput.value = "";
    messageInput.style.height = "auto";
    messageInput.focus();
  }
}

function clearChat() {
  chatWindow.innerHTML = "";
  messageInput.focus();
}

function copyLastReply() {
  if (!lastAssistantReply) {
    return;
  }
  navigator.clipboard?.writeText(lastAssistantReply).catch(() => {});
}

sendButton.addEventListener("click", () => {
  const value = messageInput.value.trim();
  sendMessage(value);
});

clearButton.addEventListener("click", clearChat);
copyButton.addEventListener("click", copyLastReply);

chipButtons.forEach((button) => {
  button.addEventListener("click", () => {
    messageInput.value = button.dataset.prompt || "";
    messageInput.focus();
    messageInput.style.height = "auto";
    messageInput.style.height = `${messageInput.scrollHeight}px`;
  });
});

messageInput.addEventListener("input", () => {
  messageInput.style.height = "auto";
  messageInput.style.height = `${Math.min(messageInput.scrollHeight, 160)}px`;
});

messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage(messageInput.value.trim());
  }
});

window.addEventListener("beforeunload", saveMessages);

renderMessages();
checkBackend();
messageInput.focus();
