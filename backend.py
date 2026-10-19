"""Backend: talks to Windows through PowerShell / pnputil and to the Microsoft Update Catalog.
Only two sources of drivers are used: Windows Update and the Microsoft Update Catalog
(signed, vendor-certified). Nothing else is downloaded."""
import base64
import csv
import ctypes
import hashlib
import html as html_lib
import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).parent / "scripts"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DriverTool"
CATALOG = "https://www.catalog.update.microsoft.com"
UA = {"User-Agent": "Mozilla/5.0"}

PROBLEM_CODES = {
    1: "Устройство настроено неправильно",
    10: "Устройство не запускается",
    18: "Нужно переустановить драйверы",
    22: "Устройство отключено",
    28: "Драйверы не установлены",
    31: "Устройство работает неправильно",
    39: "Не удаётся загрузить драйвер",
    43: "Windows остановила устройство (сбой)",
    52: "Не удаётся проверить цифровую подпись",
}

VENDOR_PAGES = {
    "10DE": ("NVIDIA", "https://www.nvidia.com/Download/index.aspx"),
    "1002": ("AMD", "https://www.amd.com/en/support/download/drivers.html"),
    "1022": ("AMD", "https://www.amd.com/en/support/download/drivers.html"),
    "8086": ("Intel", "https://www.intel.com/content/www/us/en/download-center/home.html"),
    "10EC": ("Realtek", "https://www.realtek.com/Download"),
    "14C3": ("MediaTek", "https://www.mediatek.com/products/connectivity-and-networking"),
}


class Cancelled(Exception):
    pass


# ---------- cancellation / process registry ----------
_lock = threading.Lock()
_procs: set = set()
_cancel = threading.Event()


def reset_cancel() -> None:
    _cancel.clear()


def cancel_all() -> None:
    _cancel.set()
    with _lock:
        for p in list(_procs):
            try:
                p.kill()
            except OSError:
                pass


def _check_cancel() -> None:
    if _cancel.is_set():
        raise Cancelled("Операция отменена")


def _popen(cmd, **kw) -> subprocess.Popen:
    p = subprocess.Popen(cmd, creationflags=NO_WINDOW, **kw)
    with _lock:
        _procs.add(p)
    return p


def _run(cmd, timeout=None) -> tuple[int, bytes]:
    p = _popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        out, _ = p.communicate(timeout=timeout)
    finally:
        with _lock:
            _procs.discard(p)
    _check_cancel()
    return p.returncode, out or b""


# ---------- friendly errors ----------
def friendly_error(exc) -> str:
    if isinstance(exc, Cancelled):
        return str(exc)
    t = str(exc)
    low = t.lower()
    if isinstance(exc, (urllib.error.URLError, TimeoutError)) or any(
            k in low for k in ("0x8024402c", "0x80072ee7", "0x80072efd", "could not be resolved", "timed out")):
        return "Нет подключения к интернету или сервер Microsoft недоступен."
    if "0x80070422" in low:
        return "Служба Windows Update отключена. Включите службу «Центр обновления Windows» (wuauserv)."
    if "0x80070005" in low or "access is denied" in low or "отказано в доступе" in low:
        return "Недостаточно прав. Запустите программу от администратора."
    if "0x8024001e" in low:
        return "Служба обновления Windows остановлена или занята. Повторите попытку позже."
    return t or "Неизвестная ошибка"


# ---------- settings / snapshots ----------
def load_settings() -> dict:
    try:
        return json.loads((DATA_DIR / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(s: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "settings.json").write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def save_snapshot(drivers: list[dict]) -> Path:
    """Record of every driver version before a change — shows exactly what an install altered."""
    d = DATA_DIR / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    slim = [{k: x.get(k) for k in ("name", "deviceId", "provider", "version", "date", "inf")} for x in drivers]
    path.write_text(json.dumps(slim, ensure_ascii=False), encoding="utf-8")
    return path


def latest_snapshot() -> Path | None:
    d = DATA_DIR / "snapshots"
    files = sorted(d.glob("*.json")) if d.exists() else []
    return files[-1] if files else None


def diff_snapshot(path: Path, current: list[dict]) -> list[str]:
    old = {x["deviceId"]: x for x in json.loads(path.read_text(encoding="utf-8"))}
    lines = []
    for c in current:
        o = old.get(c["deviceId"])
        if o and (o["version"], o["date"]) != (c["version"], c["date"]):
            lines.append(f"{c['name']}: {o['version']} ({o['date']}) → {c['version']} ({c['date']})")
    return lines


# ---------- admin ----------
def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> None:
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, None
    else:
        exe, params = sys.executable, f'"{Path(sys.argv[0]).resolve()}"'
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)


# ---------- PowerShell helpers ----------
def _ps_cmd(script: str, *args: str) -> list[str]:
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(SCRIPTS / script), *args]


