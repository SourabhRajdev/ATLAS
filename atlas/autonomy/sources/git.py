"""Git source — uncommitted-time + branch-drift detection for a tracked repo.

Emits:
  - uncommitted_long (seconds since last commit + dirty working tree)
  - branch_behind    (local branch behind upstream)
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from atlas.autonomy.models import Signal

logger = logging.getLogger("atlas.autonomy.git")

UNCOMMITTED_THRESHOLD_S = 2 * 3600       # 2 hours


class GitSource:
    source = "git"

    def __init__(self, repo: Path | None = None) -> None:
        self.repo = (repo or Path.cwd()).resolve()
        self._last_fired_uncommitted: float = 0.0

    async def poll(self) -> list[Signal]:
        if not (self.repo / ".git").exists():
            return []
        signals: list[Signal] = []

        dirty = await self._run("git", "status", "--porcelain")
        if dirty is None:
            return []
        if dirty.strip():
            ts_str = await self._run("git", "log", "-1", "--format=%ct")
            try:
                last_commit = int((ts_str or "0").strip())
            except ValueError:
                last_commit = 0
            age = time.time() - last_commit if last_commit else 0
            if age > UNCOMMITTED_THRESHOLD_S and time.time() - self._last_fired_uncommitted > 3600:
                self._last_fired_uncommitted = time.time()
                signals.append(Signal(
                    source=self.source, kind="uncommitted_long",
                    payload={"age_seconds": int(age), "files": dirty.strip().count("\n") + 1,
                             "repo": str(self.repo)},
                ))

        behind = await self._run("git", "rev-list", "--count", "HEAD..@{upstream}")
        try:
            n = int((behind or "0").strip())
        except ValueError:
            n = 0
        if n > 0:
            signals.append(Signal(
                source=self.source, kind="branch_behind",
                payload={"commits_behind": n, "repo": str(self.repo)},
            ))
        return signals

    async def _run(self, *args: str) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, cwd=str(self.repo),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode != 0:
                return None
            return stdout.decode("utf-8", "replace")
        except Exception as e:
            logger.debug("git %s failed: %s", args, e)
            return None
