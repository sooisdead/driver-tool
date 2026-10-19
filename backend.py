"""Backend: talks to Windows through PowerShell / pnputil and to the Microsoft Update Catalog.
Only two sources of drivers are used: Windows Update and the Microsoft Update Catalog
(signed, vendor-certified). Vendor tools are installed only through winget, on explicit request."""
import base64
import csv
import ctypes
import hashlib
import html as html_lib
import http.client
import json
import logging
import logging.handlers
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from i18n import tr

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

# Vendor download pages by PCI vendor id (VEN_xxxx)
VENDOR_PAGES = {
    "10DE": ("NVIDIA", "https://www.nvidia.com/Download/index.aspx"),
    "1002": ("AMD", "https://www.amd.com/en/support/download/drivers.html"),
    "1022": ("AMD", "https://www.amd.com/en/support/download/drivers.html"),
    "8086": ("Intel", "https://www.intel.com/content/www/us/en/download-center/home.html"),
    "10EC": ("Realtek", "https://www.realtek.com/Download"),
    "14C3": ("MediaTek", "https://www.mediatek.com/products/connectivity-and-networking"),
    "17CB": ("Qualcomm", "https://www.qualcomm.com/support"),
    "14E4": ("Broadcom", "https://www.broadcom.com/support/download-search"),
}
# Vendor/USB ids: VID_xxxx
USB_VENDOR_PAGES = {"046D": ("Logitech", "https://support.logi.com/hc/en-us")}

# PC maker support pages (matched by the "Manufacturer" string of the computer)
OEM_PAGES = {
    "lenovo": ("Lenovo", "https://support.lenovo.com/"),
    "dell": ("Dell", "https://www.dell.com/support/home/"),
    "hp": ("HP", "https://support.hp.com/"),
    "hewlett": ("HP", "https://support.hp.com/"),
    "asus": ("ASUS", "https://www.asus.com/support/download-center/"),
    "acer": ("Acer", "https://www.acer.com/support"),
    "micro-star": ("MSI", "https://www.msi.com/support/download"),
}

# Vendor update utilities that winget actually carries (NVIDIA/AMD drivers are not in winget).
OEM_TOOLS = {
    "lenovo": ("Lenovo System Update", "Lenovo.SystemUpdate"),
    "dell": ("Dell Command | Update", "Dell.CommandUpdate"),
    "hp": ("HP Support Assistant", "HP.SupportAssistant"),
    "hewlett": ("HP Support Assistant", "HP.SupportAssistant"),
    "micro-star": ("MSI Center", "MSI.MSICenter"),
}
VENDOR_TOOLS = {"8086": ("Intel Driver & Support Assistant", "Intel.IntelDriverAndSupportAssistant")}


class Cancelled(Exception):
    pass


# ---------- logging ----------
LOG = logging.getLogger("drivertool")


def init_logging() -> None:
    if LOG.handlers:
        return
    LOG.setLevel(logging.INFO)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(DATA_DIR / "driver-tool.log", maxBytes=512 * 1024,
                                                 backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOG.addHandler(h)
    except OSError:
        LOG.addHandler(logging.NullHandler())


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
        raise Cancelled(tr("Операция отменена"))


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
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException)) or any(
            k in low for k in ("0x8024402c", "0x80072ee7", "0x80072efd", "could not be resolved", "timed out")):
        return tr("Нет подключения к интернету или сервер Microsoft недоступен.")
    if "0x80070422" in low:
        return tr("Служба Windows Update отключена. Включите службу «Центр обновления Windows» (wuauserv).")
    if "0x80070005" in low or "access is denied" in low or "отказано в доступе" in low:
        return tr("Недостаточно прав. Запустите программу от администратора.")
    if "0x8024001e" in low:
        return tr("Служба обновления Windows остановлена или занята. Повторите попытку позже.")
    return t or tr("Неизвестная ошибка")


# ---------- settings / snapshots / journal ----------
def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        LOG.exception("cannot write %s", path)


