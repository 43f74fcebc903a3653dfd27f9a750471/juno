from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from logging import getLogger
from typing import AsyncGenerator, List, Optional
from yarl import URL

from playwright.async_api import (
    Browser,
    BrowserContext,
    Error,
    Page,
    Playwright,
    async_playwright,
)
# from . import stealth
from config import cookies

logger = getLogger("bot.browser")


class BrowserManager:
    pages: List[Page]
    browser: Optional[Browser]
    context: Optional[BrowserContext]
    playwright: Optional[Playwright]

    def __init__(self, total_pages: int = 4) -> None:
        self.pages = []
        self.browser = None
        self.context = None
        self.playwright = None
        self.total_pages = total_pages

    def __repr__(self) -> str:
        return f"<BrowserManager chromium pool={len(self.pages)}>"

    async def cleanup(self) -> None:
        if self.browser:
            await self.browser.close()

        if self.playwright:
            await self.playwright.stop()

        for page in list(self.pages):
            await page.close()
            self.pages.remove(page)

    async def _install(self) -> None:
        logger.warning("Executable not found, installing them now")
        process = await asyncio.create_subprocess_exec(
            "playwright",
            "install",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.wait()

    async def _install_dependencies(self) -> None:
        logger.warning("Host system is missing dependencies, installing them now")
        process = await asyncio.create_subprocess_exec(
            "playwright",
            "install-deps",
            stderr=asyncio.subprocess.PIPE,
        )
        await process.wait()

    async def setup(self) -> "BrowserManager":
        await self.cleanup()
        self.playwright = await async_playwright().start()
        try:
            self.browser = await self.playwright.chromium.connect_over_cdp(
                URL("ws://localhost:48324/")
                .with_query(
                    {
                        "token": "Cz54syrdEtqavNCsrWZ9",
                        "stealth": "true",
                        "blockAds": "true",
                        "--proxy-server": "socks5://warp:40000",
                    },
                )
                .human_repr()
            )
        except Error as exc:
            if "Executable doesn't exist" in exc.message:
                await self._install()
            elif "Host system is missing dependencies" in exc.message:
                await self._install_dependencies()
            else:
                raise exc

            return await self.setup()

        self.context = await self.browser.new_context(
            color_scheme="dark",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            locale="en_US",
        )
        await self.context.add_cookies(
            [cookie.dict(exclude_unset=True) for cookie in cookies]  # type: ignore
        )
        for _ in range(self.total_pages):
            page = await self.context.new_page()
            # await stealth.add_script(page)
            self.pages.append(page)

        logger.debug(
            f"Headless browser is ready with {len(self.pages)} pages in the pool"
        )
        return self

    @asynccontextmanager
    async def borrow_page(self) -> AsyncGenerator[Page, None]:
        while not self.pages:
            await asyncio.sleep(1)

        page = self.pages.pop()
        try:
            logger.info(
                f"Page borrowed from the pool, {len(self.pages)}/{self.total_pages} pages available"
            )
            yield page
        finally:
            with suppress(Error):
                await page.goto("about:blank")

            self.pages.append(page)
            logger.debug(
                f"Page released back to the pool, {len(self.pages)}/{self.total_pages} pages available"
            )