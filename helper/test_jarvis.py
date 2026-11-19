"""Tests for the Jarvis helper. Run from the helper folder:  python -m unittest -v"""

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import jarvis_helper as jh

CONFIG = {
    "apps": {"chrome": "chrome", "notepad": "notepad", "mt5": "C:\\Nope\\terminal64.exe"},
    "sites": {"youtube": "https://www.youtube.com"},
}


class FakeJarvis(jh.Jarvis):
    """Records what would have happened instead of touching the computer."""

    def __init__(self, home, windows=True):
        super().__init__(config=CONFIG, windows=windows, home=home)
        self.opened, self.urls, self.runs, self.keys = [], [], [], []

    def _startfile(self, target):
        self.opened.append(target)

    def _open_url(self, url):
        self.urls.append(url)

    def _run(self, args, timeout=15):
        self.runs.append(args)

        class R:
            returncode, stdout, stderr = 0, "", ""

        return R()

    def _press_key(self, vk, times=1):
        self.keys.append((vk, times))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        for name in jh.FOLDERS:
            (self.home / name.capitalize()).mkdir()
        self.j = FakeJarvis(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def say(self, text):
        return self.j.handle(text)["reply"]

    def test_normalize(self):
        self.assertEqual(jh.normalize("Hey Jarvis, please open Chrome, sir!"), "open chrome")
        self.assertEqual(jh.normalize("  Could you   open   notepad? "), "open notepad")

    def test_open_app_from_config_only(self):
        self.assertIn("Opening chrome", self.say("open chrome"))
        self.assertEqual(self.j.opened, ["chrome"])
        reply = self.say("open powershell")
        self.assertIn("do not have an app", reply)
        self.assertEqual(self.j.opened, ["chrome"])  # nothing new launched

    def test_missing_absolute_path_is_reported(self):
        self.assertIn("could not find", self.say("open mt5"))
        self.assertEqual(self.j.opened, [])

    def test_typed_text_is_never_run_as_a_command(self):
        for text in ["open chrome && calc", "run cmd /c del *", "open ; shutdown /s"]:
            self.say(text)
        self.assertEqual(self.j.runs, [])
        self.assertTrue(all(o in ("chrome",) for o in self.j.opened))

    def test_websites(self):
        self.say("open youtube")
        self.say("go to example.com")
        self.say("search for gold price today")
        self.assertEqual(self.j.urls[0], "https://www.youtube.com")
        self.assertEqual(self.j.urls[1], "https://example.com")
        self.assertTrue(self.j.urls[2].startswith("https://www.google.com/search?q=gold+price"))

    def test_rejects_bad_urls(self):
        self.assertIn("does not look like", self.say("go to javascript:alert(1)"))
        self.assertIn("does not look like", self.say("go to file:///c:/windows"))
        self.assertEqual(self.j.urls, [])

    def test_create_folder(self):
        self.assertIn("Created", self.say("create folder Trading Notes"))
        self.assertTrue((self.home / "Desktop" / "Trading Notes").is_dir())
        self.assertIn("already exists", self.say("create folder Trading Notes"))
        self.assertIn("Created", self.say("make a new folder reports in documents"))
        self.assertTrue((self.home / "Documents" / "reports").is_dir())

    def test_create_folder_rejects_paths(self):
        for bad in ["..", "../evil", "a/b", "C:\\x", "x:y"]:
            self.assertNotIn("Created", self.say(f"create folder {bad}"))
        self.assertIn("letters, numbers", self.say("create folder ../evil"))
        self.assertEqual(sorted(p.name for p in (self.home / "Desktop").iterdir()), [])

    def test_find_and_open_result(self):
        (self.home / "Documents" / "Invoice March.pdf").write_text("x")
        (self.home / "Downloads" / "invoice april.pdf").write_text("x")
        reply = self.say("find invoice")
        self.assertIn("found 2", reply)
        self.assertIn("open result", self.say("find invoice"))
        self.say("open result 1")
        self.assertEqual(len(self.j.opened), 1)
        self.assertIn("no such result", self.say("open result 9"))

    def test_find_in_one_folder(self):
        (self.home / "Documents" / "plan.txt").write_text("x")
        (self.home / "Downloads" / "plan.docx").write_text("x")
        self.assertIn("found 1", self.say("find plan in downloads"))

    def test_open_file_single_match_opens(self):
        (self.home / "Documents" / "unique-report.pdf").write_text("x")
        self.assertIn("Opening unique-report.pdf", self.say("open file unique-report"))
        self.assertEqual(len(self.j.opened), 1)

    def test_blocked_extensions_are_not_opened(self):
        (self.home / "Downloads" / "setup.exe").write_text("x")
        (self.home / "Downloads" / "run.ps1").write_text("x")
        self.say("find setup")
        self.assertIn("will not open", self.say("open result 1"))
        self.assertIn("will not open", self.say("open file run.ps1"))
        self.assertEqual(self.j.opened, [])

    def test_cannot_open_outside_allowed_folders(self):
        outside = Path(self.tmp.name) / "secret.txt"
        outside.write_text("x")
        with self.assertRaises(RuntimeError):
            self.j._open_path(outside)

    def test_symlink_escape_is_blocked(self):
        target = Path(tempfile.mkdtemp())
        (target / "private.txt").write_text("x")
        link = self.home / "Documents" / "link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        with self.assertRaises(RuntimeError):
            self.j._open_path(link / "private.txt")

    def test_list_folder(self):
        (self.home / "Downloads" / "a.txt").write_text("x")
        self.assertIn("a.txt", self.say("list downloads"))
        self.assertIn("empty", self.say("show my pictures"))

    def test_volume_and_mute(self):
        self.say("volume up")
        self.say("turn the volume down")
        self.say("mute")
        self.assertEqual(self.j.keys, [(jh.VK_VOLUME_UP, 5), (jh.VK_VOLUME_DOWN, 5), (jh.VK_VOLUME_MUTE, 1)])

    def test_lock_runs_immediately(self):
        self.say("lock the computer")
        self.assertEqual(self.j.runs, [["rundll32.exe", "user32.dll,LockWorkStation"]])

    def test_shutdown_needs_confirmation(self):
        self.assertIn("confirm", self.say("shut down the computer"))
        self.assertEqual(self.j.runs, [])
        self.assertIn("30 seconds", self.say("confirm"))
        self.assertEqual(self.j.runs[0][:3], ["shutdown", "/s", "/t"])

    def test_confirm_without_pending_does_nothing(self):
        self.assertIn("Nothing is waiting", self.say("yes"))
        self.assertEqual(self.j.runs, [])

    def test_cancel_clears_pending(self):
        self.say("restart")
        self.assertIn("Cancelled", self.say("cancel"))
        self.say("confirm")
        self.assertEqual(self.j.runs, [])

    def test_pending_expires(self):
        self.say("sleep")
        self.j.pending = (self.j.pending[0], time.time() - 1)
        self.assertIn("Nothing is waiting", self.say("confirm"))
        self.assertEqual(self.j.runs, [])

    def test_abort_shutdown(self):
        self.say("cancel shutdown")
        self.assertEqual(self.j.runs, [["shutdown", "/a"]])

    def test_windows_only_controls_refuse_elsewhere(self):
        j = FakeJarvis(self.home, windows=False)
        reply = j.handle("shut down")["reply"]
        self.assertIn("only available on Windows", reply)
        self.assertIsNone(j.pending)
        self.assertEqual(j.keys, [])

    def test_unknown_command(self):
        result = self.j.handle("make me a sandwich")
        self.assertFalse(result["ok"])
        self.assertIn("help", result["reply"])

    def test_help_and_time(self):
        self.assertIn("open apps", self.say("help"))
        self.assertIn(":", self.say("what time is it"))
        self.assertIn("Today is", self.say("what's the date"))

    def test_disk_works(self):
        self.assertIn("free of", self.say("disk space"))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        home = Path(cls.tmp.name)
        for name in jh.FOLDERS:
            (home / name.capitalize()).mkdir()
        cls.index = home / "index.html"
        cls.index.write_text(f"<html><head>{jh.TOKEN_TAG}</head><body>hi</body></html>")
        cls.token = "test-token-123"
        cls.jarvis = FakeJarvis(home)
        probe = ThreadingHTTPServer(("127.0.0.1", 0), None)
        cls.port = probe.server_address[1]
        probe.server_close()
        handler = jh.make_handler(cls.jarvis, cls.token, cls.port, cls.index)
        cls.server = ThreadingHTTPServer(("127.0.0.1", cls.port), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode(), dict(resp.headers)
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode(), dict(err.headers)

    def command(self, text, token=None, extra=None):
        headers = {"Content-Type": "application/json", "X-Jarvis-Token": self.token if token is None else token}
        headers.update(extra or {})
        return self.request("POST", "/api/command", {"text": text}, headers)

    def test_page_gets_token_injected(self):
        code, body, headers = self.request("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(f'content="{self.token}"', body)
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_command_works_with_token(self):
        code, body, _ = self.command("hello")
        self.assertEqual(code, 200)
        self.assertIn("At your service", json.loads(body)["reply"])

    def test_missing_or_wrong_token_rejected(self):
        self.assertEqual(self.command("hello", token="wrong")[0], 401)
        code, _, _ = self.request("POST", "/api/command", {"text": "hello"}, {"Content-Type": "application/json"})
        self.assertEqual(code, 401)
        self.assertEqual(self.request("GET", "/api/status")[0], 401)
        self.assertEqual(self.request("GET", "/api/status", None, {"X-Jarvis-Token": self.token})[0], 200)

    def test_foreign_host_rejected(self):
        # DNS-rebinding style request: right IP, wrong Host header
        self.assertEqual(self.command("hello", extra={"Host": "evil.example"})[0], 403)

    def test_foreign_origin_rejected(self):
        self.assertEqual(self.command("hello", extra={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.command("hello", extra={"Origin": self.base})[0], 200)

    def test_requires_json_content_type(self):
        code, _, _ = self.request("POST", "/api/command", {"text": "hello"},
                                  {"Content-Type": "text/plain", "X-Jarvis-Token": self.token})
        self.assertEqual(code, 415)

    def test_bad_bodies(self):
        h = {"Content-Type": "application/json", "X-Jarvis-Token": self.token}
        self.assertEqual(self.request("POST", "/api/command", {"nope": 1}, h)[0], 400)
        self.assertEqual(self.request("POST", "/api/command", {"text": "x" * 600}, h)[0], 400)
        self.assertEqual(self.request("POST", "/api/command", {"text": "y" * 3000}, h)[0], 413)

    def test_unknown_paths(self):
        self.assertEqual(self.request("GET", "/etc/passwd")[0], 404)
        self.assertEqual(self.request("GET", "/../helper/jarvis_helper.py")[0], 404)
        self.assertEqual(self.request("POST", "/other", {}, {"Content-Type": "application/json"})[0], 404)


if __name__ == "__main__":
    unittest.main()