def load_settings() -> dict:
    return _read_json(DATA_DIR / "settings.json", {})


def save_settings(s: dict) -> None:
    _write_json(DATA_DIR / "settings.json", s)


def save_snapshot(drivers: list[dict]) -> Path:
    """Record of every driver version before a change — shows exactly what an install altered."""
    path = DATA_DIR / "snapshots" / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    slim = [{k: x.get(k) for k in ("name", "deviceId", "provider", "version", "date", "inf")} for x in drivers]
    _write_json(path, slim)
    return path


def latest_snapshot() -> Path | None:
    d = DATA_DIR / "snapshots"
    files = sorted(d.glob("*.json")) if d.exists() else []
    return files[-1] if files else None


def diff_snapshot(path: Path, current: list[dict]) -> list[str]:
    old = {x["deviceId"]: x for x in _read_json(path, [])}
    lines = []
    for c in current:
        o = old.get(c["deviceId"])
        if o and (o["version"], o["date"]) != (c["version"], c["date"]):
            lines.append(f"{c['name']}: {o['version']} ({o['date']}) → {c['version']} ({c['date']})")
    return lines


def journal_load() -> list[dict]:
    return _read_json(DATA_DIR / "journal.json", [])


def _journal_save(entries: list[dict]) -> None:
    _write_json(DATA_DIR / "journal.json", entries)


def published_set() -> set[str] | None:
    """Names (oemNN.inf) of all third-party driver packages in the store; None if unreadable.
    Reading C:\\Windows\\INF is instant, needs no admin rights and is locale-independent."""
    inf_dir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "INF"
    try:
        return {p.name.lower() for p in inf_dir.glob("oem*.inf")}
    except OSError:
        return None


def journaled(title: str, source: str, fn):
    """Run an install and record which driver packages it added, so it can be rolled back."""
    before = published_set()
    result = fn()
    after = published_set()
    new = sorted(set(after) - set(before)) if before is not None and after is not None else []
    entries = journal_load()
    entries.append({"id": f"{datetime.now():%Y%m%d-%H%M%S-%f}", "time": f"{datetime.now():%Y-%m-%d %H:%M}",
                    "title": title, "source": source, "packages": new, "rolled_back": False,
                    "trackable": before is not None and after is not None})
    _journal_save(entries[-200:])
    LOG.info("journal: %s (%s) packages=%s", title, source, new)
    return result


def rollback(entry_id: str, log) -> int:
    """Removes the packages an install added; Windows falls back to the previous driver, which
    stays in the store. Returns number of packages removed."""
    entries = journal_load()
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if not entry:
        raise RuntimeError(tr("Запись журнала не найдена."))
    if not entry["packages"]:
        raise RuntimeError(tr("Для этой записи нет данных об установленных пакетах — откат невозможен."))
    removed = 0
    for pkg in entry["packages"]:
        _check_cancel()
        code, out = _run(["pnputil", "/delete-driver", pkg, "/uninstall", "/force"])
        if code in (0, 3010):
            removed += 1
            log(tr("Удалён пакет {pkg}", pkg=pkg))
        else:
            log(tr("Не удалось удалить {pkg}", pkg=pkg))
    _run(["pnputil", "/scan-devices"])
    entry["rolled_back"] = removed > 0
    _journal_save(entries)
    LOG.info("rollback %s removed=%d", entry_id, removed)
    return removed


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
        raise RuntimeError(tr("пустой ответ PowerShell"))
    return json.loads(text)


def create_restore_point() -> str:
    cmd = ["powershell", "-NoProfile", "-Command",
           "Enable-ComputerRestore -Drive \"$env:SystemDrive\\\" -ErrorAction SilentlyContinue; "
           "Checkpoint-Computer -Description 'DriverTool: before driver install' -RestorePointType MODIFY_SETTINGS"]
    code, out = _run(cmd, timeout=180)
    return tr("Точка восстановления создана.") if code == 0 else \
        tr("Точка восстановления не создана (Windows разрешает не чаще 1 раза в сутки).")


