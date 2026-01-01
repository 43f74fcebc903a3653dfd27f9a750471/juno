from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from logging import getLogger
from secrets import token_urlsafe
from typing import TYPE_CHECKING, Optional

from discord import Embed, Guild, HTTPException, Member, Message, TextChannel, Thread, User
from discord.types.embed import Embed as EmbedData
from pydantic import BaseModel

from bot.core import Juno
from bot.shared.formatter import plural
from config import config

if TYPE_CHECKING:
    from .settings import Settings, Ticket

logger = getLogger("bot.tickets")


class UserProxy(BaseModel):
    id: int
    bot: bool
    avatar: str
    username: str

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UserProxy):
            return NotImplemented
        return self.id == other.id
    
    def __hash__(self) -> int:
        return hash(self.id)
    
    @property
    def mention(self) -> str:
        return f"<@{self.id}>"
    
    @classmethod
    def from_user(cls, user: Member | User) -> UserProxy:
        return cls(
            id=user.id,
            bot=user.bot,
            avatar=user.display_avatar.key,
            username=user.name,
        )


class MessageProxy(BaseModel):
    id: int
    author: UserProxy
    mentions: list[UserProxy]
    content: Optional[str]
    embeds: list[EmbedData]
    attachments: int
    timestamp: float

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UserProxy):
            return NotImplemented
        return self.id == other.id
    
    def __hash__(self) -> int:
        return hash(self.id)
    
    @property
    def created_at(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, UTC)

    @classmethod
    def from_message(cls, message: Message) -> MessageProxy:
        author = UserProxy.from_user(message.author)
        mentions = [UserProxy.from_user(mention) for mention in message.mentions]
        return cls(
            id=message.id,
            author=author,
            mentions=mentions,
            content=message.clean_content or message.system_content,
            embeds=[embed.to_dict() for embed in message.embeds],
            attachments=len(message.attachments),
            timestamp=message.created_at.timestamp(),
        )


class Transcript(BaseModel):
    id: str
    guild_id: int
    channel_id: int
    user_id: int
    messages: list[MessageProxy]

    @property
    def url(self) -> str:
        return f"{config.backend.public_url}/transcripts/{self.id}"

    @classmethod
    async def create(
        cls,
        settings: Settings,
        guild: Guild,
        channel: TextChannel,
        ticket: Ticket,
    ) -> Optional[Transcript]:
        if not settings.transcript_destinations:
            return None

        transcript = cls(
            id=token_urlsafe(12),
            guild_id=guild.id,
            channel_id=channel.id,
            user_id=ticket["user_id"],
            messages=[],
        )
        async for message in channel.history(limit=500, oldest_first=True):
            transcript.messages.append(MessageProxy.from_message(message))

        query = """
        INSERT INTO tickets.transcript (
            id,
            guild_id,
            channel_id,
            user_id,
            messages
        ) VALUES ($1, $2, $3, $4, $5)
        """
        await settings.bot.db.execute(
            query,
            transcript.id,
            transcript.guild_id,
            transcript.channel_id,
            transcript.user_id,
            [message.model_dump() for message in transcript.messages],
        )
        logger.info(f"Created transcript {transcript.id} for ticket {ticket['id']}")

        user = guild.get_member(transcript.user_id)
        embed = Embed(
            url=transcript.url,
            title=f"Transcript of ticket {ticket['id']}",
            description=f"Click [here]({transcript.url}) to view the transcript",
        )
        embed.set_author(
            name=f"{user and user.display_name or 'Unknown User'} [{transcript.user_id}]",
            icon_url=user and user.display_avatar,
        )
        members = list({message.author for message in transcript.messages if not message.author.bot})
        if len(members) > 1:
            embed.add_field(
                name=format(plural(len(members)), "member"),
                value="\n".join(
                    [f"> {member.mention} [`{member.id}`]" for member in members[:10]],
                )
                + (
                    f"\n... and {plural(len(members) - 10):more}"
                    if len(members) > 10
                    else ""
                ),
                inline=False,
            )


        for destination in settings.transcript_destinations:
            if destination == "dm":
                user = guild.get_member(transcript.user_id)
                if user:
                    with suppress(HTTPException):
                        await user.send(embed=embed)

            elif isinstance(destination, (TextChannel, Thread)):
                with suppress(HTTPException):
                    await destination.send(embed=embed)

        return transcript

    @classmethod
    async def fetch(cls, bot: Juno, id: str) -> Optional[Transcript]:
        query = "SELECT * FROM tickets.transcript WHERE id = $1"
        record = await bot.db.fetchrow(query, id)
        if not record:
            return None

        return cls(
            id=record["id"],
            guild_id=record["guild_id"],
            channel_id=record["channel_id"],
            user_id=record["user_id"],
            messages=[MessageProxy(**message) for message in record["messages"]],
        )