def _run_json(script: str, *args: str, timeout: int = 600):
    code, out = _run(_ps_cmd(script, *args), timeout=timeout)
    text = out.decode("utf-8", "replace").strip()
    if not text:
        raise RuntimeError("пустой ответ PowerShell")
    return json.loads(text)


def create_restore_point() -> str:
    cmd = ["powershell", "-NoProfile", "-Command",
           "Enable-ComputerRestore -Drive \"$env:SystemDrive\\\" -ErrorAction SilentlyContinue; "
           "Checkpoint-Computer -Description 'DriverTool: before driver install' -RestorePointType MODIFY_SETTINGS"]
    code, out = _run(cmd, timeout=180)
    return "Точка восстановления создана." if code == 0 else \
        "Точка восстановления не создана (Windows разрешает не чаще 1 раза в сутки)."


# ---------- devices ----------
def scan_devices() -> dict:
    data = _run_json("scan.ps1")
    for d in data.get("drivers", []):
        d["hwid"] = d.get("hwid") or []
    for p in data.get("problems", []):
        p["reason"] = PROBLEM_CODES.get(p["code"], f"Код ошибки {p['code']}")
    return data


def vendor_page(hwids: list[str]) -> tuple[str, str] | None:
    for h in hwids:
        m = re.search(r"VEN_([0-9A-F]{4})", h.upper())
        if m and m.group(1) in VENDOR_PAGES:
            return VENDOR_PAGES[m.group(1)]
    return None


