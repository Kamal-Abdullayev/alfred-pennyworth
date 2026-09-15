# supervisor.py — keeps the right number of workers running for each role.
#
#   python supervisor.py            # replaces running daemon.py by hand
#   python supervisor.py --dry-run  # print scaling decisions, spawn nothing
#
# Per role, agents/<role>.yaml may declare:
#   workers: { min: 1, max: 3 }     # min are permanent; up to max are spawned on demand
# Global cap (settings key "max_workers", default 4) protects the seat's rate limit.
#
# Scaling rule, every poll: if open tasks for a role exceed its live workers and
# both the role max and the global cap allow it, spawn an ephemeral worker; it
# exits by itself after being idle. Permanent workers are restarted if they die.
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

import board
from daemon import configs, flow

ROOT = Path(__file__).parent
PYTHON = sys.executable
POLL_S = 5
DEFAULT_MAX_TOTAL = 4


def role_limits(cfgs):
    out = {}
    for role, path in cfgs.items():
        cfg = yaml.safe_load(open(ROOT / path)) or {}
        w = cfg.get("workers") or {}
        out[role] = {"min": int(w.get("min", 1)), "max": max(int(w.get("max", 1)), int(w.get("min", 1)))}
    return out


class Supervisor:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.procs: dict[int, dict] = {}   # pid -> {role, ephemeral, popen, started}
        self.stamp = self.source_stamp()

    def spawn(self, role, ephemeral):
        if self.dry_run:
            print(f"  would spawn {role}{' (ephemeral)' if ephemeral else ''}")
            return
        args = [PYTHON, str(ROOT / "daemon.py"), role] + (["--ephemeral"] if ephemeral else [])
        p = subprocess.Popen(args, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs[p.pid] = {"role": role, "ephemeral": ephemeral, "popen": p, "started": time.time()}
        flow(f"[supervisor] started {role} worker pid {p.pid}{' (ephemeral)' if ephemeral else ''}")

    def retire(self, pid, why):
        info = self.procs.pop(pid, None)
        if not info:
            return
        flow(f"[supervisor] retiring idle {info['role']} worker pid {pid} — {why}")
        try:
            info["popen"].terminate()
            info["popen"].wait(timeout=10)
        except Exception:
            info["popen"].kill()
        board.worker_gone(pid)

    def reap(self):
        for pid, info in list(self.procs.items()):
            if info["popen"].poll() is not None:
                flow(f"[supervisor] {info['role']} worker pid {pid} exited ({'ephemeral' if info['ephemeral'] else 'PERMANENT — will restart'})")
                del self.procs[pid]

    def source_stamp(self):
        """mtimes of the Python modules workers import; a change means running workers are stale."""
        return {f: f.stat().st_mtime for f in ROOT.glob("*.py")}

    def recycle_stale(self, live):
        """Code on disk changed (git pull, an edit): retire idle permanent workers so they come
        back on the new code. Busy workers finish their task first — they are retired on a later tick."""
        stamp = self.source_stamp()
        if stamp == self.stamp:
            return
        changed = sorted(f.name for f in stamp if stamp[f] != self.stamp.get(f))
        busy = {w["pid"] for w in live if w.get("current_task")}
        stale = [pid for pid, i in self.procs.items() if i["started"] < max(stamp.values()) and pid not in busy]
        if not stale:
            return                                   # everyone is busy; keep the old stamp and retry next tick
        for pid in stale:
            self.retire(pid, f"source changed ({', '.join(changed)[:80]}) — restarting on the new code")
        if all(i["started"] >= max(stamp.values()) for i in self.procs.values()):
            self.stamp = stamp

    def tick(self):
        self.reap()
        self.recycle_stale(board.workers())
        try:
            import importlib, schedules
            mt = (ROOT / "schedules.py").stat().st_mtime
            if getattr(self, "_sched_mtime", None) not in (None, mt):
                importlib.reload(schedules)
                flow("[schedule] schedules.py changed — reloaded")
            self._sched_mtime = mt
            schedules.tick()
        except Exception as e:  # noqa: BLE001 — a bad schedule must not stop the workers
            flow(f"[schedule] tick failed: {e!r}")
        cfgs = configs()
        limits = role_limits(cfgs)
        max_total = int(board.get_setting("max_workers", DEFAULT_MAX_TOTAL))
        live = board.workers()                       # heartbeats from every worker, ours or not
        by_role: dict[str, int] = {}
        for w in live:
            by_role[w["role"]] = by_role.get(w["role"], 0) + 1
        mine_by_role: dict[str, int] = {}
        for info in self.procs.values():
            mine_by_role[info["role"]] = mine_by_role.get(info["role"], 0) + 1
        open_by_role = board.open_count_by_role()
        total = len(live)

        for role, lim in limits.items():
            running = max(by_role.get(role, 0), mine_by_role.get(role, 0))
            # permanent floor
            while mine_by_role.get(role, 0) < lim["min"] and running < lim["min"]:
                self.spawn(role, ephemeral=False)
                mine_by_role[role] = mine_by_role.get(role, 0) + 1
                running += 1; total += 1
            # on-demand extras
            backlog = open_by_role.get(role, 0)
            while backlog > running and running < lim["max"] and total < max_total:
                self.spawn(role, ephemeral=True)
                running += 1; total += 1; backlog -= 1
            # scale DOWN: the role's min was lowered on the Agents page (or the global cap was), so
            # retire idle permanent workers above the floor — newest first, never one mid-task
            excess = mine_by_role.get(role, 0) - lim["min"]
            if excess > 0 and backlog == 0:
                busy = {w["pid"] for w in live if w.get("current_task")}
                mine = sorted((pid for pid, i in self.procs.items() if i["role"] == role and not i["ephemeral"]), reverse=True)
                for pid in mine:
                    if excess <= 0:
                        break
                    if pid in busy:
                        continue
                    self.retire(pid, f"{role} min is {lim['min']}, {mine_by_role[role]} permanent workers running")
                    mine_by_role[role] -= 1; excess -= 1; total -= 1
        return {"live": {r: by_role.get(r, 0) for r in limits}, "open": open_by_role, "limits": limits, "max_total": max_total}

    def run(self):
        board.init()
        print(f"[supervisor] managing roles: {', '.join(configs())}  (ctrl-c stops every worker)")
        try:
            while True:
                state = self.tick()
                if self.dry_run:
                    print(state); return
                time.sleep(POLL_S)
        except KeyboardInterrupt:
            pass
        finally:
            for pid, info in self.procs.items():
                info["popen"].terminate()
            for info in self.procs.values():
                try:
                    info["popen"].wait(timeout=10)
                except Exception:
                    info["popen"].kill()
            print("[supervisor] all workers stopped")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    Supervisor(dry_run=a.dry_run).run()
