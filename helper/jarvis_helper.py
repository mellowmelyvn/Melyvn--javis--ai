#!/usr/bin/env python3
"""MELYVN JARVIS - local computer helper.

Runs a small web server on 127.0.0.1 (this computer only) that serves the
Jarvis page and carries out a fixed list of commands: open apps and websites,
find and open files, system controls and system info.

Design rules:
  * Uses only the Python standard library.
  * Listens on 127.0.0.1 only, and every command needs a random per-run token.
  * Never runs text you type as a shell command. Only built-in commands run,
    and apps can only be launched if they are listed in config.json.
  * Never deletes files. Shutdown, restart and sleep ask for confirmation.
"""

import argparse
import csv
import ctypes
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote_plus

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HOST = "127.0.0.1"
DEFAULT_PORT = 8765
TOKEN_TAG = '<meta name="jarvis-token" content="">'

FOLDERS = ("desktop", "documents", "downloads", "pictures", "videos", "music")
FOLDER_RE = "|".join(FOLDERS)

# Files of these types are never opened by Jarvis, because opening them can run code.
BLOCKED_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".scr", ".ps1", ".psm1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".msi", ".msp", ".lnk", ".hta", ".jar",
    ".reg", ".dll", ".cpl", ".pif", ".appx", ".msix", ".gadget",
}

CONFIRM_SECONDS = 20
MAX_RESULTS = 8
SEARCH_SECONDS = 5
SEARCH_MAX_DEPTH = 4
SEARCH_MAX_FILES = 60000

VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAD, 0xAE, 0xAF


