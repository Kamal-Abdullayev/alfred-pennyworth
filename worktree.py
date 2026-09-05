# worktree.py — git worktree isolation.
#
# Agents never touch the user's checked-out branch. Each chain gets its own
# worktree under project_x/worktrees/<chain> on branch alfred/<chain>. Review
# with `git diff <base_sha>..alfred/<chain>`; discard with remove().
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parent
WORKTREES = ROOT / "worktrees"


@dataclass
class Worktree:
    path: str
    branch: str
    base_sha: str
    repo: str


def _git(cwd, *args) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def is_repo(path) -> bool:
    try:
        _git(path, "rev-parse", "--show-toplevel")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def repo_root(path) -> str:
    return _git(path, "rev-parse", "--show-toplevel")


def head_sha(path) -> str:
    return _git(path, "rev-parse", "HEAD")


def create(repo_path, chain_id: str) -> Worktree:
    root = repo_root(repo_path)
    branch = f"alfred/{chain_id}"
    path = WORKTREES / chain_id
    if path.exists():
        raise RuntimeError(f"worktree already exists: {path}")
    WORKTREES.mkdir(exist_ok=True)
    base = head_sha(root)
    _git(root, "worktree", "add", "-b", branch, str(path), "HEAD")
    return Worktree(path=str(path), branch=branch, base_sha=base, repo=root)


def remove(repo_path, path, delete_branch: str | None = None) -> None:
    root = repo_root(repo_path)
    _git(root, "worktree", "remove", "--force", str(path))
    if delete_branch:
        _git(root, "branch", "-D", delete_branch)


def list_for(repo_path) -> list[dict]:
    root = repo_root(repo_path)
    out, cur = [], {}
    for line in _git(root, "worktree", "list", "--porcelain").splitlines() + [""]:
        if line.startswith("worktree "):
            cur = {"path": line[9:]}
        elif line.startswith("HEAD "):
            cur["head"] = line[5:12]
        elif line.startswith("branch "):
            cur["branch"] = line[7:].replace("refs/heads/", "")
        elif line == "" and cur:
            out.append({"path": cur.get("path"), "branch": cur.get("branch", "(detached)"), "head": cur.get("head", "")})
            cur = {}
    return out
