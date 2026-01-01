import re
from datetime import timedelta
from typing import Annotated, Callable, List, Optional

from discord import Member, Message
from discord.ext.commands import (
    BucketType,
    Cog,
    CommandError,
    Range,
    command,
    cooldown,
    group,
    has_permissions,
    max_concurrency,
)
from discord.utils import utcnow

from bot.core import Context, Juno
from bot.shared import quietly_delete
from bot.shared.converters import StrictMember, StrictUser
from bot.shared.formatter import plural


async def do_removal(
    ctx: Context,
    amount: int,
    predicate: Callable[[Message], bool] = lambda _: True,
    *,
    before: Optional[Message] = None,
    after: Optional[Message] = None,
) -> List[Message]:
    """A helper function to do bulk message removal."""

    if not ctx.channel.permissions_for(ctx.guild.me).manage_messages:
        raise CommandError("I don't have permission to delete messages")

    if not before:
        before = ctx.message

    def check(message: Message) -> bool:
        if message.created_at < (utcnow() - timedelta(weeks=2)):
            return False

        elif message.pinned:
            return False

        return predicate(message)

    await quietly_delete(ctx.message)
    messages = await ctx.channel.purge(
        limit=amount,
        check=check,
        before=before,
        after=after,
    )
    if not messages:
        raise CommandError("No messages were found, try a larger search?")

    return messages


Amount = Annotated[int, Range[int, 1, 1000]]


