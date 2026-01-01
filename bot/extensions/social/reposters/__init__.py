from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from logging import getLogger
from typing import TYPE_CHECKING, List, Optional, TypedDict, cast

from discord import File, HTTPException, Message
from discord.utils import find
from yt_dlp.extractor.common import ExtractorError
from yt_dlp.extractor.tumblr import TumblrIE
from yt_dlp.extractor.pinterest import PinterestIE
from yt_dlp.extractor.bilibili import BiliBiliIE
from bot.core import Context, Juno
from bot.shared.formatter import as_chunks

from .extraction import Information, download

if TYPE_CHECKING:
    from .. import Social

logger = getLogger("bot.reposter")
REPOSTERS = {
    "YouTube": [
        r"(?:http(?:s)?:\/\/)?(?:www\.)?(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/shorts\/)([a-zA-Z0-9_-]+)",
        r"(?:http(?:s)?:\/\/)?(?:www\.)?(?:youtube\.com\/clip\/)([a-zA-Z0-9_-]+)",
    ],
    "Snapchat": r"\<?(https?://(?:www\.)?snapchat\.com/(?:spotlight|t)/([a-zA-Z\d]+))\>?",
    "Facebook": r"\<?(https?://(?:www\.)?(facebook\.com|fb\.com)/share/r/([a-zA-Z\d]+))",
    "Twitter": r"\<?(https?://(twitter\.com|x\.com)/(\w+)/status/(\d+))\>?",
    "Tumblr": [TumblrIE._VALID_URL, r"https?://tmblr\.co/[A-Za-z0-9_-]{12,}"],
    "Pinterest": PinterestIE._VALID_URL,
    "BiliBili": BiliBiliIE._VALID_URL,
    "Twitch": r"""(?x)
        https?://
            (?:
                clips\.twitch\.tv/(?:embed\?.*?\bclip=|(?:[^/]+/)*)|
                (?:(?:www|go|m)\.)?twitch\.tv/(?:[^/]+/)?clip/
            )
            (?P<id>[^/?#&]+)
    """,
    "Streamable": r"https://streamable\.com/[a-zA-Z0-9]+",
    "Gofile": r"https?://(?:www\.)?gofile\.io/d/([a-zA-Z0-9]+)",
}


class ReposterConfig(TypedDict):
    guild_id: int
    status: bool
    prefix: bool
    deletion: bool
    disabled: bool


class Reposter:
    bot: Juno
    regex: List[str]
    add_listener: bool

    def __init__(
        self,
        bot: Juno,
        *,
        regex: List[str],
        name: Optional[str] = None,
        add_listener: bool = True,
    ):
        self.bot = bot
        self.regex = regex
        self.name = name or self.__class__.__name__
        if add_listener:
            self.bot.add_listener(self.listener, "on_message_without_command")

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"<{self.name} regex={self.regex}>"

    def match(self, url: str) -> Optional[re.Match[str]]:
        """Attempt to match the given url against the regex."""
        for pattern in self.regex:
            if match := re.search(pattern, url):
                return match

    async def fetch(self, url: str) -> Optional[Information]:
        """Fetch the information from the given url."""

        return await download(url)

    async def fetch_config(self, ctx: Context) -> ReposterConfig:
        query = """
        SELECT 
            COALESCE(config.status, TRUE) AS status,
            COALESCE(config.prefix, FALSE) AS prefix,
            COALESCE(config.deletion, FALSE) AS deletion,
            disabled.channel_id IS NOT NULL AS disabled
        FROM (SELECT $1::bigint AS guild_id) AS g
        LEFT JOIN reposter.config AS config ON g.guild_id = config.guild_id
        LEFT JOIN reposter.disabled AS disabled
        ON g.guild_id = disabled.guild_id
        AND disabled.channel_id = $2
        AND disabled.platform = $3
        """
        return cast(
            ReposterConfig,
            await self.bot.db.fetchrow(
                query,
                ctx.guild.id,
                ctx.channel.id,
                self.name,
            ),
        )

    async def listener(self, ctx: Context) -> Optional[Message]:
        possible_prefixes = tuple(
            member.name.lower()
            for member in ctx.guild.members
            if member.bot and member.id != self.bot.user.id
        )
        if ctx.message.content.lower().startswith(possible_prefixes):
            return

        url = self.match(ctx.message.content)
        if not url:
            return

        config = await self.fetch_config(ctx)
        if not config["status"] or config["disabled"]:
            return

        elif config["prefix"] and not ctx.message.content.lower().startswith(
            (
                self.bot.user.name,
                ctx.guild.me.display_name,
            )
        ):
            return

        ratelimited = await self.bot.redis.ratelimited(
            f"reposter:{ctx.guild.id}",
            limit=4,
            timespan=40,
        )
        if ratelimited:
            return

        async with ctx.typing():
            try:
                data = await asyncio.wait_for(self.fetch(url[0]), timeout=15)
            except asyncio.TimeoutError:
                data = None

            except ExtractorError as exc:
                logger.error(f"Failed to fetch {self.name} post", exc_info=exc)
                return await ctx.reply(
                    content=f"Failed to fetch {self.name} post: {exc.msg}",
                    delete_after=5,
                )

        if not data or not data.id or not any([data.requested_downloads, data.files]):
            if self.name not in ("YouTube", "Twitter"):
                await ctx.reply(
                    content=f"That {self.name} post could not be found",
                    delete_after=3,
                )

            return

        elif data.age_limit and data.age_limit >= 18 and not ctx.channel.is_nsfw():
            return await ctx.reply(
                content="This post is marked as NSFW and can only be posted in NSFW channels",
                delete_after=5,
            )

        logger.info(
            f"Redistributing {self.name} post {data.id[:12]} for {ctx.author} in {ctx.guild}"
        )
        files: List[File] = []
        if data.requested_downloads:
            for download in data.requested_downloads[:20]:
                buffer = await download.read()
                files.append(File(buffer, filename=download.filepath.split("/")[-1]))

        elif data.files:
            files.extend(data.files)

        try:
            for chunk in as_chunks(files, 6):
                if config["deletion"]:
                    await ctx.send(files=chunk)
                else:
                    await ctx.reply(files=chunk)

        except HTTPException:
            return await ctx.reply(
                content="The file was likely too large to send",
                delete_after=5,
            )
        else:
            if config["deletion"]:
                with suppress(HTTPException):
                    await ctx.message.delete()

            elif ctx.message.embeds:
                with suppress(HTTPException):
                    await ctx.message.edit(suppress=True)

        finally:
            for file in files:
                file.close()

            files.clear()
            if data.requested_downloads:
                for download in data.requested_downloads:
                    await download.delete(instant=False)

            if data.id:
                await self.bot.db.execute(
                    """
                    INSERT INTO reposter.log (
                        user_id,
                        guild_id,
                        channel_id,
                        platform,
                        post_id
                    ) VALUES ($1, $2, $3, $4, $5)
                    """,
                    ctx.author.id,
                    ctx.guild.id,
                    ctx.channel.id,
                    self.name,
                    data.id,
                )

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Reposter:
        social = cast(
            Optional["Social"],
            ctx.bot.get_cog("Social"),
        )
        if not social:
            raise ValueError("The Social cog is not loaded")

        reposter = find(
            lambda reposter: reposter.name.lower() == argument.lower(),
            social.reposters,
        )
        if not reposter:
            raise ValueError(f"Reposter `{argument}` not found")

        return reposter
