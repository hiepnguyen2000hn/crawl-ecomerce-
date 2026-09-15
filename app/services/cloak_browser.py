"""
CloakBrowser session manager.

Architecture:
  1 cloakserve container (Docker) → N Chrome processes via fingerprint seed.
  Mỗi account/profile kết nối qua:
    ws://cloakbrowser:9222?fingerprint=<seed>&proxy=<url>

RAM estimate: ~190MB idle / session, ~280MB khi có 3 tabs.
"""
from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncGenerator

# Import trễ, KHÔNG ở cấp module. `crud/browser_profile.py` chỉ cần `generate_seed()`
# (một lời gọi random) nhưng lại kéo theo cả file này; import playwright ở cấp module
# làm API lẫn worker không khởi động nổi trên môi trường chưa cài trình duyệt — kể cả
# khi chỉ chạy nguồn T0 như Shopify, vốn không cần trình duyệt nào.
#
# Đây cũng là điều kiện để `is_available()` của các adapter T2 chạy đúng: chúng tự loại
# mình khỏi chuỗi tier khi thiếu playwright, nhưng chỉ có tác dụng nếu app còn import được.
if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright

from app.config import settings


class CloakBrowserError(Exception):
    pass


class CloakSession:
    """Một session = một Chrome process với fingerprint riêng."""

    def __init__(
        self,
        page: Page,
        context: BrowserContext,
        browser: Browser,
        playwright: Playwright,
        fingerprint_seed: int,
    ):
        self.page = page
        self.context = context
        self.browser = browser
        self._playwright = playwright
        self.fingerprint_seed = fingerprint_seed

    async def close(self) -> None:
        try:
            await self.context.close()
        except Exception:
            pass
        try:
            await self.browser.close()
        except Exception:
            pass
        try:
            self._playwright.stop()
        except Exception:
            pass


@asynccontextmanager
async def cloak_session(
    fingerprint_seed: int,
    proxy_url: str | None = None,
    headless: bool = True,
    profile_dir: str | None = None,
) -> AsyncGenerator[CloakSession, None]:
    """
    Context manager tạo 1 session CloakBrowser.

    Ưu tiên kết nối cloakserve (Docker).
    Fallback: launch local nếu CLOAK_BROWSER_URL không available.

    Usage:
        async with cloak_session(fingerprint_seed=12345, proxy_url="http://...") as s:
            await s.page.goto("https://example.com")
            content = await s.page.content()
    """
    from playwright.async_api import async_playwright

    cloak_url = settings.cloak_browser_url.rstrip("/")
    cdp_url = f"{cloak_url}?fingerprint={fingerprint_seed}"
    if proxy_url:
        cdp_url += f"&proxy={proxy_url}"

    pw = await async_playwright().start()

    try:
        # Thử kết nối cloakserve
        browser = await pw.chromium.connect_over_cdp(cdp_url)
    except Exception:
        # Fallback: launch cloakbrowser local (cần binary installed)
        try:
            from cloakbrowser import launch as cloak_launch  # type: ignore
            args = [f"--fingerprint={fingerprint_seed}"]
            if proxy_url:
                args.append(f"--proxy-server={proxy_url}")
            browser = cloak_launch(headless=headless, args=args)
        except ImportError:
            # Fallback cuối: Playwright thường (không có cloak)
            launch_args = []
            if proxy_url:
                launch_args.append(f"--proxy-server={proxy_url}")
            browser = await pw.chromium.launch(headless=headless, args=launch_args)

    context_kwargs: dict = {}
    if profile_dir:
        context_kwargs["user_data_dir"] = profile_dir

    context = await browser.new_context(**context_kwargs)
    page = await context.new_page()

    session = CloakSession(
        page=page,
        context=context,
        browser=browser,
        playwright=pw,
        fingerprint_seed=fingerprint_seed,
    )
    try:
        yield session
    finally:
        await session.close()


def generate_seed() -> int:
    """Random seed cho profile mới (1 → 999_999_999)."""
    return random.randint(1, 999_999_999)


class BrowserPool:
    """
    Pool giới hạn số session đồng thời (theo license CloakBrowser).
    Dùng semaphore để tránh vượt quá concurrent limit.
    """

    def __init__(self, max_concurrent: int = 1):
        self._sem = asyncio.Semaphore(max_concurrent)

    @asynccontextmanager
    async def acquire(
        self,
        fingerprint_seed: int,
        proxy_url: str | None = None,
        headless: bool = True,
        profile_dir: str | None = None,
    ) -> AsyncGenerator[CloakSession, None]:
        async with self._sem:
            async with cloak_session(
                fingerprint_seed=fingerprint_seed,
                proxy_url=proxy_url,
                headless=headless,
                profile_dir=profile_dir,
            ) as session:
                yield session


# Singleton pool — max_concurrent theo tier license
# Free = 1, Pro = tuỳ plan (5/20/200/2000)
browser_pool = BrowserPool(max_concurrent=1)