class Purge(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @command(aliases=("clean", "bc",))
    @has_permissions(manage_messages=True)
    async def cleanup(self, ctx: Context, amount: Amount = 100) -> None:
        """Remove bot invocations and messages from bots."""

        await do_removal(
            ctx,
            amount,
            lambda message: (
                message.author.bot
                or message.content.startswith(
                    (ctx.clean_prefix, ",", ";", ".", "!", "$", "-")
                )
            ),
        )

    @group(aliases=("clear", "rm", "c"), invoke_without_command=True)
    @max_concurrency(1, BucketType.channel)
    @has_permissions(manage_messages=True)
    async def purge(
        self,
        ctx: Context,
        user: Optional[StrictMember | StrictUser],
        amount: Amount = 100,
    ) -> None:
        """Remove messages which meet a criteria."""

        await do_removal(
            ctx,
            amount,
            lambda message: message.author == user if user else True,
        )

    @purge.command(name="contains", aliases=("contain", "c"))
    @has_permissions(manage_messages=True)
    async def purge_contains(
        self,
        ctx: Context,
        substring: str,
        amount: Amount = 100,
    ) -> None:
        """Remove messages which contain a substring."""

        await do_removal(
            ctx,
            amount,
            lambda message: bool(message.content)
            and substring.lower() in message.content.lower(),
        )

    @purge.command(name="startswith", aliases=("prefix", "start", "sw"))
    @has_permissions(manage_messages=True)
    async def purge_startswith(
        self,
        ctx: Context,
        substring: str,
        amount: Amount = 100,
    ) -> None:
        """Remove messages which start with a substring."""

        await do_removal(
            ctx,
            amount,
            lambda message: bool(message.content)
            and message.content.lower().startswith(substring.lower()),
        )

    @purge.command(name="endswith", aliases=("suffix", "end", "ew"))
    @has_permissions(manage_messages=True)
    async def purge_endswith(
        self,
        ctx: Context,
        substring: str,
        amount: Amount = 100,
    ) -> None:
        """Remove messages which end with a substring."""

        await do_removal(
            ctx,
            amount,
            lambda message: bool(message.content)
            and message.content.lower().endswith(substring.lower()),
        )

    @purge.command(name="invites", aliases=("invite", "inv", "i"))
    @has_permissions(manage_messages=True)
    async def purge_invites(
        self,
        ctx: Context,
        user: Optional[StrictMember | StrictUser],
        amount: Amount = 100,
    ) -> None:
        """Remove messages with invites."""

        await do_removal(
            ctx,
            amount,
            lambda message: (
                message.author == user
                if user
                else True
                and bool(
                    re.findall(
                        r"(?:https?://)?discord(?:\.gg|app\.com/invite)/[a-zA-Z0-9]+/?",
                        message.content,
                    )
                )
            ),
        )

    @purge.command(name="links", aliases=("link", "l"))
    @has_permissions(manage_messages=True)
    async def purge_links(
        self,
        ctx: Context,
        user: Optional[StrictMember | StrictUser],
        amount: Amount = 100,
    ) -> None:
        """Remove messages with links."""

        await do_removal(
            ctx,
            amount,
            lambda message: (
                message.author == user
                if user
                else True and "http" in message.content.lower()
            ),
        )

    @purge.command(name="embeds", aliases=("embed", "emb"))
    @has_permissions(manage_messages=True)
    async def purge_embeds(
        self,
        ctx: Context,
        user: Optional[StrictMember | StrictUser],
        amount: Amount = 100,
    ) -> None:
        """Remove messages with embeds."""

        await do_removal(
            ctx,
            amount,
            lambda message: (
                message.author == user if user else True and bool(message.embeds)
            ),
        )

    @purge.command(name="files", aliases=("file", "f"))
    @has_permissions(manage_messages=True)
    async def purge_files(
        self,
        ctx: Context,
        user: Optional[StrictMember | StrictUser],
        amount: Amount = 100,
    ) -> None:
        """Remove messages with files."""

        await do_removal(
            ctx,
            amount,
            lambda message: (
                message.author == user if user else True and bool(message.attachments)
            ),
        )

    @purge.command(name="voice", aliases=("vm", "vc", "v"))
    @has_permissions(manage_messages=True)
    async def purge_voice(
        self,
        ctx: Context,
        user: Optional[StrictMember | StrictUser],
        amount: Amount = 100,
    ) -> None:
        """Remove voice messages."""

        await do_removal(
            ctx,
            amount,
            lambda message: (
                message.author == user
                if user
                else True
                and any(attachment.waveform for attachment in message.attachments)
            ),
        )

    @purge.command(name="system", aliases=("sys", "sm"))
    @has_permissions(manage_messages=True)
    async def purge_system(self, ctx: Context, amount: Amount = 100) -> None:
        """Remove system messages."""

        await do_removal(ctx, amount, lambda message: message.is_system())

    @purge.command(name="mentions", aliases=("mention", "m"))
    @has_permissions(manage_messages=True)
    async def purge_mentions(
        self,
        ctx: Context,
        user: Optional[StrictMember | StrictUser],
        amount: Amount = 100,
    ) -> None:
        """Remove messages with mentions."""

        await do_removal(
            ctx,
            amount,
            lambda message: (
                message.author == user if user else True and bool(message.mentions)
            ),
        )

    @purge.command(name="emojis", aliases=("emotes", "emote", "emoji", "em", "e"))
    @has_permissions(manage_messages=True)
    async def purge_emojis(
        self,
        ctx: Context,
        user: Optional[StrictMember | StrictUser],
        amount: Amount = 100,
    ) -> None:
        """Remove messages with custom emojis."""

        await do_removal(
            ctx,
            amount,
            lambda message: (
                message.author == user
                if user
                else True and bool(re.findall(r"<a?:\w+:\d+>", message.content))
            ),
        )

    @purge.command(name="stickers", aliases=("sticker", "s"))
    @has_permissions(manage_messages=True)
    async def purge_stickers(
        self,
        ctx: Context,
        user: Optional[StrictMember | StrictUser],
        amount: Amount = 100,
    ) -> None:
        """Remove messages with stickers."""

        await do_removal(
            ctx,
            amount,
            lambda message: (
                message.author == user if user else True and bool(message.stickers)
            ),
        )

    @purge.command(name="humans", aliases=("human", "h"))
    @has_permissions(manage_messages=True)
    async def purge_humans(self, ctx: Context, amount: Amount = 100) -> None:
        """Remove messages from humans."""

        await do_removal(ctx, amount, lambda message: not message.author.bot)

    @purge.command(name="bots", aliases=("bot", "b"))
    @has_permissions(manage_messages=True)
    async def purge_bots(self, ctx: Context, amount: Amount = 100) -> None:
        """Remove messages from bots."""

        await do_removal(ctx, amount, lambda message: message.author.bot)

    @purge.command(name="webhooks", aliases=("webhook", "wh"))
    @has_permissions(manage_messages=True)
    async def purge_webhooks(self, ctx: Context, amount: Amount = 100) -> None:
        """Remove messages from webhooks."""

        await do_removal(ctx, amount, lambda message: bool(message.webhook_id))

    @purge.command(name="before")
    @has_permissions(manage_messages=True)
    async def purge_before(self, ctx: Context, message: Optional[Message]) -> None:
        """Remove messages before a specific message."""

        message = message or ctx.replied_message
        if not message:
            return await ctx.send_help(ctx.command)

        elif message.guild != ctx.guild:
            return await ctx.send_help(ctx.command)

        await do_removal(ctx, 300, before=message)

    @purge.command(name="after", aliases=("since", "upto", "up"))
    @has_permissions(manage_messages=True)
    async def purge_after(self, ctx: Context, message: Optional[Message]) -> None:
        """Remove messages after a specific message."""

        message = message or ctx.replied_message
        if not message:
            return await ctx.send_help(ctx.command)

        elif message.guild != ctx.guild:
            return await ctx.send_help(ctx.command)

        await do_removal(ctx, 300, after=message)

    @purge.command(name="between", aliases=("range", "btw", "rng"))
    @has_permissions(manage_messages=True)
    @cooldown(1, 30, BucketType.channel)
    async def purge_between(
        self, ctx: Context, start: Optional[Message], end: Optional[Message]
    ) -> None:
        """Remove messages between two specific messages."""

        start = start or ctx.replied_message
        end = end or ctx.message
        if not start or not end:
            return await ctx.send_help(ctx.command)

        elif start.guild != ctx.guild or end.guild != ctx.guild:
            return await ctx.send_help(ctx.command)

        await do_removal(ctx, 1000, after=start, before=end)

    @purge.command(name="except", aliases=("besides", "schizo"))
    @has_permissions(manage_messages=True)
    async def purge_except(
        self,
        ctx: Context,
        member: Member,
        amount: Amount = 500,
    ) -> None:
        """Remove messages not sent by a specific member."""

        await do_removal(
            ctx,
            amount,
            lambda message: message.author != member,
        )

    @purge.command(name="reactions", aliases=("reaction", "reacts" "react", "r"))
    @has_permissions(manage_messages=True)
    async def purge_reactions(
        self,
        ctx: Context,
        user: Optional[StrictMember | StrictUser],
        amount: Amount = 100,
    ) -> Message:
        """Remove reactions from messages."""

        total_removed = 0
        async with ctx.typing():
            async for message in ctx.channel.history(limit=amount, before=ctx.message):
                if len(message.reactions):
                    total_removed += sum(
                        reaction.count for reaction in message.reactions
                    )
                    await message.clear_reactions()

        return await ctx.respond(
            f"Successfully removed {plural(total_removed, md='`'):reaction}"
        )
