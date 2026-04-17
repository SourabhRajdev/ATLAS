"""Playwright backend — persistent browser session for real web control.

Uses a single persistent context rooted at ~/.atlas/browser_profile so sessions,
cookies, and logins survive between runs. The browser is launched lazily on
first use and kept alive for the daemon lifetime.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from atlas.control.models import Action

logger = logging.getLogger("atlas.control.playwright")

PROFILE_DIR = Path("~/.atlas/browser_profile").expanduser()


class PlaywrightBackend:
    SUPPORTS = {
        "browser.open",
        "browser.click",
        "browser.fill",
        "browser.extract_text",
        "browser.screenshot",
    }

    def __init__(self) -> None:
        self._ctx = None
        self._pw = None

    def can_handle(self, action: Action) -> bool:
        return action.kind in self.SUPPORTS

    async def _ensure(self):
        if self._ctx is not None:
            return self._ctx
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except ImportError:
            raise RuntimeError("playwright not installed — run: uv add playwright && playwright install chromium")
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()
        self._ctx = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1440, "height": 900},
        )
        return self._ctx

    async def _page(self):
        ctx = await self._ensure()
        if ctx.pages:
            return ctx.pages[-1]
        return await ctx.new_page()

    async def shutdown(self) -> None:
        try:
            if self._ctx:
                await self._ctx.close()
            if self._pw:
                await self._pw.stop()
        except Exception as e:
            logger.debug("shutdown error: %s", e)
        self._ctx = None
        self._pw = None

    async def execute(self, action: Action) -> tuple[bool, Any, dict]:
        try:
            handler = getattr(self, f"_{action.kind.replace('.', '_')}")
            return await handler(action.params)
        except Exception as e:
            logger.warning("playwright %s failed: %s", action.kind, e)
            return False, str(e), {}

    async def _browser_open(self, p: dict) -> tuple[bool, Any, dict]:
        url = p.get("url", "")
        page = await self._page()
        await page.goto(url, wait_until="domcontentloaded")
        return True, await page.title(), {"url": url}

    async def _browser_click(self, p: dict) -> tuple[bool, Any, dict]:
        page = await self._page()
        await page.click(p.get("selector", ""))
        return True, "clicked", {"selector": p.get("selector")}

    async def _browser_fill(self, p: dict) -> tuple[bool, Any, dict]:
        page = await self._page()
        await page.fill(p.get("selector", ""), p.get("value", ""))
        return True, "filled", {"selector": p.get("selector")}

    async def _browser_extract_text(self, p: dict) -> tuple[bool, Any, dict]:
        page = await self._page()
        sel = p.get("selector") or "body"
        text = await page.inner_text(sel)
        return True, text[:4000], {}

    async def _browser_screenshot(self, p: dict) -> tuple[bool, Any, dict]:
        page = await self._page()
        out = Path(p.get("path") or "~/.atlas/screenshots/browser.png").expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(out), full_page=True)
        return True, str(out), {"path": str(out)}