def load_config(path=None):
    path = Path(path) if path else HERE / "config.json"
    cfg = {"apps": {}, "sites": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    for key in ("apps", "sites"):
        section = data.get(key, {})
        if isinstance(section, dict):
            cfg[key] = {
                str(k).strip().lower(): str(v)
                for k, v in section.items()
                if isinstance(v, str) and v.strip()
            }
    return cfg


def normalize(text, lower=True):
    t = text.strip()
    if lower:
        t = t.lower()
    t = re.sub(r"[.!?]+$", "", t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(
        r"^(?:(?:hey|ok|okay)\s+)?(?:jarvis[,\s]+)?(?:(?:please|can you|could you|would you)\s+)*",
        "",
        t,
        flags=re.I,
    )
    t = re.sub(r"\s*,?\s*(?:please|sir)$", "", t, flags=re.I)
    return t.strip()


def clean_folder_name(name):
    """Return a safe single folder name, or None if the name is not acceptable."""
    name = name.strip().strip("\"'")
    if not name or len(name) > 60 or name in (".", ".."):
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.\-]*", name):
        return None
    if name.endswith((".", " ")):
        return None
    return name


def format_size(num_bytes):
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


class Jarvis:
    """Command interpreter. Platform-touching methods are small so tests can replace them."""

    def __init__(self, config=None, windows=None, home=None):
        self.config = config if config is not None else load_config()
        self.windows = (os.name == "nt") if windows is None else windows
        self.home = Path(home) if home else Path.home()
        self.lock = threading.Lock()
        self.pending = None  # (action, expires_at)
        self.last_results = []
        self.routes = self._build_routes()

    # ---- platform layer (overridden in tests) -------------------------------
    def _startfile(self, target):
        if self.windows:
            os.startfile(target)  # noqa: S606 - target comes from config or a vetted path
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])

    def _run(self, args, timeout=15):
        return subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _press_key(self, vk, times=1):
        user32 = ctypes.windll.user32
        for _ in range(times):
            user32.keybd_event(vk, 0, 0, 0)
            user32.keybd_event(vk, 0, 2, 0)
            time.sleep(0.01)

    def _open_url(self, url):
        webbrowser.open(url)

    # ---- entry point --------------------------------------------------------
    def handle(self, text):
        with self.lock:
            cmd = normalize(text)
            if not cmd:
                return {"ok": False, "reply": "I did not catch that, Sir."}
            self._expire_pending()
            self._original = normalize(text, lower=False)
            for pattern, method in self.routes:
                m = pattern.match(cmd)
                if m:
                    try:
                        reply = method(m)
                    except Exception as exc:  # keep the helper alive whatever happens
                        return {"ok": False, "reply": f"That did not work, Sir: {exc}"}
                    return {"ok": True, "reply": reply}
            return {
                "ok": False,
                "reply": "I do not recognise that command, Sir. Say 'help' to hear what I can do.",
            }

    def _expire_pending(self):
        if self.pending and time.time() > self.pending[1]:
            self.pending = None

    # ---- routing table ------------------------------------------------------
    def _build_routes(self):
        r = lambda pattern, method: (re.compile(pattern), method)  # noqa: E731
        return [
            r(r"^(?:help|what can you do|commands|list commands)$", self.cmd_help),
            r(r"^(?:hello|hi|hey|good (?:morning|afternoon|evening))$", self.cmd_greet),
            r(r"^(?:what(?:'s| is) )?(?:the )?(time|date)(?: now| today)?$", self.cmd_time),
            r(r"^what day is it$", self.cmd_time),
            r(r"^what time is it(?: now)?$", self.cmd_time),
            r(r"^(?:cancel|abort|stop) (?:the )?(?:shutdown|restart|reboot)$", self.cmd_abort_shutdown),
            r(r"^(?:confirm|yes|yes do it|do it|go ahead|proceed)$", self.cmd_confirm),
            r(r"^(?:cancel|no|never mind)$", self.cmd_cancel),
            r(rf"^open (?:the |my )?(?:folder )?({FOLDER_RE})(?: folder)?$", self.cmd_open_folder),
            r(rf"^(?:list|show)(?: me)?(?: my| the)?(?: recent| latest| newest)? ({FOLDER_RE})(?: folder| files)?$", self.cmd_list_folder),
            r(rf"^(?:create|make|new)(?: a)?(?: new)? folder (?:called |named )?(.+?)(?: (?:on|in)(?: my| the)? ({FOLDER_RE}))?$", self.cmd_create_folder),
            r(r"^open (?:result|number|file number) (\d+)$", self.cmd_open_result),
            r(r"^open file (.+)$", self.cmd_open_file),
            r(rf"^(?:find|locate)(?: the)?(?: files?)? (.+?)(?: (?:in|on)(?: my| the)? ({FOLDER_RE}))?$", self.cmd_find),
            r(r"^(?:google|search google for|search the web for|search for|look up|search) (.+)$", self.cmd_web_search),
            r(r"^(?:go to|visit|browse to|open (?:the )?(?:website|site|url)) (.+)$", self.cmd_website),
            r(r"^(?:open|launch|start|run) (?:the )?(.+)$", self.cmd_open),
            r(r"^(?:volume up|turn (?:the )?volume up|louder|increase (?:the )?volume)$", self.cmd_volume_up),
            r(r"^(?:volume down|turn (?:the )?volume down|quieter|lower (?:the )?volume|decrease (?:the )?volume)$", self.cmd_volume_down),
            r(r"^(?:mute|unmute)(?: the)?(?: sound| volume)?$", self.cmd_mute),
            r(r"^(?:take (?:a )?)?screenshot$", self.cmd_screenshot),
            r(r"^lock(?: the)?(?: computer| pc| screen| laptop)?$", self.cmd_lock),
            r(r"^(?:go to )?sleep(?: mode)?$|^put (?:the )?(?:computer|pc|laptop) to sleep$", self.cmd_sleep),
            r(r"^(?:shut ?down|power off|turn off)(?: the)?(?: computer| pc| laptop)?$", self.cmd_shutdown),
            r(r"^(?:restart|reboot)(?: the)?(?: computer| pc| laptop)?$", self.cmd_restart),
            r(r"^(?:battery|battery level|battery status|how much battery)$", self.cmd_battery),
            r(r"^(?:cpu|cpu usage|processor)$", self.cmd_cpu),
            r(r"^(?:memory|ram|memory usage)$", self.cmd_memory),
            r(r"^(?:disk|storage|disk space|free space)$", self.cmd_disk),
            r(r"^(?:system status|status|system info|system report)$", self.cmd_status),
            r(r"^(?:running apps|what(?:'s| is) running|list processes|running programs)$", self.cmd_running),
        ]

    # ---- helpers ------------------------------------------------------------
    def _need_windows(self):
        if not self.windows:
            raise RuntimeError("that control is only available on Windows")

    def known_folder(self, name):
        name = name.lower()
        if name not in FOLDERS:
            return None
        title = name.capitalize()
        for base in (self.home, self.home / "OneDrive"):
            candidate = base / title
            if candidate.is_dir():
                return candidate
        return self.home / title

    def _allowed_roots(self):
        roots = []
        for name in FOLDERS:
            folder = self.known_folder(name)
            if folder and folder.is_dir():
                roots.append(folder.resolve())
        return roots

    def _inside_allowed(self, path):
        try:
            resolved = Path(path).resolve()
        except OSError:
            return False
        for root in self._allowed_roots():
            if resolved == root or root in resolved.parents:
                return True
        return False

    def _open_path(self, path):
        path = Path(path)
        if not self._inside_allowed(path):
            raise RuntimeError("I only open items inside your Desktop, Documents, Downloads, Pictures, Videos and Music folders")
        if path.is_file() and path.suffix.lower() in BLOCKED_EXTENSIONS:
            raise RuntimeError(f"I will not open {path.suffix.lower()} files, because they can run programs")
        self._startfile(str(path.resolve()))

    def _search_files(self, query, folder_name=None):
        query = query.lower().strip()
        folders = [folder_name] if folder_name else list(FOLDERS)
        results, scanned = [], 0
        deadline = time.time() + SEARCH_SECONDS
        for name in folders:
            base = self.known_folder(name)
            if not base or not base.is_dir():
                continue
            base_depth = len(base.parts)
            for dirpath, dirnames, filenames in os.walk(base):
                depth = len(Path(dirpath).parts) - base_depth
                dirnames[:] = [d for d in dirnames if not d.startswith(".")] if depth < SEARCH_MAX_DEPTH else []
                for fname in filenames:
                    scanned += 1
                    if query in fname.lower():
                        results.append(Path(dirpath) / fname)
                        if len(results) >= MAX_RESULTS:
                            return results
                if scanned > SEARCH_MAX_FILES or time.time() > deadline:
                    return results
        return results

    # ---- commands: general --------------------------------------------------
    def cmd_help(self, m):
        return (
            "I can open apps and websites, search Google, find and open files, list folders, "
            "create folders, change volume, take screenshots, lock, sleep, shut down or restart "
            "the computer, and report battery, CPU, memory, disk and running apps. "
            "Try: open chrome, find invoice, list downloads, volume up, system status."
        )

    def cmd_greet(self, m):
        return "At your service, Sir."

    def cmd_time(self, m):
        now = datetime.now()
        if m.re.pattern.startswith("^what day") or (m.groups() and m.group(1) == "date"):
            return now.strftime("Today is %A, %d %B %Y, Sir.")
        return now.strftime("It is %H:%M, Sir.")

    # ---- commands: confirmation ---------------------------------------------
    def _ask_confirm(self, action, prompt):
        self.pending = (action, time.time() + CONFIRM_SECONDS)
        return f"{prompt} Say 'confirm' within {CONFIRM_SECONDS} seconds, or 'cancel'."

    def cmd_confirm(self, m):
        if not self.pending:
            return "Nothing is waiting for confirmation, Sir."
        action = self.pending[0]
        self.pending = None
        return {
            "shutdown": self._do_shutdown,
            "restart": self._do_restart,
            "sleep": self._do_sleep,
        }[action]()

    def cmd_cancel(self, m):
        if self.pending:
            self.pending = None
            return "Cancelled, Sir."
        return "Nothing to cancel, Sir."

    def cmd_abort_shutdown(self, m):
        self._need_windows()
        self._run(["shutdown", "/a"])
        return "Scheduled shutdown cancelled, Sir, if one was pending."

    # ---- commands: apps and web ---------------------------------------------
    def cmd_open(self, m):
        name = m.group(1).strip()
        apps, sites = self.config["apps"], self.config["sites"]
        if name in apps:
            target = apps[name]
            if re.match(r"^[A-Za-z]:\\", target) and not Path(target).exists():
                return f"I could not find {name} at {target}, Sir. Fix the path in helper/config.json."
            self._startfile(target)
            return f"Opening {name}, Sir."
        if name in sites:
            self._open_url(sites[name])
            return f"Opening {name}, Sir."
        return f"I do not have an app called {name}, Sir. Add it to helper/config.json to enable it."

    def cmd_website(self, m):
        name = m.group(1).strip()
        sites = self.config["sites"]
        if name in sites:
            self._open_url(sites[name])
            return f"Opening {name}, Sir."
        url = name if re.match(r"^https?://", name) else "https://" + name
        if not re.fullmatch(r"https?://[A-Za-z0-9][A-Za-z0-9.\-]*\.[A-Za-z]{2,}(?::\d+)?(?:/[^\s]*)?", url):
            return "That does not look like a website address, Sir."
        self._open_url(url)
        return f"Opening {url}, Sir."

    def cmd_web_search(self, m):
        query = m.group(1).strip()
        self._open_url("https://www.google.com/search?q=" + quote_plus(query))
        return f"Searching Google for {query}, Sir."

    # ---- commands: files ----------------------------------------------------
    def cmd_open_folder(self, m):
        folder = self.known_folder(m.group(1))
        if not folder or not folder.is_dir():
            return f"I cannot find your {m.group(1)} folder, Sir."
        self._open_path(folder)
        return f"Opening your {m.group(1)} folder, Sir."

    def cmd_list_folder(self, m):
        folder = self.known_folder(m.group(1))
        if not folder or not folder.is_dir():
            return f"I cannot find your {m.group(1)} folder, Sir."
        items = [p for p in folder.iterdir() if not p.name.startswith(".")]
        items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not items:
            return f"Your {m.group(1)} folder is empty, Sir."
        lines = [f"{p.name}{'/' if p.is_dir() else ''}" for p in items[:10]]
        return f"Newest in {m.group(1)}: " + "; ".join(lines) + "."

    def cmd_create_folder(self, m):
        raw = m.group(1)
        original = getattr(self, "_original", "")
        if len(original) == len(m.string):  # keep the capital letters the user typed
            raw = original[m.start(1):m.end(1)]
        name = clean_folder_name(raw)
        if not name:
            return "Use letters, numbers, spaces, dashes or underscores for the folder name, Sir."
        parent = self.known_folder(m.group(2) or "desktop")
        if not parent or not parent.is_dir():
            return "I cannot find that location, Sir."
        target = parent / name
        try:
            target.mkdir(exist_ok=False)
        except FileExistsError:
            return f"A folder called {name} already exists there, Sir."
        return f"Created the folder {name} in {parent.name}, Sir."

    def _describe_results(self, results):
        lines = []
        for i, p in enumerate(results, 1):
            lines.append(f"{i}. {p.name} ({p.parent.name})")
        return " ".join(lines)

    def cmd_find(self, m):
        query = m.group(1).strip()
        results = self._search_files(query, m.group(2))
        self.last_results = results
        if not results:
            return f"I found nothing matching {query}, Sir."
        return f"I found {len(results)}: " + self._describe_results(results) + " Say 'open result 1' to open one."

    def cmd_open_file(self, m):
        query = m.group(1).strip()
        results = self._search_files(query)
        self.last_results = results
        if not results:
            return f"I found nothing matching {query}, Sir."
        if len(results) == 1:
            self._open_path(results[0])
            return f"Opening {results[0].name}, Sir."
        return f"I found {len(results)} matches: " + self._describe_results(results) + " Say 'open result 1' to choose."

    def cmd_open_result(self, m):
        index = int(m.group(1))
        if not 1 <= index <= len(self.last_results):
            return "I have no such result, Sir. Ask me to find a file first."
        path = self.last_results[index - 1]
        self._open_path(path)
        return f"Opening {path.name}, Sir."

    # ---- commands: system controls -----------------------------------------
    def cmd_volume_up(self, m):
        self._need_windows()
        self._press_key(VK_VOLUME_UP, 5)
        return "Volume up, Sir."

    def cmd_volume_down(self, m):
        self._need_windows()
        self._press_key(VK_VOLUME_DOWN, 5)
        return "Volume down, Sir."

    def cmd_mute(self, m):
        self._need_windows()
        self._press_key(VK_VOLUME_MUTE, 1)
        return "Toggled mute, Sir."

    def cmd_screenshot(self, m):
        self._need_windows()
        pictures = self.known_folder("pictures")
        shots = pictures / "Jarvis Screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        file_path = shots / f"jarvis-{datetime.now():%Y%m%d-%H%M%S}.png"
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
            "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
            "$g=[System.Drawing.Graphics]::FromImage($bmp);"
            "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
            "$bmp.Save('" + str(file_path).replace("'", "''") + "');"
        )
        result = self._run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=30)
        if result.returncode != 0 or not file_path.exists():
            raise RuntimeError("the screenshot failed")
        return f"Screenshot saved in Pictures, Jarvis Screenshots, Sir."

    def cmd_lock(self, m):
        self._need_windows()
        self._run(["rundll32.exe", "user32.dll,LockWorkStation"])
        return "Locking the computer, Sir."

    def cmd_sleep(self, m):
        self._need_windows()
        return self._ask_confirm("sleep", "Put the computer to sleep?")

    def cmd_shutdown(self, m):
        self._need_windows()
        return self._ask_confirm("shutdown", "Shut the computer down?")

    def cmd_restart(self, m):
        self._need_windows()
        return self._ask_confirm("restart", "Restart the computer?")

    def _do_sleep(self):
        self._run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
        return "Going to sleep, Sir."

    def _do_shutdown(self):
        self._run(["shutdown", "/s", "/t", "30", "/c", "Jarvis: shutting down in 30 seconds"])
        return "Shutting down in 30 seconds, Sir. Say 'cancel shutdown' to stop it."

    def _do_restart(self):
        self._run(["shutdown", "/r", "/t", "30", "/c", "Jarvis: restarting in 30 seconds"])
        return "Restarting in 30 seconds, Sir. Say 'cancel restart' to stop it."

    # ---- commands: system info ---------------------------------------------
    def _battery_text(self):
        if not self.windows:
            return "Battery information is only available on Windows, Sir."

        class PowerStatus(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        status = PowerStatus()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return "I could not read the battery, Sir."
        if status.BatteryFlag == 128 or status.BatteryLifePercent == 255:
            return "This computer has no battery, Sir."
        plugged = "plugged in" if status.ACLineStatus == 1 else "on battery"
        return f"Battery is at {status.BatteryLifePercent} percent and {plugged}, Sir."

    def _memory_info(self):
        """Return (used_percent, used_bytes, total_bytes) or None."""
        if self.windows:
            class MemStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MemStatus()
            stat.dwLength = ctypes.sizeof(MemStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            total, avail = stat.ullTotalPhys, stat.ullAvailPhys
        else:
            try:
                info = {}
                for line in Path("/proc/meminfo").read_text().splitlines():
                    key, _, rest = line.partition(":")
                    info[key] = int(rest.split()[0]) * 1024
                total, avail = info["MemTotal"], info["MemAvailable"]
            except (OSError, KeyError, ValueError, IndexError):
                return None
        used = total - avail
        return round(used * 100 / total), used, total

    def _cpu_percent(self):
        if self.windows:
            result = self._run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average"],
                timeout=20,
            )
            return round(float(result.stdout.strip()))
        return round(os.getloadavg()[0] * 100 / (os.cpu_count() or 1))

    def _disk_info(self):
        anchor = Path(self.home.anchor or "/")
        usage = shutil.disk_usage(anchor)
        return usage.used, usage.free, usage.total

    def cmd_battery(self, m):
        return self._battery_text()

    def cmd_cpu(self, m):
        return f"CPU load is about {self._cpu_percent()} percent, Sir."

    def cmd_memory(self, m):
        info = self._memory_info()
        if not info:
            return "I could not read memory usage, Sir."
        pct, used, total = info
        return f"Memory is {pct} percent used: {format_size(used)} of {format_size(total)}, Sir."

    def cmd_disk(self, m):
        used, free, total = self._disk_info()
        return f"{format_size(free)} free of {format_size(total)} on the main drive, Sir."

    def cmd_status(self, m):
        parts = []
        try:
            parts.append(f"CPU {self._cpu_percent()} percent")
        except Exception:
            pass
        info = self._memory_info()
        if info:
            parts.append(f"memory {info[0]} percent")
        _, free, total = self._disk_info()
        parts.append(f"{format_size(free)} free disk")
        if self.windows:
            parts.append(self._battery_text().replace(", Sir.", "").replace("Battery is", "battery"))
        return "System status: " + ", ".join(parts) + ", Sir."

    def cmd_running(self, m):
        if self.windows:
            out = self._run(["tasklist", "/fo", "csv", "/nh"], timeout=20).stdout
            names = [row[0] for row in csv.reader(io.StringIO(out)) if row]
        else:
            out = self._run(["ps", "-eo", "comm="]).stdout
            names = [line.strip() for line in out.splitlines() if line.strip()]
        noise = {"system", "registry", "svchost.exe", "conhost.exe", "csrss.exe", "smss.exe",
                 "wininit.exe", "services.exe", "lsass.exe", "fontdrvhost.exe", "dwm.exe",
                 "system idle process", "memory compression", "secure system"}
        counts = {}
        for n in names:
            if n.lower() not in noise:
                counts[n] = counts.get(n, 0) + 1
        top = sorted(counts, key=lambda k: (-counts[k], k.lower()))[:12]
        return "Running: " + ", ".join(top) + ", Sir." if top else "I could not list running apps, Sir."


# ---- HTTP server ------------------------------------------------------------
def make_handler(jarvis, token, port, index_path):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "JarvisHelper"

        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, payload):
            self._send(code, json.dumps(payload))

        def _guard(self, need_token):
            if self.headers.get("Host", "") not in allowed_hosts:
                self._json(403, {"error": "bad host"})
                return False
            origin = self.headers.get("Origin")
            if origin is not None and origin not in allowed_origins:
                self._json(403, {"error": "bad origin"})
                return False
            if need_token:
                supplied = self.headers.get("X-Jarvis-Token", "")
                if not secrets.compare_digest(supplied.encode("utf-8", "replace"), token.encode()):
                    self._json(401, {"error": "bad token"})
                    return False
            return True

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                if not self._guard(False):
                    return
                try:
                    html = index_path.read_text(encoding="utf-8")
                except OSError:
                    self._json(404, {"error": "index.html not found"})
                    return
                html = html.replace(TOKEN_TAG, f'<meta name="jarvis-token" content="{token}">')
                self._send(200, html, "text/html; charset=utf-8")
            elif path == "/api/status":
                if self._guard(True):
                    self._json(200, {"ok": True, "windows": jarvis.windows})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/api/command":
                self._json(404, {"error": "not found"})
                return
            if not self._guard(True):
                return
            if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
                self._json(415, {"error": "json required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0 or length > 2048:
                self._json(413, {"error": "request too large"})
                return
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                text = payload.get("text") if isinstance(payload, dict) else None
            except ValueError:
                text = None
            if not isinstance(text, str) or not text.strip() or len(text) > 500:
                self._json(400, {"error": "send {\"text\": \"...\"} up to 500 characters"})
                return
            result = jarvis.handle(text)
            try:
                with open(HERE / "jarvis.log", "a", encoding="utf-8") as log:
                    log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{text.strip()[:200]!r}\t{result['reply'][:120]!r}\n")
            except OSError:
                pass
            self._json(200, result)

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description="MELYVN JARVIS local computer helper")
    parser.add_argument("--port", type=int, default=int(os.environ.get("JARVIS_PORT", DEFAULT_PORT)))
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    args = parser.parse_args(argv)

    token = secrets.token_urlsafe(32)
    jarvis = Jarvis()
    handler = make_handler(jarvis, token, args.port, ROOT / "index.html")
    try:
        server = ThreadingHTTPServer((HOST, args.port), handler)
    except OSError as exc:
        print(f"Could not start on port {args.port}: {exc}\nTry: python jarvis_helper.py --port 8766")
        return 1

    url = f"http://127.0.0.1:{args.port}/"
    print(f"MELYVN JARVIS helper running at {url}")
    print("Only this computer can reach it. Press Ctrl+C to stop.")
    if not jarvis.windows:
        print("Note: system controls and battery are Windows-only; other platforms have limited support.")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nJarvis helper stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
