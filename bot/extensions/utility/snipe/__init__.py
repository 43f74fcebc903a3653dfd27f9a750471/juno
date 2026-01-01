from discord import Message, Reaction, User
from discord.ext.commands import (
    BucketType,
    Cog,
    cooldown,
    group,
    command,
    has_permissions,
    is_owner,
)
from discord.utils import as_chunks, format_dt

from bot.core import Context, Juno

from .model import MessageSnipe, ReactionSnipe


class Snipe(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @Cog.listener("on_message_delete")
    async def snipe_listener(self, message: Message) -> None:
        """Push a message to the snipe cache when it is deleted."""

        if not message.guild or message.author.bot:
            return

        await MessageSnipe.save(self.bot, message)

    @Cog.listener("on_message_edit")
    async def edit_snipe_listener(self, before: Message, after: Message) -> None:
        """Push a message to the snipe cache when it is edited."""

        if not before.guild or before.author.bot:
            return

        await MessageSnipe.save(self.bot, before, edited=True)

    @Cog.listener("on_reaction_remove")
    async def reaction_snipe_listener(self, reaction: Reaction, user: User) -> None:
        """Push a reaction to the snipe cache when it is removed."""

        await ReactionSnipe.push(self.bot, reaction, user)

    @group(aliases=("sn", "s"), invoke_without_command=True)
    @cooldown(2, 3, BucketType.member)
    async def snipe(self, ctx: Context, index: int = 1) -> Message:
        """Snipe a deleted message from the channel."""

        message = await MessageSnipe.get(self.bot, ctx.channel.id, index)
        if not message:
            return await ctx.warn(
                "No messages have been deleted recently"
                if index == 1
                else f"No message is available at index `{index}`"
            )

        if (
            not ctx.channel.permissions_for(ctx.author).manage_messages
            and ctx.author.id not in self.bot.config.owner_ids
        ):
            if message.filtered:
                return await ctx.reply("my muddy got filtered ...")

        async with ctx.typing():
            embed = message.embed.set_footer(text=f"Message {index}/{message.total}")
            files = [attachment.file for attachment in message.attachments]
            return await ctx.send(embed=embed, files=files)

    @snipe.command(name="edit", aliases=("edited", "e"))
    @cooldown(2, 3, BucketType.member)
    async def snipe_edit(self, ctx: Context, index: int = 1) -> Message:
        """Snipe an edited message from the channel."""

        message = await MessageSnipe.get(self.bot, ctx.channel.id, index, edited=True)
        if not message:
            return await ctx.warn(
                "No messages have been edited recently"
                if index == 1
                else f"No message is available at index `{index}`"
            )

        if (
            not ctx.channel.permissions_for(ctx.author).manage_messages
            and ctx.author.id not in self.bot.config.owner_ids
        ):
            if message.filtered:
                return await ctx.reply("my muddy got filtered ...")

        async with ctx.typing():
            embed = message.embed.set_footer(text=f"Message {index}/{message.total}")
            files = [attachment.file for attachment in message.attachments]
            return await ctx.send(embed=embed, files=files)
        
    @snipe.command(name="reaction", aliases=("react", "r"))
    @cooldown(2, 3, BucketType.member)
    async def snipe_reaction(self, ctx: Context, index: int = 1) -> Message:
        """Snipe a removed reaction from the channel."""

        reaction = await ReactionSnipe.get(self.bot, ctx.channel.id)
        if not reaction:
            return await ctx.warn(
                "No reactions have been removed recently"
                if index == 1
                else f"No reaction is available at index `{index}`"
            )

        return await ctx.respond(
            f"<@{reaction.user_id}> removed **{reaction.emoji}** [{format_dt(reaction.removed_at, 'R')}]({reaction.message_url})",
            reference=ctx.channel.get_partial_message(reaction.message_id),
        )

    @snipe.command(name="clear", aliases=("c",))
    @has_permissions(manage_messages=True)
    async def snipe_clear(self, ctx: Context) -> None:
        """Remove all sniped messages from the cache."""

        for table in ("message", "edited_message"):
            query = f"DELETE FROM snipe.{table} WHERE channel_id = $1"
            await self.bot.db.execute(query, ctx.channel.id)

        rs_key = ReactionSnipe.key(ctx.channel.id)
        await self.bot.redis.delete(rs_key)
        return await ctx.add_check()

    @snipe.command(name="dump", aliases=("all",), hidden=True)
    @is_owner()
    async def snipe_dump(self, ctx: Context, *, user: User) -> None:
        """Dump all sniped messages from the cache."""

        query = """
        SELECT * FROM snipe.message
        WHERE user_id = $1
        ORDER BY created_at DESC
        """
        records = await self.bot.db.fetch(query, user.id)
        for chunk in as_chunks(records, 20):
            await ctx.send(
                "\n".join(
                    f"`{user}` {record['content']}"
                    for record in chunk
                    if record["content"]
                )
            )

        return await ctx.add_check()

    @command(aliases=("edits", "esnipe", "es"), hidden=True)
    @cooldown(1, 3, BucketType.channel)
    async def editsnipe(self, ctx: Context, index: int = 1) -> Message:
        """Snipe an edited message from the channel."""

        return await self.snipe_edit(ctx, index)

    @command(aliases=("reactsnipe", "rsnipe", "rs"), hidden=True)
    @cooldown(1, 3, BucketType.channel)
    async def reactionsnipe(self, ctx: Context, index: int = 1) -> Message:
        """Snipe a removed reaction from the channel."""

        return await self.snipe_reaction(ctx, index)
