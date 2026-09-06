# vault.py — connector credentials live in the macOS Keychain, not in SQLite,
# YAML or .env. One generic-password item per (connector, variable).
#
# Trade-off: `security add-generic-password -w <value>` exposes the value in the
# process list for the milliseconds the command runs. Acceptable for a
# single-user laptop tool; the alternative (a 0600 JSON file) is weaker at rest.
# Portability: on Linux/Windows (no `security` CLI) secrets go to data/secrets.json with
# mode 0600 — weaker at rest than a keychain, but the tool stays usable. ALFRED_VAULT=file
# forces the file store on macOS too (e.g. CI, or a machine where Keychain prompts annoy).
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SERVICE = "alfred-mcp"
FILE = Path(os.environ.get("ALFRED_VAULT_FILE") or Path(__file__).resolve().parent / "data" / "secrets.json")
USE_KEYCHAIN = sys.platform == "darwin" and shutil.which("security") and os.environ.get("ALFRED_VAULT", "") != "file"


def backend() -> str:
    return "keychain" if USE_KEYCHAIN else f"file:{FILE}"


def _account(connector: str, var: str) -> str:
    return f"{connector}/{var}"


def _load() -> dict:
    try:
        return json.loads(FILE.read_text())
    except Exception:
        return {}


def _save(d: dict) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(FILE)


def set_secret(connector: str, var: str, value: str) -> None:
    if USE_KEYCHAIN:
        subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE,
                        "-a", _account(connector, var), "-w", value],
                       check=True, capture_output=True, text=True)
        return
    d = _load()
    d[_account(connector, var)] = value
    _save(d)


def get_secret(connector: str, var: str) -> str | None:
    if USE_KEYCHAIN:
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICE,
                            "-a", _account(connector, var), "-w"],
                           capture_output=True, text=True)
        return r.stdout.rstrip("\n") if r.returncode == 0 else None
    return _load().get(_account(connector, var))


def has_secret(connector: str, var: str) -> bool:
    return get_secret(connector, var) is not None


def delete_secret(connector: str, var: str) -> None:
    if USE_KEYCHAIN:
        subprocess.run(["security", "delete-generic-password", "-s", SERVICE,
                        "-a", _account(connector, var)], capture_output=True, text=True)
        return
    d = _load()
    if d.pop(_account(connector, var), None) is not None:
        _save(d)
