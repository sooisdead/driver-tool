import base64
import hashlib
import http.server
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))
import backend  # noqa: E402
import i18n  # noqa: E402


class RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves DATA, supports Range, and drops the first connection halfway to test resuming."""
    DATA = bytes(range(256)) * 4096  # 1 MiB
    drops = 1

    def do_GET(self):
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
        body = self.DATA[start:]
        self.send_response(206 if rng else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if RangeHandler.drops > 0 and not rng:
            RangeHandler.drops -= 1
            self.wfile.write(body[: len(body) // 2])
            self.wfile.flush()
            self.connection.close()  # connection dies mid-transfer
            return
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class VersionLogic(unittest.TestCase):
    def test_upgrade_needs_higher_version_and_not_older_date(self):
        self.assertTrue(backend.is_newer("32.0.16.1088", "2026-07-21", "32.0.15.7602", "2025-01-01"))
        self.assertFalse(backend.is_newer("32.0.15.7602", "2026-07-21", "32.0.15.7602", "2025-01-01"))
        # higher number but older date -> never a downgrade
        self.assertFalse(backend.is_newer("40.0.0.1", "2020-01-01", "32.0.0.1", "2025-01-01"))
        # lower version, newer date -> not an upgrade
        self.assertFalse(backend.is_newer("10.0.0.1", "2026-01-01", "32.0.0.1", "2025-01-01"))

    def test_unreadable_version_never_suggests_an_update(self):
        self.assertFalse(backend.is_newer("n/a", "2026-01-01", "1.0.0.7", "2025-01-01"))
        self.assertFalse(backend.is_newer("2.0.0.1", "2026-01-01", "", "2025-01-01"))

    def test_os_filter(self):
        with mock.patch.object(backend.sys, "getwindowsversion", create=True, return_value=mock.Mock(build=26100)):
            self.assertTrue(backend.os_ok("Windows 11 Client, version 25H2 and later, Servicing Drivers"))
            self.assertTrue(backend.os_ok("Windows 10 and later x64 Drivers"))
            self.assertFalse(backend.os_ok("Windows 7 Drivers"))
        with mock.patch.object(backend.sys, "getwindowsversion", create=True, return_value=mock.Mock(build=19045)):
            self.assertFalse(backend.os_ok("Windows 11 Client, version 25H2 and later"))
            self.assertTrue(backend.os_ok("Windows 10 and later x64 Drivers"))


class Download(unittest.TestCase):
    def test_resume_after_dropped_connection_and_hash_check(self):
        RangeHandler.drops = 1
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}/f.cab"
            sha = base64.b64encode(hashlib.sha256(RangeHandler.DATA).digest()).decode()
            d = Path(tempfile.mkdtemp()) / "f.cab"
            with mock.patch.object(backend.time, "sleep"):
                backend._download(url, sha, d, lambda x: None)
            self.assertEqual(d.read_bytes(), RangeHandler.DATA)
            with mock.patch.object(backend.time, "sleep"), self.assertRaises(RuntimeError):
                backend._download(url, "AAAA", d, lambda x: None)
            self.assertFalse(d.exists())  # corrupted file is removed
        finally:
            srv.shutdown()


class Misc(unittest.TestCase):
    def test_diff_snapshot(self):
        p = Path(tempfile.mkdtemp()) / "s.json"
        p.write_text(json.dumps([{"name": "GPU", "deviceId": "A", "version": "1.0", "date": "2020-01-01"}]))
        cur = [{"name": "GPU", "deviceId": "A", "version": "2.0", "date": "2026-01-01"}]
        self.assertEqual(len(backend.diff_snapshot(p, cur)), 1)
        self.assertEqual(backend.diff_snapshot(p, [{**cur[0], "version": "1.0", "date": "2020-01-01"}]), [])

    def test_friendly_errors(self):
        i18n.set_lang("ru")
        self.assertIn("администратора", backend.friendly_error(Exception("Access is denied")))
        self.assertIn("wuauserv", backend.friendly_error(Exception("0x80070422")))
        i18n.set_lang("en")
        self.assertIn("administrator", backend.friendly_error(Exception("Access is denied")))
        i18n.set_lang("ru")

    def test_support_links_and_tools(self):
        links = backend.support_links(["PCI\\VEN_10DE&DEV_2504"], "LENOVO")
        self.assertEqual([n for n, _ in links], ["NVIDIA", "Lenovo"])
        tools = backend.suggested_tools(["PCI\\VEN_8086&DEV_1"], "Dell Inc.")
        self.assertEqual([i for _, i in tools], ["Intel.IntelDriverAndSupportAssistant", "Dell.CommandUpdate"])

    def test_journal_records_new_packages(self):
        tmp = Path(tempfile.mkdtemp())
        sets = iter([{"oem1.inf"}, {"oem1.inf", "oem7.inf"}])
        with mock.patch.object(backend, "DATA_DIR", tmp), mock.patch.object(backend, "published_set", lambda: next(sets)):
            backend.journaled("GPU driver", "test", lambda: "done")
            entry = backend.journal_load()[0]
        self.assertEqual(entry["packages"], ["oem7.inf"])

    def test_translations_have_same_placeholders(self):
        import re
        for ru, en in i18n.EN.items():
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ru)), sorted(re.findall(r"\{(\w+)\}", en)), ru)


if __name__ == "__main__":
    unittest.main()
