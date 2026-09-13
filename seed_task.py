# seed_task.py — drop a new job onto the board for the team lead.
#
#   python seed_task.py "Add rate limiting to the payments API" \
#       --project ~/Desktop/projects/payments-service
#
# Without --project the agents work in <project>/workspace/ (safe sandbox).

import argparse
from pathlib import Path

import board

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("job", help="what the team should build or fix")
    p.add_argument("--project", help="absolute path to the repo/microservice to work in")
    args = p.parse_args()

    project_dir = None
    if args.project:
        project_dir = str(Path(args.project).expanduser().resolve())
        if not Path(project_dir).is_dir():
            raise SystemExit(f"not a directory: {project_dir}")

    board.init()
    task_id = board.create_task(
        role="team_lead",
        title=args.job[:60],
        body=args.job,
        created_by=__import__("getpass").getuser(),
        project_dir=project_dir,
    )
    where = project_dir or "default workspace/"
    print(f"seeded task {task_id} for team_lead (working in: {where})")
