from typing import List, Optional, cast

from discord import (
    Embed,
    Guild,
    HTTPException,
    Member,
    Message,
    RawReactionActionEvent,
    RawReactionClearEmojiEvent,
    TextChannel,
    Thread,
)
from discord.abc import GuildChannel
from discord.ext.commands import Cog, Range, flag, group, has_permissions
from discord.utils import find

from bot.core import Context, Juno
from bot.shared import quietly_delete
from bot.shared.converters import FlagConverter, Status
from bot.shared.formatter import plural
from bot.shared.paginator import Paginator

from .config import Config, Record


class CompleteConfig(Config):
    guild: Guild
    channel: TextChannel


class Flags(FlagConverter):
    threshold: Range[int, 1, 12] = flag(
        aliases=["limit"],
        description="The threshold before reposting a message.",
        default=3,
    )
    self_star: Status = flag(
        aliases=["self"],
        description="Allow the author to star their own message.",
        default=True,
    )


class Starboard(Cog):
    def __init__(self, bot: Juno):
        self.bot = bot

    @group(
        aliases=("star", "board", "sb"),
        invoke_without_command=True,
    )
    @has_permissions(manage_guild=True)
    async def starboard(self, ctx: Context) -> Message:
        """Archive popular messages to a dedicated channel."""

        return await ctx.send_help(ctx.command)

    @starboard.command(name="add", aliases=("create",))
    @has_permissions(manage_guild=True)
    async def starboard_add(
        self,
        ctx: Context,
        channel: TextChannel,
        emoji: str,
        *,
        flags: Flags,
    ) -> Message:
        """Add a starboard to a channel."""

        try:
            await ctx.message.add_reaction(emoji)
        except (HTTPException, TypeError):
            return await ctx.warn(
                f"I'm not able to use **{emoji}**, try using an emoji from this server"
            )

        await self.bot.db.execute(
            """
            INSERT INTO starboard VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (guild_id, emoji) DO UPDATE
            SET
                channel_id = EXCLUDED.channel_id,
                self_star = EXCLUDED.self_star,
                threshold = EXCLUDED.threshold
            """,
            ctx.guild.id,
            channel.id,
            flags.self_star,
            flags.threshold,
            emoji,
        )
        return await ctx.approve(f"Added a starboard for {emoji} in {channel.mention}")

    @starboard.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_guild=True)
    async def starboard_remove(
        self,
        ctx: Context,
        channel: TextChannel,
        emoji: str,
    ) -> Message:
        """Remove a starboard from a channel."""

        result = await self.bot.db.execute(
            """
            DELETE FROM starboard
            WHERE guild_id = $1
            AND channel_id = $2
            AND emoji = $3
            """,
            ctx.guild.id,
            channel.id,
            emoji,
        )
        if result == "DELETE 0":
            return await ctx.warn(
                f"A starboard for **{emoji}** in {channel.mention} doesn't exist"
            )

        return await ctx.approve(
            f"Removed the starboard for {emoji} in {channel.mention}"
        )

    @starboard.command(name="clear", aliases=("reset", "purge"))
    @has_permissions(manage_guild=True)
    async def starboard_clear(self, ctx: Context) -> Message:
        """Remove all starboards."""

        result = await self.bot.db.execute(
            """
            DELETE FROM starboard
            WHERE guild_id = $1
            """,
            ctx.guild.id,
        )
        if result == "DELETE 0":
            return await ctx.warn("No starboards exist for this server")

        return await ctx.approve(
            f"Successfully removed {plural(result, md='`'):starboard}"
        )

    @starboard.command(name="list")
    @has_permissions(manage_guild=True)
    async def starboard_list(self, ctx: Context) -> Message:
        """View all starboards for this server."""

        query = "SELECT * FROM starboard WHERE guild_id = $1"
        records = cast(List[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} - **{record['emoji']}** (threshold: `{record['threshold']}`, author: `{record['self_star']}`)"
            for record in records
            if (channel := ctx.guild.get_channel(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No channels have a starboard configured")

        embed = Embed(title="Starboards")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()

    async def get_starboard(self, guild: Guild, emoji: str) -> Optional[Config]:
        query = "SELECT * FROM starboard WHERE guild_id = $1 AND emoji = $2"
        record = cast(
            Optional[Record],
            await self.bot.db.fetchrow(query, guild.id, emoji),
        )
        if record:
            return Config(bot=self.bot, record=record)

    async def reaction_action(
        self,
        fmt: str,
        payload: RawReactionActionEvent,
    ):
        guild = payload.guild_id and self.bot.get_guild(payload.guild_id)
        if not guild or guild.me.is_timed_out():
            return

        channel = guild.get_channel_or_thread(payload.channel_id)
        if not isinstance(channel, (TextChannel, Thread)):
            return

        starboard = await self.get_starboard(guild, str(payload.emoji))
        if (
            not starboard
            or not starboard.channel
            or starboard.channel == channel
            or not starboard.channel.permissions_for(guild.me).send_messages
            or not starboard.channel.permissions_for(guild.me).embed_links
        ):
            return

        member = guild.get_member(payload.user_id)
        if not member:
            return

        message = self.bot.get_message(payload.message_id)
        if not message:
            try:
                message = await channel.fetch_message(payload.message_id)
            except HTTPException:
                return

        lock = self.bot.redis.get_lock(f"starboard:{guild.id}")
        async with lock:
            method = getattr(self, f"{fmt}_message")

            await method(
                starboard,
                channel=channel,
                member=member,
                message=message,
            )

    async def star_message(
        self,
        starboard: CompleteConfig,
        *,
        channel: TextChannel | Thread,
        member: Member,
        message: Message,
    ):
        if channel.is_nsfw() and not starboard.channel.is_nsfw():
            return

        if message.author.id == member.id and not starboard.self_star:
            return

        reaction = find(
            lambda reaction: str(reaction.emoji) == starboard.emoji,
            message.reactions,
        )
        if not reaction or reaction.count < starboard.threshold:
            return

        await starboard.save_star(
            stars=reaction.count,
            message=message,
        )

    async def unstar_message(
        self,
        starboard: CompleteConfig,
        *,
        channel: TextChannel | Thread,
        member: Member,
        message: Message,
    ):
        star_message = await starboard.get_star(message)
        if not star_message:
            return

        reaction = find(
            lambda reaction: str(reaction.emoji) == starboard.emoji,
            message.reactions,
        )
        if not reaction or reaction.count < starboard.threshold:
            await quietly_delete(star_message)

            await self.bot.db.execute(
                """
                DELETE FROM starboard_entry
                WHERE star_id = $1
                """,
                star_message.id,
            )
            return

        await starboard.save_star(
            stars=reaction.count,
            message=message,
        )

    @Cog.listener("on_guild_channel_delete")
    async def starboard_channel_delete(self, channel: GuildChannel):
        await self.bot.db.execute(
            """
            DELETE FROM starboard
            WHERE guild_id = $1
            AND channel_id = $2
            """,
            channel.guild.id,
            channel.id,
        )
        await self.bot.db.execute(
            """
            DELETE FROM starboard_entry
            WHERE guild_id = $1
            AND channel_id = $2
            """,
            channel.guild.id,
            channel.id,
        )

    @Cog.listener("on_raw_reaction_clear")
    async def starboard_reaction_clear(self, payload: RawReactionClearEmojiEvent):
        entries = await self.bot.db.fetch(
            """
            DELETE FROM starboard_entry
            WHERE guild_id = $1
            AND channel_id = $2
            AND message_id = $3
            RETURNING star_id, emoji
            """,
            payload.guild_id,
            payload.channel_id,
            payload.message_id,
        )
        if not entries:
            return

        for entry in entries:
            if not payload.guild_id or not (
                guild := self.bot.get_guild(payload.guild_id)
            ):
                continue

            starboard = await self.get_starboard(guild, entry["emoji"])
            if not starboard or not starboard.channel:
                continue

            star_message = starboard.channel.get_partial_message(entry["star_id"])
            await quietly_delete(star_message)

    @Cog.listener("on_raw_reaction_add")
    async def starboard_reaction_add(self, payload: RawReactionActionEvent):
        await self.reaction_action("star", payload)

    @Cog.listener("on_raw_reaction_remove")
    async def starboard_reaction_remove(self, payload: RawReactionActionEvent):
        await self.reaction_action("unstar", payload)
