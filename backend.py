"""Backend: talks to Windows through PowerShell / pnputil. No network calls of its own —
driver updates come only from Windows Update (signed, vendor-certified)."""
import ctypes
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).parent / "scripts"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

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


def _ps_cmd(script: str, *args: str) -> list[str]:
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(SCRIPTS / script), *args]


def _run_json(script: str, *args: str, timeout: int = 600) -> dict:
    p = subprocess.run(_ps_cmd(script, *args), capture_output=True, timeout=timeout,
                       creationflags=NO_WINDOW)
    out = p.stdout.decode("utf-8", "replace").strip()
    if not out:
        raise RuntimeError(p.stderr.decode("utf-8", "replace").strip() or "пустой ответ PowerShell")
    return json.loads(out)


def scan_devices() -> dict:
    data = _run_json("scan.ps1")
    for d in data.get("drivers", []):
        d["hwid"] = d.get("hwid") or []
    for p in data.get("problems", []):
        p["reason"] = PROBLEM_CODES.get(p["code"], f"Код ошибки {p['code']}")
    return data


def search_updates() -> list[dict]:
    data = _run_json("search_updates.ps1", timeout=900)
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data["updates"]


def install_updates(ids: list[str], restore_point: bool, log) -> dict:
    """Streams LOG: lines to `log`; returns the RESULT dict."""
    args = ["-Ids", ",".join(ids)] + (["-RestorePoint"] if restore_point else [])
    proc = subprocess.Popen(_ps_cmd("install_updates.ps1", *args), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    result = {"installed": 0, "failed": 0, "reboot": False}
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("RESULT:"):
            result = json.loads(line[7:])
        elif line.startswith("LOG:"):
            log(line[4:].strip())
        elif line:
            log(line)
    if proc.wait() != 0 and not result["installed"]:
        result["failed"] = max(result["failed"], 1)
    return result


def backup_drivers(dest: Path, log) -> int:
    """Exports all third-party drivers from the driver store (pnputil)."""
    dest.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(["pnputil", "/export-driver", "*", str(dest)], capture_output=True,
                       creationflags=NO_WINDOW)
    text = p.stdout.decode("cp866", "replace")
    log(text.strip().splitlines()[-1] if text.strip() else "")
    if p.returncode != 0:
        raise RuntimeError(text.strip() or "pnputil завершился с ошибкой")
    return sum(1 for _ in dest.glob("*/*.inf"))


def restore_drivers(src: Path, log) -> None:
    p = subprocess.run(["pnputil", "/add-driver", str(src / "*.inf"), "/subdirs", "/install"],
                       capture_output=True, creationflags=NO_WINDOW)
    text = p.stdout.decode("cp866", "replace")
    for line in text.strip().splitlines()[-5:]:
        log(line)
    # 3010 = success, reboot required
    if p.returncode not in (0, 3010):
        raise RuntimeError(text.strip() or "pnputil завершился с ошибкой")
