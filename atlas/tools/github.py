"""GitHub tools — PRs, issues, commits, repos, code search."""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone

import httpx

from atlas.core.models import Tier
from atlas.tools.registry import ToolRegistry

logger = logging.getLogger("atlas.tools.github")

_BASE = "https://api.github.com"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _fmt_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%b %d")
    except Exception:
        return iso[:10]


def _detect_repo() -> str | None:
    """Auto-detect GitHub repo from git remote in cwd."""
    try:
        out = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        # ssh: git@github.com:user/repo.git  or  https://github.com/user/repo.git
        if "github.com" in out:
            part = out.split("github.com")[-1].lstrip(":/")
            return part.removesuffix(".git")
    except Exception:
        pass
    return None


def register(registry: ToolRegistry, config=None) -> None:
    token = getattr(config, "github_token", "") if config else ""

    if not token:
        logger.info("GitHub token not set — GitHub tools disabled")
        return

    @registry.register(
        name="github_get_prs",
        description=(
            "List pull requests for a GitHub repository. "
            "Auto-detects repo from current git remote if not specified."
        ),
        parameters={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "owner/repo (e.g. octocat/Hello-World). Auto-detected if omitted."},
                "state": {"type": "string", "description": "open | closed | all (default: open)"},
                "limit": {"type": "integer", "description": "Max PRs to return (default 10)"},
            },
            "required": [],
        },
        tier=Tier.AUTO,
    )
    async def github_get_prs(repo: str = "", state: str = "open", limit: int = 10) -> str:
        r = repo or _detect_repo()
        if not r:
            return "Could not detect GitHub repo. Pass repo='owner/repo'."
        url = f"{_BASE}/repos/{r}/pulls?state={state}&per_page={min(limit, 30)}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_headers(token))
            if resp.status_code == 404:
                return f"Repo '{r}' not found or token lacks access."
            resp.raise_for_status()
            prs = resp.json()

        if not prs:
            return f"No {state} PRs in {r}."
        lines = [f"**{r}** — {state} PRs:"]
        for pr in prs[:limit]:
            num = pr["number"]
            title = pr["title"]
            author = pr["user"]["login"]
            updated = _fmt_date(pr["updated_at"])
            draft = " [draft]" if pr.get("draft") else ""
            lines.append(f"  #{num} {title} — @{author} ({updated}){draft}")
        return "\n".join(lines)

    @registry.register(
        name="github_get_issues",
        description=(
            "List issues for a GitHub repository. Auto-detects repo from git remote."
        ),
        parameters={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "owner/repo. Auto-detected if omitted."},
                "state": {"type": "string", "description": "open | closed | all (default: open)"},
                "label": {"type": "string", "description": "Filter by label (optional)"},
                "limit": {"type": "integer", "description": "Max issues to return (default 10)"},
            },
            "required": [],
        },
        tier=Tier.AUTO,
    )
    async def github_get_issues(
        repo: str = "", state: str = "open", label: str = "", limit: int = 10,
    ) -> str:
        r = repo or _detect_repo()
        if not r:
            return "Could not detect GitHub repo. Pass repo='owner/repo'."
        url = f"{_BASE}/repos/{r}/issues?state={state}&per_page={min(limit, 30)}"
        if label:
            url += f"&labels={label}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_headers(token))
            if resp.status_code == 404:
                return f"Repo '{r}' not found or token lacks access."
            resp.raise_for_status()
            items = resp.json()

        # GitHub returns PRs in issues endpoint too — filter them out
        issues = [i for i in items if "pull_request" not in i]
        if not issues:
            return f"No {state} issues in {r}."
        lines = [f"**{r}** — {state} issues:"]
        for iss in issues[:limit]:
            num = iss["number"]
            title = iss["title"]
            author = iss["user"]["login"]
            updated = _fmt_date(iss["updated_at"])
            labels = ", ".join(lb["name"] for lb in iss.get("labels", []))
            label_str = f" [{labels}]" if labels else ""
            lines.append(f"  #{num} {title} — @{author} ({updated}){label_str}")
        return "\n".join(lines)

    @registry.register(
        name="github_get_commits",
        description="List recent commits for a GitHub repository.",
        parameters={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "owner/repo. Auto-detected if omitted."},
                "branch": {"type": "string", "description": "Branch name (default: default branch)"},
                "limit": {"type": "integer", "description": "Number of commits (default 10)"},
            },
            "required": [],
        },
        tier=Tier.AUTO,
    )
    async def github_get_commits(repo: str = "", branch: str = "", limit: int = 10) -> str:
        r = repo or _detect_repo()
        if not r:
            return "Could not detect GitHub repo. Pass repo='owner/repo'."
        url = f"{_BASE}/repos/{r}/commits?per_page={min(limit, 30)}"
        if branch:
            url += f"&sha={branch}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_headers(token))
            if resp.status_code == 404:
                return f"Repo '{r}' not found or token lacks access."
            resp.raise_for_status()
            commits = resp.json()

        if not commits:
            return f"No commits found in {r}."
        lines = [f"**{r}** — recent commits:"]
        for c in commits[:limit]:
            sha = c["sha"][:7]
            msg = c["commit"]["message"].split("\n")[0][:72]
            author = (c.get("author") or {}).get("login") or c["commit"]["author"]["name"]
            date = _fmt_date(c["commit"]["author"]["date"])
            lines.append(f"  {sha} {msg} — @{author} ({date})")
        return "\n".join(lines)

    @registry.register(
        name="github_create_issue",
        description="Create a new GitHub issue in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Issue title"},
                "body": {"type": "string", "description": "Issue body/description (markdown OK)"},
                "repo": {"type": "string", "description": "owner/repo. Auto-detected if omitted."},
                "labels": {"type": "string", "description": "Comma-separated labels (optional)"},
            },
            "required": ["title"],
        },
        tier=Tier.NOTIFY,
    )
    async def github_create_issue(
        title: str, body: str = "", repo: str = "", labels: str = "",
    ) -> str:
        r = repo or _detect_repo()
        if not r:
            return "Could not detect GitHub repo. Pass repo='owner/repo'."
        payload: dict = {"title": title}
        if body:
            payload["body"] = body
        if labels:
            payload["labels"] = [lb.strip() for lb in labels.split(",")]
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{_BASE}/repos/{r}/issues",
                json=payload,
                headers=_headers(token),
            )
            if resp.status_code == 403:
                return "Token lacks 'issues: write' permission for this repo."
            resp.raise_for_status()
            issue = resp.json()
        return f"Created #{issue['number']}: {issue['html_url']}"

    @registry.register(
        name="github_search_repos",
        description="Search GitHub repositories by keyword.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default 8)"},
                "sort": {"type": "string", "description": "stars | forks | updated (default: stars)"},
            },
            "required": ["query"],
        },
        tier=Tier.AUTO,
    )
    async def github_search_repos(query: str, limit: int = 8, sort: str = "stars") -> str:
        url = f"{_BASE}/search/repositories?q={query}&sort={sort}&per_page={min(limit, 20)}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_headers(token))
            resp.raise_for_status()
            data = resp.json()

        repos = data.get("items", [])
        if not repos:
            return f"No repos found for '{query}'."
        lines = [f"GitHub repos matching '{query}':"]
        for r in repos[:limit]:
            stars = r.get("stargazers_count", 0)
            desc = (r.get("description") or "")[:60]
            lines.append(f"  **{r['full_name']}** ★{stars:,}  {desc}")
        return "\n".join(lines)

    @registry.register(
        name="github_get_user",
        description="Get info about the authenticated GitHub user or another user.",
        parameters={
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "GitHub username (omit for authenticated user)"},
            },
            "required": [],
        },
        tier=Tier.AUTO,
    )
    async def github_get_user(username: str = "") -> str:
        url = f"{_BASE}/users/{username}" if username else f"{_BASE}/user"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_headers(token))
            resp.raise_for_status()
            u = resp.json()
        name = u.get("name") or u.get("login")
        bio = u.get("bio") or ""
        repos = u.get("public_repos", 0)
        followers = u.get("followers", 0)
        return f"**{name}** (@{u['login']})\n{bio}\nPublic repos: {repos} | Followers: {followers}"