# ---------- devices ----------
def scan_devices() -> dict:
    data = _run_json("scan.ps1")
    for d in data.get("drivers", []):
        d["hwid"] = d.get("hwid") or []
    for p in data.get("problems", []):
        p["reason"] = tr(PROBLEM_CODES[p["code"]]) if p["code"] in PROBLEM_CODES \
            else tr("Код ошибки {code}", code=p["code"])
    LOG.info("scan: %d drivers, %d problems", len(data.get("drivers", [])), len(data.get("problems", [])))
    return data


def _hw_vendor(hwids: list[str]) -> tuple[str, str] | None:
    for h in hwids:
        u = h.upper()
        m = re.search(r"VEN_([0-9A-F]{4})", u)
        if m and m.group(1) in VENDOR_PAGES:
            return VENDOR_PAGES[m.group(1)]
        m = re.search(r"VID_([0-9A-F]{4})", u)
        if m and m.group(1) in USB_VENDOR_PAGES:
            return USB_VENDOR_PAGES[m.group(1)]
    return None


def system_manufacturer() -> str:
    try:
        code, out = _run(["powershell", "-NoProfile", "-Command",
                          "(Get-CimInstance Win32_ComputerSystem).Manufacturer"], timeout=60)
        return out.decode("utf-8", "replace").strip()
    except Exception:  # noqa: BLE001
        return ""


def _match_maker(table: dict, maker: str):
    low = maker.lower()
    return next((v for k, v in table.items() if k in low), None)


def support_links(hwids: list[str], maker: str) -> list[tuple[str, str]]:
    """Official pages worth opening for this device: chip vendor first, then the PC maker."""
    links = []
    v = _hw_vendor(hwids)
    if v:
        links.append(v)
    oem = _match_maker(OEM_PAGES, maker)
    if oem and oem not in links:
        links.append(oem)
    return links


def suggested_tools(hwids: list[str], maker: str) -> list[tuple[str, str]]:
    """(name, winget id) of vendor update utilities that fit this device / PC."""
    tools = []
    for h in hwids:
        m = re.search(r"VEN_([0-9A-F]{4})", h.upper())
        if m and m.group(1) in VENDOR_TOOLS and VENDOR_TOOLS[m.group(1)] not in tools:
            tools.append(VENDOR_TOOLS[m.group(1)])
    oem = _match_maker(OEM_TOOLS, maker)
    if oem and oem not in tools:
        tools.append(oem)
    return tools


def winget_install(pkg_id: str, log) -> None:
    if not shutil.which("winget"):
        raise RuntimeError(tr("winget не найден. Установите «Установщик приложений» из Microsoft Store."))
    proc = _popen(["winget", "install", "--id", pkg_id, "--exact", "--silent",
                   "--accept-package-agreements", "--accept-source-agreements"],
                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").strip()
        if line and not set(line) <= set("-\\|/ ▒█"):
            log(line)
    code = proc.wait()
    with _lock:
        _procs.discard(proc)
    _check_cancel()
    if code != 0:
        raise RuntimeError(tr("winget завершился с кодом {code}", code=code))


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


# ---------- version logic ----------
def vtuple(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:4])


def is_newer(avail_ver: str, avail_date: str, inst_ver: str, inst_date: str) -> bool:
    """A candidate counts as an upgrade only if its version is higher AND it is not older by date,
    so a vendor's older-but-higher-numbered package can never downgrade a driver. If either version
    is unreadable (the catalog sometimes says "n/a") nothing is suggested: a date alone is not evidence."""
    a, i = vtuple(avail_ver), vtuple(inst_ver)
    if not a or not i:
        return False
    if inst_date and avail_date and avail_date < inst_date:
        return False
    return a > i


