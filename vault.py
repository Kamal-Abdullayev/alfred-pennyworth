# vault.py — connector credentials live in the macOS Keychain, not in SQLite,
# YAML or .env. One generic-password item per (connector, variable).
#
# Trade-off: `security add-generic-password -w <value>` exposes the value in the
# process list for the milliseconds the command runs. Acceptable for a
# single-user laptop tool; the alternative (a 0600 JSON file) is weaker at rest.
import subprocess

SERVICE = "alfred-mcp"


def _account(connector: str, var: str) -> str:
    return f"{connector}/{var}"


def set_secret(connector: str, var: str, value: str) -> None:
    subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE,
                    "-a", _account(connector, var), "-w", value],
                   check=True, capture_output=True, text=True)


def get_secret(connector: str, var: str) -> str | None:
    r = subprocess.run(["security", "find-generic-password", "-s", SERVICE,
                        "-a", _account(connector, var), "-w"],
                       capture_output=True, text=True)
    return r.stdout.rstrip("\n") if r.returncode == 0 else None


def has_secret(connector: str, var: str) -> bool:
    return get_secret(connector, var) is not None


def delete_secret(connector: str, var: str) -> None:
    subprocess.run(["security", "delete-generic-password", "-s", SERVICE,
                    "-a", _account(connector, var)], capture_output=True, text=True)
