from __future__ import annotations

import asyncio
from functools import wraps
import inspect
from contextlib import asynccontextmanager
from datetime import timedelta
from io import BytesIO
from secrets import token_hex
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable, Optional, cast

from anyio import Path
from cashews import suppress
from colorthief import ColorThief
from discord import Color, Embed, HTTPException, Interaction, Message, PartialMessage
from discord import Spotify as SpotifyActivity
from discord import Thread
from discord.ext.commands import MissingRequiredArgument, CooldownMapping, CommandOnCooldown, Cog
from discord.ui import Select, View
from discord.utils import format_dt, utcnow
from jishaku.functools import executor_function
from xxhash import xxh32_hexdigest

# from .browser import BrowserManager  # noqa
from .paginator import Paginator  # noqa
from .script import Script  # noqa
from .timer import Timer

if TYPE_CHECKING:
    from bot.core import Context


class RestrictedView(View):
    ctx: Context

    def __init__(self, ctx: Context, **kwargs) -> None:
        self.ctx = ctx
        super().__init__(**kwargs)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user != self.ctx.author:
            embed = Embed(
                color=Color.dark_embed(),
                description="You cannot interact with this view",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        return interaction.user == self.ctx.author


class RestrictedSelect(Select):
    ctx: Context

    def __init__(self, ctx: Context, **kwargs) -> None:
        self.ctx = ctx
        super().__init__(**kwargs)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user != self.ctx.author:
            embed = Embed(
                color=Color.dark_embed(),
                description="You cannot interact with this selection",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        return interaction.user == self.ctx.author


def from_stack(name: str) -> Optional[Any]:
    stack = inspect.stack()
    try:
        for frames in stack:
            try:
                frame = frames[0]
                current_locals = frame.f_locals
                if name in current_locals:
                    return current_locals[name]
            finally:
                del frame
    finally:
        del stack


def clean_url(url: str) -> str:
    return url.split("?")[0].split("#")[0]


def codeblock(content: str, language: str = "") -> str:
    return f"```{language}\n{content}\n```"


def retry(attempts: int = 0, delay: int = 0, timeout: Optional[int] = None):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            for _ in range(attempts + 1):
                try:
                    return await asyncio.wait_for(
                        func(*args, **kwargs),
                        timeout=timeout,
                    )
                except Exception:
                    if _ == attempts:
                        raise

                    await asyncio.sleep(delay)

        return wrapper

    return decorator

@asynccontextmanager
async def temp_directory() -> AsyncGenerator[Path, None]:
    tmp = Path(f"/tmp/juno/{token_hex(8)}")

    try:
        await tmp.mkdir()
        yield tmp
    finally:
        await asyncio.sleep(0.5)
        async for child in tmp.iterdir():
            await child.unlink(missing_ok=True)

        await tmp.rmdir()

@asynccontextmanager
async def temp_file(extension: str) -> AsyncGenerator[Path, None]:
    tmp = Path(f"/tmp/juno/{token_hex(8)}.{extension}")

    try:
        yield tmp
    finally:
        await asyncio.sleep(0.5)
        await tmp.unlink(missing_ok=True)


@asynccontextmanager
async def temp_thread(
    ctx: Context,
    name: str,
    duration: Optional[timedelta] = None,
) -> AsyncGenerator[Thread, None]:
    """Create a temporary thread on the message."""

    duration = duration or timedelta(minutes=5)
    thread = await ctx.message.create_thread(
        name=name,
        reason=f"Temporary thread for {ctx.command.name}.",
    )
    try:
        yield thread
    finally:
        exists = ctx.guild.get_channel_or_thread(thread.id)
        if not exists:
            return

        expires_at = utcnow() + duration
        await thread.send(f"This thread will be deleted {format_dt(expires_at, 'R')}")
        await Timer.create(
            ctx.bot,
            "thread",
            expires_at,
            guild_id=ctx.guild.id,
            thread_id=thread.id,
        )


async def quietly_delete(message: Message | PartialMessage) -> None:
    if not message.guild:
        return

    if message.channel.permissions_for(message.guild.me).manage_messages:
        with suppress(HTTPException):
            await message.delete()


def get_spotify_activity(ctx: Context) -> str:
    for activity in ctx.author.activities:
        if isinstance(activity, SpotifyActivity):
            return f"{activity.title} by {activity.artists[0]}"

    if ctx.current_parameter:
        raise MissingRequiredArgument(ctx.current_parameter)

    raise ValueError("You need to specify a search query")


@executor_function
def color_thief(buffer: BytesIO) -> Color:
    """Extract the dominant color from an image."""

    thief = ColorThief(buffer)
    color = thief.get_color()
    return Color.from_rgb(*color)


async def dominant_color(buffer: bytes | BytesIO | bytearray | memoryview) -> Color:
    """Extract the dominant color from an image."""

    from bot.core.redis import GLOBAL_REDIS

    if isinstance(buffer, (bytes, bytearray, memoryview)):
        key = xxh32_hexdigest(buffer)
        buffer = BytesIO(buffer)
    else:
        key = xxh32_hexdigest(buffer.getvalue())

    cached = cast(Optional[str], await GLOBAL_REDIS.hget("dominant_colors", key))
    if cached:
        return Color.from_rgb(*map(int, cached.split(",")))

    color = await color_thief(buffer)

    await GLOBAL_REDIS.hset("dominant_colors", key, ",".join(map(str, color.to_rgb())))
    return color

def coerce_to_string(data: Any) -> Any:
    if isinstance(data, dict):
        return {key: coerce_to_string(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [coerce_to_string(value) for value in data]
    elif isinstance(data, (int, float)) and not isinstance(data, bool):
        return str(data)
    
    return data

def coerce_to_int(data: Any) -> Any:
    if isinstance(data, dict):
        return {key: coerce_to_int(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [coerce_to_int(value) for value in data]
    elif isinstance(data, str) and data.isdigit():
        return int(data)
    
    return data

def cooldowns(*mappings: CooldownMapping) -> Callable:
    """A decorator to apply multiple cooldowns to a command."""
    
    def wrapper(func: Callable):
        func.__cooldowns__ = mappings

        @wraps(func)
        async def wrapped(*args, **kwargs):
            ctx = cast("Context", args[1] if isinstance(args[0], Cog) else args[0])
            for mapping in mappings:
                bucket = mapping.get_bucket(ctx.message)
                if not bucket:
                    continue

                retry_after = bucket.update_rate_limit()
                if retry_after:
                    raise CommandOnCooldown(bucket, retry_after, mapping._type) # type: ignore
                
            return await func(*args, **kwargs)

        return wrapped
    
    return wrapper