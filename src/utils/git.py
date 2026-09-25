"""Git helpers for reproducibility metadata."""

from __future__ import annotations

import subprocess
from pathlib import Path


def get_commit_hash(repo_dir: Path | None = None) -> str | None:
    """Return the current HEAD commit hash (suffixed ``-dirty`` if uncommitted changes exist).

    Returns ``None`` when git is unavailable or the directory is not a repository.
    """
    cwd = repo_dir or Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return f"{commit}-dirty" if status else commit
