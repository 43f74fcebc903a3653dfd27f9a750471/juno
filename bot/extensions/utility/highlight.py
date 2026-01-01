import asyncio
import re
from logging import getLogger
from random import choice
from textwrap import shorten
from typing import List, TypedDict, cast

from asyncpg import UniqueViolationError
from discord import (
    DMChannel,
    Embed,
    Forbidden,
    GroupChannel,
    HTTPException,
    Member,
    Message,
    PartialMessageable,
)
from discord.ext.commands import Cog, Range, check, group
from discord.utils import escape_markdown, escape_mentions, format_dt

from bot.core import Context, Juno
from bot.shared import Paginator

logger = getLogger("bot.highlight")


class Record(TypedDict):
    guild_id: int
    user_id: int
    keyword: str


async def can_dm(ctx: Context) -> bool:
    try:
        await ctx.author.send()
    except HTTPException as exc:
        if exc.code == 50007:
            raise ValueError("You need to enable DMs to use this command")

    return True


class Highlight(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @Cog.listener("on_message")
    async def highlight_listener(self, message: Message) -> None:
        if not message.guild or message.author.bot:
            return

        query = """
        SELECT DISTINCT ON (user_id) *
        FROM highlights
        WHERE guild_id = $1
        AND POSITION(keyword IN $2) > 0
        """
        records: List[Record] = [
            record
            for record in await self.bot.db.fetch(
                query,
                message.guild.id,
                message.content.lower(),
            )
            if record["user_id"] != message.author.id
            and (member := message.guild.get_member(record["user_id"]))
            and message.channel.permissions_for(member).view_channel
        ]
        if not records:
            return

        for record in records:
            if record["keyword"] not in message.content.lower():
                continue

            member = message.guild.get_member(record["user_id"])
            if member:
                self.bot.dispatch(
                    "highlight_dispatch", message, member, record["keyword"]
                )

    @Cog.listener()
    async def on_highlight_dispatch(
        self,
        message: Message,
        member: Member,
        keyword: str,
    ) -> None:
        """DM a member with a corresponding highlight."""

        if member in message.mentions:
            return

        elif isinstance(message.channel, (DMChannel, GroupChannel, PartialMessageable)):
            return

        resource = f"highlight:{message.channel.id}:{member.id}"
        ratelimited = await self.bot.redis.ratelimited(resource, 1, 30)
        if ratelimited:
            return

        try:
            await self.bot.wait_for(
                "member_activity",
                check=lambda channel, _member: channel == message.channel
                and _member == member,
                timeout=10,
            )
        except asyncio.TimeoutError:
            ...
        else:
            return

        embed = Embed(title=f"Highlight in {message.guild}")
        embed.set_author(
            name=message.author.display_name,
            icon_url=message.author.display_avatar,
        )
        embed.description = f"Keyword [*`{escape_markdown(keyword)}`*]({message.jump_url}) was said in {message.jump_url}\n>>> "

        messages: List[str] = []
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)

        for message in sorted(
            [
                message
                async for message in message.channel.history(
                    limit=5,
                    around=message,
                )
            ],
            key=lambda m: m.created_at,
        ):
            if not message.content:
                continue

            content = shorten(
                escape_markdown(message.content),
                width=50,
                placeholder="..",
            )
            if keyword in message.content.lower():
                content = pattern.sub("__\\g<0>__", message.content)

            fmt_dt = format_dt(message.created_at, "T")
            messages.append(
                f"[{fmt_dt}]({message.jump_url}) "
                f"**{escape_markdown(message.author.name)}:** {content}"
            )

        if not messages or not any("__" in message for message in messages):
            return

        embed.description += "\n".join(messages)

        try:
            await member.send(embed=embed)
        except Forbidden:
            query = "DELETE FROM highlights WHERE user_id = $1"
            await self.bot.db.execute(query, member.id)
        except HTTPException as exc:
            logger.exception(f"Failed to send highlight to {member}", exc_info=exc)
        else:
            logger.info(
                f"Dispatched highlight {keyword!r} to {member} from {message.guild}"
            )

    @group(aliases=("hl", "snitch"), invoke_without_command=True)
    @check(can_dm)
    async def highlight(self, ctx: Context, *, keyword: Range[str, 2, 32]) -> Message:
        """Receive a notification when a keyword is said."""

        keyword = keyword.lower()
        if escape_mentions(keyword) != keyword:
            return await ctx.warn("Keywords cannot contain mentions")

        query = """
        INSERT INTO highlights (
            guild_id,
            user_id,
            keyword
        ) VALUES ($1, $2, $3)
        """
        try:
            await self.bot.db.execute(query, ctx.guild.id, ctx.author.id, keyword)
        except UniqueViolationError:
            return await ctx.warn("You're already being notified for that keyword")

        return await ctx.send(
            f"Okay.. I'll {choice(['lyk', 'hit ur line', 'msg u'])} when `{keyword}` is said"
        )

    @highlight.command(
        name="add",
        aliases=("create", "new"),
        hidden=True,
    )
    async def highlight_add(
        self,
        ctx: Context,
        *,
        keyword: Range[str, 2, 32],
    ) -> Message:
        return await self.highlight(ctx, keyword=keyword)

    @highlight.command(name="remove", aliases=("delete", "del", "rm"))
    async def highlight_remove(
        self,
        ctx: Context,
        *,
        keyword: Range[str, 2, 32],
    ) -> Message:
        """Remove a keyword from your highlights."""

        keyword = keyword.lower()
        query = """
        DELETE FROM highlights
        WHERE guild_id = $1
        AND user_id = $2
        AND keyword = $3
        """
        result = await self.bot.db.execute(query, ctx.guild.id, ctx.author.id, keyword)
        if result == "DELETE 0":
            return await ctx.warn("You're not being notified for that keyword")

        return await ctx.send(f"Okay.. I won't notify you for `{keyword}` anymore")

    @highlight.command(name="list")
    async def highlight_list(self, ctx: Context) -> Message:
        """View your keyword highlights."""

        query = """
        SELECT ARRAY_AGG(keyword) AS keywords
        FROM highlights
        WHERE guild_id = $1
        AND user_id = $2
        """
        keywords = cast(
            List[str],
            await self.bot.db.fetchval(query, ctx.guild.id, ctx.author.id) or [],
        )
        if not keywords:
            return await ctx.warn("You don't have any active highlights")

        embed = Embed(title="Keyword Highlights")
        keywords = [f"{keyword!r}" for keyword in keywords]

        paginator = Paginator(ctx, keywords, embed)
        return await paginator.start()
