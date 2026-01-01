import asyncio
import gc
from contextlib import suppress
from dotenv import load_dotenv

from anyio import Path
import shutil
# from playwright.async_api import Error as BrowserException

from bot.core import Juno
from bot.shared.client.logging import setup_logging
from config import config

load_dotenv()
cache = Path("/tmp/juno")

async def clear_cache():
    shutil.rmtree(cache, ignore_errors=True)
    await cache.mkdir(exist_ok=True)

async def run_bot():
    gc.enable()
    await clear_cache()
    async with Juno(config) as bot:
        await bot.start()


if __name__ == "__main__":
    with setup_logging(), suppress(
        KeyboardInterrupt,
        ProcessLookupError,
        # BrowserException,
    ):
        asyncio.run(run_bot())
