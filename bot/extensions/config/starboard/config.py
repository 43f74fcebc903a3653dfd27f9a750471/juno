from logging import getLogger
from sys import getsizeof
from typing import List, Optional, Tuple, TypedDict, cast

from discord import (
    Attachment,
    DeletedReferencedMessage,
    Embed,
    File,
    Guild,
    HTTPException,
    Message,
    PartialMessage,
    StickerItem,
    TextChannel,
)

from bot.core import Juno
from bot.shared.formatter import plural, shorten

logger = getLogger("bot.starboard")


class Record(TypedDict):
    guild_id: int
    channel_id: int
    self_star: bool
    threshold: int
    emoji: str


class Config:
    bot: Juno
    guild_id: int
    channel_id: int
    self_star: bool
    threshold: int
    emoji: str

    def __init__(self, *, bot: Juno, record: Record):
        self.bot = bot
        self.guild_id = record["guild_id"]
        self.channel_id = record["channel_id"]
        self.self_star = record["self_star"]
        self.threshold = record["threshold"]
        self.emoji = record["emoji"]

    @property
    def guild(self) -> Guild:
        return self.bot.get_guild(self.guild_id)  # type: ignore

    @property
    def channel(self) -> Optional[TextChannel]:
        return self.guild and self.guild.get_channel(self.channel_id)  # type: ignore

    async def build_entry(
        self,
        message: Message,
        stars: int,
    ) -> Tuple[
        str,
        Embed,
        List[File],
    ]:
        channel = cast(TextChannel, message.channel)
        author = message.author

        embed = Embed()
        if message.embeds:
            embed = cast(Embed, message.embeds[0])

        embed.timestamp = message.created_at
        embed.set_author(
            name=author.display_name,
            icon_url=author.display_avatar,
            url=message.jump_url,
        )

        if embed.type in ("image", "gifv"):
            embed.set_image(url=embed.thumbnail.url)
            embed.set_thumbnail(url=None)

        if embed.description and message.system_content:
            embed.description = shorten(
                "\n\n".join(
                    [
                        message.system_content,
                        embed.description,
                    ]
                ),
                4096,
            )
        else:
            embed.description = shorten(message.system_content, 4096)

        files: List[File] = []
        if message.attachments and message.guild:
            attachment = cast(Attachment, message.attachments[0])

            if attachment.content_type and attachment.content_type.startswith("image"):
                embed.set_image(url=attachment.url)
            else:
                for attachment in message.attachments:
                    file = await attachment.to_file()
                    if not getsizeof(file.fp) > message.guild.filesize_limit:
                        files.append(file)

                    if (
                        sum(getsizeof(file.fp) for file in files)
                        > message.guild.filesize_limit
                    ):
                        files.pop()
                        break

                embed.add_field(
                    name=f"Attachment{'s' if len(message.attachments) != 1 else ''}",
                    value="\n".join(
                        f"[{attachment.filename}]({attachment.url})"
                        for attachment in message.attachments
                    ),
                    inline=False,
                )

        elif message.stickers:
            sticker = cast(StickerItem, message.stickers[0])
            embed.set_image(url=sticker.url)

        if (
            (reference := message.reference)
            and (resolved := reference.resolved)
            and not isinstance(resolved, DeletedReferencedMessage)
        ):
            embed.add_field(
                name=f"Replying to {resolved.author.display_name}",
                value=(
                    f">>> [{shorten(resolved.system_content, 950)}]({resolved.jump_url})"
                    if resolved.system_content
                    else f"> [Jump to replied message]({resolved.jump_url})"
                ),
                inline=False,
            )

        embed.add_field(
            name=f"#{channel}",
            value=f"[Jump to message]({message.jump_url})",
            inline=False,
        )

        return f"{self.emoji} **#{stars:,}**", embed, files

    async def get_star(self, message: Message) -> Optional[PartialMessage]:
        if not self.channel:
            return

        star_id = cast(
            Optional[int],
            await self.bot.db.fetchval(
                """
                SELECT star_id
                FROM starboard_entry
                WHERE guild_id = $1
                AND channel_id = $2
                AND message_id = $3
                AND emoji = $4
                """,
                self.guild_id,
                message.channel.id,
                message.id,
                self.emoji,
            ),
        )
        if star_id:
            return self.channel.get_partial_message(star_id)

    async def save_star(
        self,
        stars: int,
        message: Message,
    ) -> Optional[Message]:
        if not self.channel:
            return

        content, embed, files = await self.build_entry(message, stars)

        star_message = await self.get_star(message)
        if star_message:
            try:
                star_message = await star_message.edit(content=content)
            except HTTPException:
                pass
            else:
                return star_message

        star_message = await self.channel.send(
            content=content,
            embed=embed,
            files=files,
        )

        await self.bot.db.execute(
            """
            INSERT INTO starboard_entry (
                guild_id,
                star_id,
                channel_id,
                message_id,
                emoji
            )
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (guild_id, channel_id, message_id, emoji)
            DO UPDATE SET star_id = EXCLUDED.star_id
            """,
            self.guild_id,
            star_message.id,
            message.channel.id,
            message.id,
            self.emoji,
        )

        logger.debug(
            "Saved entry for %s with %s in %s/%s (%s) to %s (%s).",
            message.id,
            format(plural(stars), "star"),
            message.channel,
            message.guild,
            self.guild.id,
            self.channel,
            self.channel.id,
        )
        return star_message
