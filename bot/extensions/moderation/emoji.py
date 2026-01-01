import asyncio
from datetime import timedelta
from io import BytesIO
from random import randint
from typing import List, Optional

import discord
from discord import File, HTTPException, Message, PartialEmoji, RateLimited
from discord.ext.commands import (
    BucketType,
    Cog,
    Range,
    cooldown,
    group,
    has_permissions,
    parameter,
)
from discord.utils import format_dt, utcnow
from xxhash import xxh128_hexdigest
from yarl import URL

from bot.core import Context, Juno
from bot.shared import codeblock
from bot.shared.converters import PartialAttachment
from bot.shared.formatter import plural

seed = randint(0, 2**64 - 1)


async def determine_hash(emoji: discord.Emoji) -> tuple[discord.Emoji, str]:
    buffer = await emoji.read()
    return emoji, xxh128_hexdigest(buffer, seed=seed)


class Emoji(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @group(aliases=("emote",), invoke_without_command=True)
    async def emoji(self, ctx: Context) -> Message:
        """Various emoji management commands."""

        return await ctx.send_help(ctx.command)

    @emoji.group(
        name="create",
        aliases=("add", "upload", "steal"),
        invoke_without_command=True,
    )
    @has_permissions(manage_emojis=True)
    async def emoji_create(
        self,
        ctx: Context,
        image: Optional[PartialEmoji | PartialAttachment] = parameter(
            default=PartialAttachment.fallback
        ),
        *,
        name: Optional[Range[str, 2, 32]],
    ) -> Message:
        """Add an emoji to the server."""

        if not image:
            return await ctx.send_help(ctx.command)

        elif len(ctx.guild.emojis) == ctx.guild.emoji_limit:
            return await ctx.warn(
                "This server has reached the maximum amount of emojis"
            )

        elif isinstance(image, PartialAttachment) and image.format != "image":
            return await ctx.warn("The attachment provided isn't an image")

        buffer = await image.read()
        try:
            emoji = await ctx.guild.create_custom_emoji(
                name=name
                or (
                    image.name
                    if isinstance(image, PartialEmoji)
                    else image.filename.split(".")[0].replace(" ", "_")[:32]
                ),
                image=buffer,
                reason=f"Uploaded by {ctx.author} ({ctx.author.id})",
            )
        except RateLimited as exc:
            expires_at = utcnow() + timedelta(seconds=exc.retry_after)
            return await ctx.warn(
                f"The server has been rate limited by Discord, try again {format_dt(expires_at, 'R')}"
            )
        except HTTPException as exc:
            return await ctx.warn(
                "An error occurred while uploading the emoji",
                codeblock(exc.text),
            )

        return await ctx.respond(f"Uploaded `{emoji.name}` {emoji}")

    @emoji_create.command(name="reactions", aliases=("reaction", "reacts", "react"))
    @has_permissions(manage_emojis=True)
    async def emoji_create_reactions(
        self,
        ctx: Context,
        message: Optional[Message],
    ) -> Optional[Message]:
        """Add emojis from a message's reactions."""

        message = message or ctx.replied_message
        if not message:
            async for _message in ctx.channel.history(limit=25, before=ctx.message):
                if not _message.reactions:
                    continue

                message = _message
                break

        if not message:
            return await ctx.send_help(ctx.command)

        elif not message.reactions:
            return await ctx.warn("The message provided doesn't have any reactions")

        added_emojis: List[discord.Emoji] = []
        async with ctx.typing():
            for reaction in message.reactions:
                if not reaction.is_custom_emoji():
                    continue

                emoji = reaction.emoji
                if isinstance(emoji, str):
                    continue

                elif getattr(emoji, "guild_id", 0) == ctx.guild.id:
                    continue

                buffer = await emoji.read()
                try:
                    emoji = await ctx.guild.create_custom_emoji(
                        name=emoji.name,
                        image=buffer,
                        reason=f"Uploaded by {ctx.author} ({ctx.author.id})",
                    )
                except RateLimited as exc:
                    expires_at = utcnow() + timedelta(seconds=exc.retry_after)
                    return await ctx.warn(
                        f"The server has been rate limited by Discord after adding {plural(added_emojis, md='`'):emoji}, try again {format_dt(expires_at, 'R')}"
                    )

                except HTTPException:
                    if len(ctx.guild.emojis) == ctx.guild.emoji_limit:
                        await ctx.warn(
                            "This server has reached the maximum amount of emojis"
                        )

                    break

                added_emojis.append(emoji)

        return await ctx.respond(
            f"Added {plural(added_emojis, md='`'):emoji} from {message.jump_url}"
            + (
                f" (`{len(message.reactions) - len(added_emojis)}` failed)"
                if len(added_emojis) < len(message.reactions)
                else ""
            )
        )

    @emoji_create.command(name="many", aliases=("bulk", "batch"))
    @has_permissions(manage_emojis=True)
    async def emoji_create_many(
        self,
        ctx: Context,
        *emojis: PartialEmoji,
    ) -> Optional[Message]:
        """Add multiple emojis to the server."""

        if not emojis:
            return await ctx.send_help(ctx.command)

        elif len(emojis) >= 50:
            return await ctx.warn("You can only add up to 50 emojis at a time")

        elif len(ctx.guild.emojis) + len(emojis) > ctx.guild.emoji_limit:
            return await ctx.warn(
                "This server doesn't have enough space for all the emojis"
            )

        added_emojis: List[discord.Emoji] = []
        async with ctx.typing():
            for emoji in emojis:
                if not emoji.is_custom_emoji():
                    continue

                elif getattr(emoji, "guild_id", 0) == ctx.guild.id:
                    continue

                buffer = await emoji.read()
                try:
                    emoji = await ctx.guild.create_custom_emoji(
                        name=emoji.name,
                        image=buffer,
                        reason=f"Uploaded by {ctx.author} ({ctx.author.id})",
                    )
                except RateLimited as exc:
                    expires_at = utcnow() + timedelta(seconds=exc.retry_after)
                    return await ctx.warn(
                        f"The server has been rate limited by Discord after adding {plural(added_emojis, md='`'):emoji}, try again {format_dt(expires_at, 'R')}"
                    )

                except HTTPException:
                    if len(ctx.guild.emojis) == ctx.guild.emoji_limit:
                        await ctx.warn(
                            "This server has reached the maximum amount of emojis"
                        )

                    break

                added_emojis.append(emoji)

        return await ctx.respond(
            f"Added {plural(added_emojis, md='`'):emoji}: {' '.join(map(str, added_emojis))}"
            + (
                f" (`{len(emojis) - len(added_emojis)}` failed)"
                if len(added_emojis) < len(emojis)
                else ""
            )
        )

    @emoji.group(
        name="delete",
        aliases=("del", "remove", "rm"),
        invoke_without_command=True,
    )
    @has_permissions(manage_emojis=True)
    async def emoji_delete(
        self,
        ctx: Context,
        emoji: discord.Emoji,
    ) -> Optional[Message]:
        """Remove an emoji from the server."""

        if emoji.guild_id != ctx.guild.id:
            return await ctx.warn("That emoji isn't from this server")

        await emoji.delete(reason=f"Deleted by {ctx.author} ({ctx.author.id})")
        await ctx.add_check()

    @emoji_delete.command(name="duplicates", aliases=("dupes", "dupe"))
    @has_permissions(manage_emojis=True)
    @cooldown(1, 60, BucketType.guild)
    async def emoji_delete_duplicates(self, ctx: Context) -> Optional[Message]:
        """Remove all duplicates of an emoji from the server."""

        message = await ctx.send("Determining emoji duplicate hashes...")
        hashes: dict[str, List[discord.Emoji]] = {}

        for emoji, hash in await asyncio.gather(*map(determine_hash, ctx.guild.emojis)):
            if hash not in hashes:
                hashes[hash] = []

            hashes[hash].append(emoji)

        duplicates = {
            hash: emojis for hash, emojis in hashes.items() if len(emojis) > 1
        }
        if not duplicates:
            return await message.edit(content="No duplicate emojis were found")

        await message.edit(
            content=f"Starting to delete duplicates of {plural(len(duplicates), md='`'):emoji}..."
        )
        async with ctx.typing():
            for emojis in duplicates.values():
                for duplicate in emojis[1:]:
                    await duplicate.delete(
                        reason=f"Deleted by {ctx.author} ({ctx.author.id}) after being found as a duplicate of {emojis[0]}"
                    )

        return await message.edit(
            content=f"Removed {plural(sum(map(len, duplicates.values())), md='`'):duplicate emoji} from the server\n>>> "
            + "\n".join(
                [f"`{hash}`: {emojis[0]}" for hash, emojis in duplicates.items()]
            )
        )

    @emoji.command(name="rename", aliases=("edit", "update"))
    @has_permissions(manage_emojis=True)
    async def emoji_rename(
        self,
        ctx: Context,
        emoji: discord.Emoji,
        *,
        name: Range[str, 2, 32],
    ) -> Optional[Message]:
        """Rename an emoji in the server."""

        if emoji.guild_id != ctx.guild.id:
            return await ctx.warn("That emoji isn't from this server")

        name = name.replace(" ", "_")
        await emoji.edit(name=name, reason=f"Renamed by {ctx.author} ({ctx.author.id})")
        return await ctx.add_check()

    @emoji.command(name="remix", aliases=("combine", "merge"))
    async def emoji_remix(self, ctx: Context, *, emojis: str) -> Optional[Message]:
        """Combine two emojis into a single emoji."""

        emojis = [
            emoji
            for emoji in list("".join(char for char in emojis if char != "\ufe0f"))
            if emoji.strip()
        ]  # type: ignore
        if len(emojis) != 2:
            return await ctx.warn("You need to provide exactly two emojis to remix")

        response = await self.bot.session.get(
            URL.build(
                scheme="https",
                host="tenor.googleapis.com",
                path="/v2/featured",
                query={
                    "key": "AIzaSyACvEq5cnT7AcHpDdj64SE3TJZRhW-iHuo",
                    "client_key": "emoji_kitchen_funbox",
                    "q": "_".join(emojis),
                    "collection": "emoji_kitchen_v6",
                    "contentfilter": "high",
                },
            )
        )
        data = await response.json()
        if not data["results"]:
            return await ctx.warn("Those emojis aren't able to be combined")

        response = await self.bot.session.get(data["results"][0]["url"])
        buffer = await response.read()
        return await ctx.reply(file=File(BytesIO(buffer), "emoji.png"))
