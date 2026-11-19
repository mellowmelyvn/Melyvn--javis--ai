# MELYVN JARVIS AI

A JARVIS-style personal assistant: a futuristic web page with voice input and
spoken replies, plus a small **local helper** that lets it manage your Windows
computer.

## How it works

- `index.html` is the Jarvis page. On its own (for example on GitHub Pages) it
  runs in **demo mode** and cannot touch your computer.
- `helper/jarvis_helper.py` runs on your PC, serves the same page at
  `http://127.0.0.1:8765/`, and carries out commands. Open the page **from the
  helper**, not from GitHub Pages, to control your computer.

## Run it (Windows)

1. Install Python 3.9 or newer from python.org (tick "Add Python to PATH").
2. Double-click `helper/start-jarvis.bat`.
3. Your browser opens Jarvis. The status line shows **COMPUTER LINK ACTIVE**.
4. Click SPEAK, or type a command and press Enter. Use Chrome or Edge for voice.

No packages to install. Press Ctrl+C in the helper window to stop it.

## Commands

Commands are fixed phrases (not free-form AI). Say or type, for example:

| Area | Examples |
| --- | --- |
| Apps | `open chrome`, `open notepad`, `open calculator`, `open mt5` |
| Websites | `open youtube`, `go to tradingview.com`, `search for gold price today` |
| Files | `find invoice`, `find plan in downloads`, `open result 1`, `open file report` |
| Folders | `open downloads`, `list downloads`, `create folder Trading Notes`, `make a folder reports in documents` |
| Sound | `volume up`, `volume down`, `mute` |
| Screen | `screenshot` (saved to Pictures, Jarvis Screenshots), `lock the computer` |
| Power | `sleep`, `shut down`, `restart` (each asks you to say `confirm`), `cancel shutdown` |
| Info | `system status`, `battery`, `cpu`, `memory`, `disk space`, `what's running`, `what time is it` |

## Add your own apps and sites

Edit `helper/config.json`. Jarvis can only open apps and sites listed there.
For MetaTrader 5, check that the `mt5` path matches where it is installed on your PC.

## Safety

The helper controls your computer, so it is deliberately restrictive:

- It listens on `127.0.0.1` only, so other devices on your network cannot reach it.
- Every command needs a random token that is created each time the helper starts
  and is only given to the page the helper serves. Requests with the wrong host,
  origin or token are refused.
- Typed text is never run as a shell command. Only the built-in commands above work.
- Apps open only if listed in `config.json`.
- Files open only from Desktop, Documents, Downloads, Pictures, Videos and Music.
  Program and script types (`.exe`, `.bat`, `.ps1`, `.msi`, `.lnk` and similar) are never opened.
- Jarvis never deletes or moves files.
- Shutdown, restart and sleep need a spoken or typed `confirm` within 20 seconds.
- Commands are logged to `helper/jarvis.log` on your computer (kept out of git).

Do not run the helper on a shared or untrusted computer, and do not forward its
port to the internet.

## Tests

```
cd helper
python -m unittest -v
```

The tests check command parsing, file safety and the server's token, host and
origin checks. Windows-only actions (volume, lock, screenshot, power) are
tested with a stand-in, so try them on your PC.

## Not included yet

- Free-form understanding of speech (this version uses fixed commands).
- A trading module (market data, signals from your bots).
- Controlling other computers or phones.