def export_csv(path: Path, rows: list[list[str]], header: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(header)
        w.writerows(rows)


# ---------- Windows Update ----------
def search_updates() -> list[dict]:
    data = _run_json("search_updates.ps1", timeout=900)
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data["updates"]


def install_updates(ids: list[str], restore_point: bool, log) -> dict:
    """Streams LOG: lines to `log`; returns the RESULT dict."""
    args = ["-Ids", ",".join(ids)] + (["-RestorePoint"] if restore_point else [])
    proc = _popen(_ps_cmd("install_updates.ps1", *args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    result = {"installed": 0, "failed": 0, "reboot": False}
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("RESULT:"):
            result = json.loads(line[7:])
        elif line.startswith("LOG:"):
            log(line[4:].strip())
        elif line:
            log(line)
    code = proc.wait()
    with _lock:
        _procs.discard(proc)
    _check_cancel()
    if code != 0 and not result["installed"]:
        result["failed"] = max(result["failed"], 1)
    return result


# ---------- Microsoft Update Catalog ----------
def _http(url: str, data: bytes | None = None, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def catalog_search(hwids: list[str]) -> list[dict]:
    """Try the most specific hardware IDs first; return driver entries, newest first."""
    for hw in hwids[:5]:
        _check_cancel()
        html = _http(f"{CATALOG}/Search.aspx?q=" + urllib.parse.quote(hw)).decode("utf-8", "replace")
        out = []
        for m in re.finditer(r'<tr id="([0-9a-f-]{36})_R\d+".*?</tr>', html, re.S):
            cells = [html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c))).strip()
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", m.group(0), re.S)]
            if len(cells) < 7 or not cells[3].startswith("Drivers"):
                continue
            try:
                dt = datetime.strptime(cells[4], "%m/%d/%Y").date().isoformat()
            except ValueError:
                dt = cells[4]
            size = re.search(r"originalSize\">(\d+)<", m.group(0))
            out.append({"guid": m.group(1), "title": cells[1], "products": cells[2],
                        "class": cells[3], "date": dt, "version": cells[5],
                        "sizeMB": round(int(size.group(1)) / 1048576, 1) if size else 0})
        if out:
            out.sort(key=lambda x: x["date"], reverse=True)
            return out
    return []


def catalog_files(guid: str) -> list[dict]:
    body = urllib.parse.urlencode({"updateIDs": json.dumps(
        [{"size": 0, "languages": "", "uidInfo": guid, "updateID": guid}])}).encode()
    page = _http(f"{CATALOG}/DownloadDialog.aspx", data=body).decode("utf-8", "replace")
    files = []
    for m in re.finditer(r"files\[\d+\]\.url = '([^']+)';\s*"
                         r"downloadInformation\[\d+\]\.files\[\d+\]\.digest = '[^']*';\s*"
                         r"downloadInformation\[\d+\]\.files\[\d+\]\.sha256 = '([^']*)'", page):
        files.append({"url": m.group(1), "sha256": m.group(2)})
    return files


def _download(url: str, sha256_b64: str, dest: Path, progress) -> None:
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(256 * 1024):
            _check_cancel()
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if total:
                progress(done / total)
    if sha256_b64 and base64.b64encode(h.digest()).decode() != sha256_b64:
        dest.unlink(missing_ok=True)
        raise RuntimeError("Контрольная сумма файла не совпала — файл повреждён, установка отменена.")


def verify_signature(path: Path) -> tuple[bool, str]:
    cmd = ["powershell", "-NoProfile", "-Command",
           f"$s = Get-AuthenticodeSignature -LiteralPath '{path}'; "
           "\"$($s.Status)|$($s.SignerCertificate.Subject)\""]
    _, out = _run(cmd, timeout=120)
    status, _, subject = out.decode("utf-8", "replace").strip().partition("|")
    return status == "Valid", subject


def install_from_catalog(guid: str, title: str, log, progress) -> dict:
    files = [f for f in catalog_files(guid) if f["url"].lower().endswith(".cab")]
    if not files:
        raise RuntimeError("В каталоге нет .cab-файла для этого драйвера.")
    work = DATA_DIR / "downloads" / guid
    work.mkdir(parents=True, exist_ok=True)
    reboot = False
    for i, f in enumerate(files, 1):
        cab = work / Path(f["url"]).name
        log(f"Скачиваю {i}/{len(files)}: {cab.name}")
        _download(f["url"], f["sha256"], cab, progress)
        log("Проверяю цифровую подпись…")
        ok, subject = verify_signature(cab)
        if not ok:
            raise RuntimeError("Цифровая подпись файла недействительна — установка отменена.")
        log(f"Подпись верна: {subject.split(',')[0]}")
        out_dir = work / cab.stem
        out_dir.mkdir(exist_ok=True)
        code, out = _run(["expand.exe", "-F:*", str(cab), str(out_dir)], timeout=600)
        if code != 0:
            raise RuntimeError("Не удалось распаковать архив драйвера.")
        log("Устанавливаю…")
        code, out = _run(["pnputil", "/add-driver", str(out_dir / "*.inf"), "/subdirs", "/install"], timeout=900)
        text = out.decode("cp866", "replace")
        if code not in (0, 3010):
            raise RuntimeError(text.strip() or f"pnputil вернул код {code}")
        reboot = reboot or code == 3010
    return {"reboot": reboot, "title": title}


# ---------- backup / restore ----------
def backup_drivers(dest: Path, log) -> int:
    """Exports all third-party drivers from the driver store (pnputil)."""
    dest.mkdir(parents=True, exist_ok=True)
    code, out = _run(["pnputil", "/export-driver", "*", str(dest)])
    text = out.decode("cp866", "replace")
    log(text.strip().splitlines()[-1] if text.strip() else "")
    if code != 0:
        raise RuntimeError(text.strip() or "pnputil завершился с ошибкой")
    return sum(1 for _ in dest.glob("*/*.inf"))


def restore_drivers(src: Path, log) -> None:
    code, out = _run(["pnputil", "/add-driver", str(src / "*.inf"), "/subdirs", "/install"])
    text = out.decode("cp866", "replace")
    for line in text.strip().splitlines()[-5:]:
        log(line)
    if code not in (0, 3010):  # 3010 = success, reboot required
        raise RuntimeError(text.strip() or "pnputil завершился с ошибкой")


# ---------- driver store cleanup ----------
def stale_drivers() -> list[dict]:
    data = _run_json("stale_drivers.ps1", timeout=300)
    if isinstance(data, dict):
        if data.get("error"):
            raise RuntimeError(data["error"])
        data = [data]
    return data


def delete_drivers(published: list[str], log) -> tuple[int, int]:
    """Deletes old driver packages. Without /force pnputil refuses packages that are in use."""
    ok = bad = 0
    for name in published:
        _check_cancel()
        code, out = _run(["pnputil", "/delete-driver", name])
        if code == 0:
            ok += 1
            log(f"Удалён {name}")
        else:
            bad += 1
            log(f"Пропущен {name} (используется или защищён)")
    return ok, bad