def os_ok(products: str) -> bool:
    """Does a catalog entry apply to this Windows version / CPU architecture?"""
    p = products
    if "ARM64" in p and "ARM" not in platform.machine().upper():
        return False
    win11 = sys.getwindowsversion().build >= 22000
    if win11:
        return "Windows 11" in p or ("Windows 10" in p and "ater" in p)
    return "Windows 10" in p


# ---------- Microsoft Update Catalog ----------
def _http(url: str, data: bytes | None = None, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, data=data, headers=UA)
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as e:
            last = e
            _check_cancel()
            time.sleep(1.5 * (attempt + 1))
    raise last


def catalog_search(hwids: list[str], limit: int = 5, os_filter: bool = True) -> list[dict]:
    """Try the most specific hardware IDs first; return driver entries, newest first."""
    for hw in hwids[:limit]:
        _check_cancel()
        html = _http(f"{CATALOG}/Search.aspx?q=" + urllib.parse.quote(hw)).decode("utf-8", "replace")
        out = []
        for m in re.finditer(r'<tr id="([0-9a-f-]{36})_R\d+".*?</tr>', html, re.S):
            cells = [html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c))).strip()
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", m.group(0), re.S)]
            if len(cells) < 7 or not cells[3].startswith("Drivers"):
                continue
            if os_filter and not os_ok(cells[2]):
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


def auto_scan(drivers: list[dict], progress, log, include_ms: bool = False) -> list[dict]:
    """Looks every real hardware device up in the catalog and keeps only genuine upgrades."""
    todo, seen = [], set()
    for d in drivers:
        hw = d.get("hwid") or []
        is_ms = (d.get("provider") or "").lower().startswith("microsoft")
        if not hw or (is_ms and not include_ms) or d["deviceId"].upper().startswith(("SWD\\", "ROOT\\")):
            continue
        if hw[0] in seen:
            continue
        seen.add(hw[0])
        todo.append(d)
    found, fails = [], 0
    for i, d in enumerate(todo, 1):
        _check_cancel()
        progress(i / max(1, len(todo)))
        try:
            results = catalog_search(d["hwid"], limit=3)
            fails = 0
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            fails += 1
            if fails >= 3:
                raise
            continue
        best = next((r for r in results if is_newer(r["version"], r["date"], d["version"], d["date"])), None)
        if best:
            log(tr("Есть новее: {name} → {ver}", name=d["name"], ver=best["version"]))
            found.append({"name": d["name"], "deviceId": d["deviceId"], "hwid": d["hwid"],
                          "installed": d["version"], "installed_date": d["date"], **best})
        time.sleep(0.3)
    LOG.info("auto_scan: checked %d, upgrades %d", len(todo), len(found))
    return found


