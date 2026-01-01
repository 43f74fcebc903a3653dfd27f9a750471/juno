from __future__ import annotations

from contextlib import suppress
from datetime import datetime
from random import sample
from typing import List, Optional, Self

from discord import Embed, HTTPException, Member, Message, TextChannel, User
from discord.ext.commands import CommandError, MessageConverter
from discord.utils import format_dt, get, utcnow
from pydantic import BaseModel, ConfigDict

from bot.core import Context, Juno


class GiveawayRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    bot: Juno
    guild_id: int
    channel_id: int
    message_id: int = 0
    creator_id: int
    prize: str
    emoji: str
    winners: int
    ended: bool = False
    ends_at: datetime
    created_at: datetime = utcnow()

    def __str__(self) -> str:
        winners = f"{self.winners}x" if self.winners > 1 else ""
        return f"{winners} {self.prize}"

    def __eq__(self, other) -> bool:
        if not isinstance(other, GiveawayRecord):
            return NotImplemented

        return self.message_id == other.message_id

    @property
    def has_ended(self) -> bool:
        return self.ends_at <= utcnow() or self.ended

    @property
    def channel(self) -> Optional[TextChannel]:
        return self.bot.get_channel(self.channel_id)  # type: ignore

    @property
    def creator(self) -> Optional[User]:
        return self.bot.get_user(self.creator_id)

    @property
    def message_url(self) -> str:
        return f"https://discord.com/channels/{self.guild_id}/{self.channel_id}/{self.message_id}"

    def embed(self, winners: List[Member] = []) -> Embed:
        embed = Embed(title=str(self))
        if not self.has_ended:
            embed.description = (
                f"React with {self.emoji} to enter!"
                f"\n> Ends {format_dt(self.ends_at, 'R')}"
            )
        else:
            if not winners:
                embed.description = "No winner was drawn!"
            else:
                embed.description = f"Congratulations! {', '.join(winner.mention for winner in winners)} 🎉"

        if self.creator:
            embed.set_footer(text=f"Started by {self.creator}")

        return embed

    async def message(self) -> Optional[Message]:
        if (channel := self.channel) is not None:
            with suppress(HTTPException):
                return await channel.fetch_message(self.message_id)

    async def entrants(self, message: Message) -> List[Member]:
        reaction = get(message.reactions, emoji=self.emoji)
        if not reaction:
            return []

        return [
            member
            async for member in reaction.users()
            if not member.bot and isinstance(member, Member)
        ]

    async def draw_winners(self, message: Message) -> List[Member]:
        entrants = await self.entrants(message)
        if not entrants:
            return []

        with suppress(ValueError):
            return sample(entrants, self.winners)

        return []

    async def draw(self, message: Message) -> None:
        """Choose the winners and announce them."""

        await self.end()
        winners = await self.draw_winners(message)
        await message.edit(
            content="🎉 **GIVEAWAY ENDED** 🎉",
            embed=self.embed(winners),
        )
        if winners:
            await message.reply(
                f"Congratulations! {', '.join(winner.mention for winner in winners)} you won **{self.prize}** 🎉",
            )

    async def create(self, message: Message) -> Self:
        self.message_id = message.id

        query = """
        INSERT INTO giveaways (
            guild_id,
            channel_id,
            message_id,
            creator_id,
            prize,
            emoji,
            winners,
            ends_at,
            created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """
        await self.bot.db.execute(
            query,
            self.guild_id,
            self.channel_id,
            self.message_id,
            self.creator_id,
            self.prize,
            self.emoji,
            self.winners,
            self.ends_at,
            self.created_at,
        )
        return self

    async def end(self) -> None:
        self.ended = True
        query = "UPDATE giveaways SET ended = $1 WHERE message_id = $2"
        await self.bot.db.execute(query, self.ended, self.message_id)

    @classmethod
    async def fetch(cls, ctx: Context, message: Message) -> Optional[Self]:
        query = "SELECT * FROM giveaways WHERE message_id = $1"
        record = await ctx.bot.db.fetchrow(query, message.id)
        if record:
            return cls(bot=ctx.bot, **record)

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Self:
        message = await MessageConverter().convert(ctx, argument)
        if message.guild != ctx.guild:
            raise ValueError("The message must be from this server")

        record = await cls.fetch(ctx, message)
        if not record:
            raise ValueError("That message is not a giveaway")

        return record

    @classmethod
    async def fallback(cls, ctx: Context) -> Self:
        """A fallback for when conversion fails."""

        if not ctx.replied_message:
            query = """
            SELECT *
            FROM giveaways
            WHERE guild_id = $1
            order by created_at DESC
            """
            record = await ctx.bot.db.fetchrow(query, ctx.guild.id)
            if record:
                return cls(bot=ctx.bot, **record)

            raise ValueError("No giveaways have been created yet")

        with suppress(CommandError):
            return await cls.convert(ctx, ctx.replied_message.jump_url)

        raise CommandError("No giveaway was found in the replied message")
