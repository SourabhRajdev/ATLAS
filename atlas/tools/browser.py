"""Browser tools — real browser via AppleScript, Playwright only for automation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from atlas.core.models import Tier
from atlas.tools.registry import ToolRegistry

# Lazy Playwright — only for automation tasks
_playwright = None
_browser = None


async def _get_browser():
    global _playwright, _browser
    if _browser is None:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError("Playwright not installed. Run: pip install atlas[browser]")
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(headless=False)
    return _browser


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


async def _find_chrome_profile(query: str) -> str | None:
    """Find Chrome profile directory by name, email, or dir name."""
    chrome_dir = Path(
        "~/Library/Application Support/Google/Chrome"
    ).expanduser()

    if not chrome_dir.is_dir():
        return None

    query_lower = query.lower()

    for item in chrome_dir.iterdir():
        if not item.is_dir():
            continue
        prefs_path = item / "Preferences"
        if not prefs_path.exists():
            continue
        try:
            prefs = json.loads(prefs_path.read_text())
            name = prefs.get("profile", {}).get("name", "").lower()
            email = (
                prefs.get("account_info", [{}])[0].get("email", "").lower()
            )

            if (
                query_lower in name
                or query_lower in email
                or query_lower == item.name.lower()
            ):
                return item.name
        except Exception:
            pass
    return None


def register(registry: ToolRegistry) -> None:

    @registry.register(
        name="list_chrome_profiles",
        description="List all available Chrome profiles with names and emails.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        tier=Tier.AUTO,
    )
    async def list_chrome_profiles() -> str:
        chrome_dir = Path(
            "~/Library/Application Support/Google/Chrome"
        ).expanduser()

        if not chrome_dir.is_dir():
            return "Chrome data directory not found."

        profiles = []
        for item in chrome_dir.iterdir():
            if item.is_dir() and (item / "Preferences").exists():
                try:
                    prefs = json.loads(
                        (item / "Preferences").read_text()
                    )
                    name = prefs.get("profile", {}).get("name", item.name)
                    email = (
                        prefs.get("account_info", [{}])[0].get("email", "")
                    )
                    profiles.append({
                        "dir": item.name,
                        "name": name,
                        "email": email,
                    })
                except Exception:
                    pass

        if not profiles:
            return "No Chrome profiles found."

        lines = ["Chrome profiles:"]
        for p in profiles:
            suffix = f" ({p['email']})" if p["email"] else ""
            lines.append(f"  {p['dir']}: {p['name']}{suffix}")
        return "\n".join(lines)

    @registry.register(
        name="browser_open",
        description=(
            "Open a URL in the user's real browser (Chrome, Safari, Firefox, Arc). "
            "For Chrome, optionally specify a profile by name, email, or dir "
            "(e.g. 'Default', 'Profile 1', 'Personal', 'user@gmail.com')."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to open"},
                "browser": {
                    "type": "string",
                    "description": "Browser name: chrome, safari, firefox, arc (default: chrome)",
                },
                "profile": {
                    "type": "string",
                    "description": "Chrome profile — name, email, or dir like 'Profile 1' (optional)",
                },
            },
            "required": ["url"],
        },
        tier=Tier.NOTIFY,
    )
    async def browser_open(
        url: str, browser: str = "chrome", profile: str | None = None
    ) -> str:
        # Chrome with specific profile
        if browser.lower().strip() in ("chrome", "google chrome") and profile:
            profile_dir = await _find_chrome_profile(profile)
            if profile_dir:
                cmd = [
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    f"--profile-directory={profile_dir}",
                    url,
                ]
                try:
                    proc = await asyncio.create_subprocess_exec(*cmd)
                    await proc.wait()
                    return f"Opened {url} in Chrome profile: {profile}"
                except FileNotFoundError:
                    # Chrome not at expected path, try open command
                    await asyncio.create_subprocess_exec(
                        "open", "-na", "Google Chrome",
                        "--args", f"--profile-directory={profile_dir}", url
                    )
                    return f"Opened {url} in Chrome profile: {profile}"
            else:
                return f"Chrome profile '{profile}' not found. Use list_chrome_profiles to see available profiles."

        # Default: AppleScript open
        browser_map = {
            "chrome": "Google Chrome",
            "safari": "Safari",
            "firefox": "Firefox",
            "arc": "Arc",
            "brave": "Brave Browser",
            "edge": "Microsoft Edge",
            "default": "Google Chrome",
        }
        app = browser_map.get(browser.lower().strip(), "Google Chrome")
        script = f'tell application "{app}" to open location "{_q(url)}"'
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode == 0:
                return f"Opened in {app}."
        except Exception:
            pass
        # Fallback: macOS open command
        proc = await asyncio.create_subprocess_exec("open", "-a", app, url)
        await proc.communicate()
        return f"Opened in {app}."

    @registry.register(
        name="browser_screenshot",
        description="Take a screenshot of a web page (uses automation browser).",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to screenshot"},
                "path": {"type": "string", "description": "Path to save screenshot"},
                "full_page": {"type": "boolean", "description": "Capture full scrollable page", "default": False},
            },
            "required": ["path"],
        },
        tier=Tier.NOTIFY,
    )
    async def browser_screenshot(url: str = "", path: str = "screenshot.png", full_page: bool = False) -> str:
        b = await _get_browser()
        page = await b.new_page()
        if url:
            await page.goto(url, wait_until="domcontentloaded")
        screenshot_path = Path(path).expanduser().resolve()
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(screenshot_path), full_page=full_page)
        await page.close()
        return f"Screenshot saved: {screenshot_path}"

    @registry.register(
        name="browser_extract_text",
        description="Extract visible text from a web page (uses automation browser).",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to extract text from"},
                "selector": {"type": "string", "description": "CSS selector (default: body)"},
            },
            "required": ["url"],
        },
        tier=Tier.AUTO,
    )
    async def browser_extract_text(url: str, selector: str = "body") -> str:
        b = await _get_browser()
        page = await b.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        text = await page.locator(selector).inner_text()
        if len(text) > 10000:
            text = text[:10000] + "\n... (truncated)"
        await page.close()
        return text

    @registry.register(
        name="browser_click",
        description="Click an element on a web page by CSS selector (uses automation browser).",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to first (optional)"},
                "selector": {"type": "string", "description": "CSS selector of element to click"},
            },
            "required": ["selector"],
        },
        tier=Tier.CONFIRM,
        destructive=True,
    )
    async def browser_click(selector: str, url: str = "") -> str:
        b = await _get_browser()
        page = await b.new_page()
        if url:
            await page.goto(url, wait_until="domcontentloaded")
        await page.click(selector)
        await page.wait_for_timeout(1000)
        return f"Clicked: {selector}"

    @registry.register(
        name="browser_fill_form",
        description="Fill a form field with text (uses automation browser).",
        parameters={
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of input field"},
                "text": {"type": "string", "description": "Text to fill"},
            },
            "required": ["selector", "text"],
        },
        tier=Tier.CONFIRM,
        destructive=True,
    )
    async def browser_fill_form(selector: str, text: str) -> str:
        b = await _get_browser()
        contexts = b.contexts
        if not contexts:
            return "Error: No browser page open. Use browser_open first."
        page = contexts[0].pages[0] if contexts[0].pages else await contexts[0].new_page()
        await page.fill(selector, text)
        return f"Filled: {selector}"