def _check_disk(need_bytes: int) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(DATA_DIR).free
    need = need_bytes * 3 + 200 * 1048576  # cab + unpacked files + driver store copy
    if free < need:
        raise RuntimeError(tr("Недостаточно места на диске: нужно около {need} МБ, свободно {free} МБ.",
                              need=need // 1048576, free=free // 1048576))


def _file_sha256_b64(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode()


def _download(url: str, sha256_b64: str, dest: Path, progress, attempts: int = 4) -> None:
    """Resumable download: a dropped connection continues from the .part file."""
    part = dest.with_name(dest.name + ".part")
    for attempt in range(attempts):
        try:
            start = part.stat().st_size if part.exists() else 0
            headers = dict(UA)
            if start:
                headers["Range"] = f"bytes={start}-"
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=40) as r:
                resumed = bool(start) and r.status == 206
                if not resumed:
                    start = 0
                total = start + int(r.headers.get("Content-Length") or 0)
                done = start
                with open(part, "ab" if resumed else "wb") as f:
                    while chunk := r.read(256 * 1024):
                        _check_cancel()
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            progress(done / total)
                if total and done < total:  # server closed the connection early: resume from the .part file
                    raise ConnectionError("incomplete download")
            break
        except urllib.error.HTTPError as e:
            if e.code == 416:  # range past the end — restart cleanly
                part.unlink(missing_ok=True)
                continue
            if attempt == attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, OSError):
            _check_cancel()
            if attempt == attempts - 1:
                raise
        time.sleep(2 ** attempt)
    part.replace(dest)
    if sha256_b64 and _file_sha256_b64(dest) != sha256_b64:
        dest.unlink(missing_ok=True)
        raise RuntimeError(tr("Контрольная сумма файла не совпала — файл повреждён, установка отменена."))


def verify_signature(path: Path) -> tuple[bool, str]:
    cmd = ["powershell", "-NoProfile", "-Command",
           f"$s = Get-AuthenticodeSignature -LiteralPath '{path}'; "
           "\"$($s.Status)|$($s.SignerCertificate.Subject)\""]
    _, out = _run(cmd, timeout=120)
    status, _, subject = out.decode("utf-8", "replace").strip().partition("|")
    return status == "Valid", subject


def install_from_catalog(guid: str, title: str, size_mb: float, log, progress) -> dict:
    files = [f for f in catalog_files(guid) if f["url"].lower().endswith(".cab")]
    if not files:
        raise RuntimeError(tr("В каталоге нет .cab-файла для этого драйвера."))
    _check_disk(int(size_mb * 1048576))
    work = DATA_DIR / "downloads" / guid
    work.mkdir(parents=True, exist_ok=True)
    reboot = False
    for i, f in enumerate(files, 1):
        cab = work / Path(f["url"]).name
        log(tr("Скачиваю {i}/{n}: {name}", i=i, n=len(files), name=cab.name))
        _download(f["url"], f["sha256"], cab, progress)
        log(tr("Проверяю цифровую подпись…"))
        ok, subject = verify_signature(cab)
        if not ok:
            raise RuntimeError(tr("Цифровая подпись файла недействительна — установка отменена."))
        log(tr("Подпись верна: {who}", who=subject.split(",")[0]))
        out_dir = work / cab.stem
        out_dir.mkdir(exist_ok=True)
        code, out = _run(["expand.exe", "-F:*", str(cab), str(out_dir)], timeout=600)
        if code != 0:
            raise RuntimeError(tr("Не удалось распаковать архив драйвера."))
        log(tr("Устанавливаю…"))
        code, out = _run(["pnputil", "/add-driver", str(out_dir / "*.inf"), "/subdirs", "/install"], timeout=900)
        text = out.decode("cp866", "replace")
        if code not in (0, 3010):
            raise RuntimeError(text.strip() or tr("pnputil вернул код {code}", code=code))
        reboot = reboot or code == 3010
    shutil.rmtree(work, ignore_errors=True)  # keep the disk clean after a successful install
    LOG.info("catalog install ok: %s", title)
    return {"reboot": reboot, "title": title}


# ---------- backup / restore ----------
def backup_drivers(dest: Path, log) -> int:
    """Exports all third-party drivers from the driver store (pnputil)."""
    dest.mkdir(parents=True, exist_ok=True)
    code, out = _run(["pnputil", "/export-driver", "*", str(dest)])
    text = out.decode("cp866", "replace")
    log(text.strip().splitlines()[-1] if text.strip() else "")
    if code != 0:
        raise RuntimeError(text.strip() or tr("pnputil завершился с ошибкой"))
    return sum(1 for _ in dest.glob("*/*.inf"))


def restore_drivers(src: Path, log) -> None:
    code, out = _run(["pnputil", "/add-driver", str(src / "*.inf"), "/subdirs", "/install"])
    text = out.decode("cp866", "replace")
    for line in text.strip().splitlines()[-5:]:
        log(line)
    if code not in (0, 3010):  # 3010 = success, reboot required
        raise RuntimeError(text.strip() or tr("pnputil завершился с ошибкой"))


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
            log(tr("Удалён {name}", name=name))
        else:
            bad += 1
            log(tr("Пропущен {name} (используется или защищён)", name=name))
    return ok, bad
