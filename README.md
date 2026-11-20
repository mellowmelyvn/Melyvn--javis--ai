# MELYVN JARVIS

MELYVN JARVIS is a personal AI assistant project.

## Current build
- Responsive futuristic assistant interface.
- Browser speech output and speech recognition where supported.
- Basic local demo responses for greetings, date, time, and help.
- Chat-style interaction panel.

## Important current limitations
This repository currently hosts a browser interface, not a full Windows assistant. A normal web page cannot continuously listen for a wake word in the background or freely open/control local Windows applications. Those capabilities require a Windows desktop companion with explicit operating-system permissions.

The AI brain is not connected yet. The next stage is a secure backend that keeps API credentials server-side and connects the interface to an AI model. Do not put private API keys in this public repository or browser JavaScript.

## Run locally
Open `index.html` in a modern browser. For microphone support, use localhost or a secure HTTPS origin and allow microphone access. Chrome or Edge are recommended.

## Roadmap
1. Secure AI backend and chat endpoint.
2. Windows desktop companion and wake-word service.
3. Approved PC actions (launch apps, open websites, read selected files).
4. Optional phone integration and notifications.

## Privacy and safety
Keep credentials in server-side environment variables. Ask for confirmation before destructive file operations, purchases, sending messages, or other consequential actions.
