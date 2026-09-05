# monitor.py — read-only view of the board. Refreshes every few seconds.
#
#   python monitor.py          # live view
#   python monitor.py --once   # single snapshot

import sys
import time

import board

STATUS_ICONS = {"open": " ", "claimed": ">", "done": "+", "failed": "x", "stuck": "!"}


def show():
    rows = board.snapshot()
    print(f"\n{'':1} {'id':8} {'chain':8} {'role':10} {'status':8} {'it':2}  title")
    print("-" * 78)
    for r in rows:
        icon = STATUS_ICONS.get(r["status"], "?")
        status = r["status"]
        print(f"{icon:1} {r['id']:8} {r['chain_id']:8} {r['role']:10} "
              f"{status[:8]:8} {r['iteration']:2}  {r['title'][:38]}")
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("-" * 78)
    print("  " + "  ".join(f"{k}:{v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    board.init()
    if "--once" in sys.argv:
        show()
    else:
        while True:
            print("\033[2J\033[H", end="")  # clear screen
            show()
            time.sleep(5)
