from __future__ import annotations

from base64 import b64decode, b64encode
from contextlib import suppress
from datetime import datetime
from io import BytesIO
from typing import List, Optional

from discord import Embed, File, HTTPException, Message, Reaction, User
from discord.types.embed import Embed as EmbedData
from discord.utils import utcnow
from pydantic import BaseModel, ConfigDict
from xxhash import xxh64_hexdigest

from bot.core import Juno


class MessageAttachment(BaseModel):
    filename: str
    content_type: Optional[str]
    buffer: bytes

    def __str__(self) -> str:
        return self.filename

    def to_dict(self):
        return {
            "filename": self.filename,
            "content_type": self.content_type,
            "buffer": b64encode(self.buffer).decode("utf-8"),
        }

    @property
    def url(self) -> str:
        return f"attachment://{self.filename}"

    @property
    def file(self) -> File:
        buffer = BytesIO(b64decode(self.buffer))
        return File(buffer, filename=self.filename)


class MessageSnipe(BaseModel):
    bot: Juno
    guild_id: int
    channel_id: int
    message_id: int
    user_id: int
    content: str
    embeds: List[EmbedData] = []
    attachments: List[MessageAttachment] = []
    stickers: List[str] = []
    created_at: datetime
    deleted_at: Optional[datetime] = None
    edited_at: Optional[datetime] = None
    total: Optional[int] = None
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __bool__(self) -> bool:
        return any((self.content, self.embeds, self.attachments, self.stickers))

    @property
    def filtered(self) -> bool:
        """Check if a bot filtered the message."""

        ts = self.deleted_at or self.edited_at
        if not ts:
            return False
        
        return (ts - self.created_at).total_seconds() < 0.3

    @property
    def user(self) -> Optional[User]:
        return self.bot.get_user(self.user_id)

    @property
    def embed(self) -> Embed:
        embed = Embed(description=self.content, timestamp=self.deleted_at or self.edited_at)
        embed.set_author(name=f"Unknown User ({self.user_id})")
        if self.user:
            embed.set_author(
                name=self.user.display_name,
                icon_url=self.user.display_avatar,
            )

        for attachment in self.attachments:
            embed.set_image(url=attachment.url)
            break

        if self.stickers:
            embed.set_image(url=self.stickers[0])
            embed.add_field(
                name=f"Sticker{'s' if len(self.stickers) > 1 else ''}",
                value="\n".join(self.stickers),
            )

        return embed

    @classmethod
    async def from_message(cls, bot: Juno, message: Message) -> MessageSnipe:
        assert message.guild is not None

        attachments: List[MessageAttachment] = []
        with suppress(HTTPException):
            attachments = [
                MessageAttachment(
                    filename=attachment.filename,
                    content_type=attachment.content_type,
                    buffer=await attachment.read(),
                )
                for attachment in message.attachments
                if attachment.content_type
                and attachment.content_type.startswith("image")
            ]

        return cls(
            bot=bot,
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            message_id=message.id,
            user_id=message.author.id,
            content=message.content,
            attachments=attachments,
            embeds=[embed.to_dict() for embed in message.embeds],
            stickers=[sticker.url for sticker in message.stickers],
            created_at=message.created_at,
            deleted_at=utcnow(),
            edited_at=message.edited_at,
        )

    @classmethod
    async def save(cls, bot: Juno, message: Message, edited: bool = False) -> MessageSnipe:
        snipe = await cls.from_message(bot, message)
        if not snipe:
            return snipe
        
        table = "edited_message" if edited else "message"
        query = f"""
        INSERT INTO snipe.{table} (
            guild_id,
            channel_id,
            message_id,
            user_id,
            content,
            embeds,
            attachments,
            stickers,
            created_at,
            {'edited_at' if edited else 'deleted_at'}
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """
        await bot.db.execute(
            query,
            snipe.guild_id,
            snipe.channel_id,
            snipe.message_id,
            snipe.user_id,
            snipe.content,
            snipe.embeds,
            [attachment.to_dict() for attachment in snipe.attachments],
            snipe.stickers,
            snipe.created_at,
            snipe.edited_at or snipe.deleted_at,
        )
        return snipe

    @classmethod
    async def get(
        cls,
        bot: Juno,
        channel_id: int,
        index: int = 1,
        edited: bool = False,
    ) -> Optional[MessageSnipe]:
        index = max(0, abs(index) - 1)
        table = "edited_message" if edited else "message"
        query = f"""
        WITH total_count AS (
            SELECT COUNT(*) AS total
            FROM snipe.{table}
            WHERE channel_id = $1
        )
        SELECT m.*, t.total
        FROM snipe.{table} m, total_count t
        WHERE m.channel_id = $1
        ORDER BY m.{'deleted' if not edited else 'edited'}_at DESC
        OFFSET $2
        LIMIT 1
        """
        record = await bot.db.fetchrow(query, channel_id, index)
        if not record:
            return None

        return cls(bot=bot, **record)

class ReactionSnipe(BaseModel):
    guild_id: int
    channel_id: int
    message_id: int
    user_id: int
    user_name: str
    removed_at: datetime
    emoji: str

    @property
    def message_url(self) -> str:
        return f"https://discord.com/channels/{self.guild_id}/{self.channel_id}/{self.message_id}"

    @staticmethod
    def key(channel_id: int) -> str:
        return "rsnipe:" + xxh64_hexdigest(str(channel_id))

    @classmethod
    async def push(cls, bot: Juno, reaction: Reaction, user: User) -> Optional[ReactionSnipe]:
        if not reaction.message.guild:
            return

        data = cls(
            guild_id=reaction.message.guild.id,
            channel_id=reaction.message.channel.id,
            message_id=reaction.message.id,
            user_id=user.id,
            user_name=user.name,
            removed_at=utcnow(),
            emoji=str(reaction.emoji),
        )

        key = cls.key(reaction.message.channel.id)
        cache_size = await bot.redis.rpush(key, data.json())
        if cache_size > 100:
            await bot.redis.ltrim(key, -49, -1)

        await bot.redis.expire(key, 14400)
        return data

    @classmethod
    async def get(cls, bot: Juno, channel_id: int, index: int = 1) -> Optional[ReactionSnipe]:
        key = cls.key(channel_id)
        if not await bot.redis.llen(key):
            return

        index = -index
        snipes = await bot.redis.lrange(key, index, index)
        if not snipes:
            return

        return cls.model_validate_json(snipes[0])
    
MessageSnipe.update_forward_refs()
