from typing import Optional, cast

from discord import TextChannel
from discord.ext.commands import CommandError, check

from bot.core import Context

from .settings import Settings, Ticket


class TicketContext(Context):
    ticket: Ticket
    channel: TextChannel


def in_ticket():
    """Check if the invoker is in a ticket channel."""

    async def predicate(ctx: TicketContext):
        query = "SELECT * FROM tickets.open WHERE channel_id = $1"
        ticket = cast(
            Optional[Ticket],
            await ctx.bot.db.fetchrow(query, ctx.channel.id),
        )
        if not ticket:
            raise CommandError("You are not in a ticket channel")

        ctx.ticket = ticket
        return True

    return check(predicate)


def staff():
    """Check if the invoker is a staff member."""

    async def predicate(ctx: TicketContext):
        settings = await Settings.fetch(ctx.bot, ctx.guild)
        if not settings.is_staff(ctx.author):
            raise CommandError("You are not a staff member")

        return True

    return check(predicate)
