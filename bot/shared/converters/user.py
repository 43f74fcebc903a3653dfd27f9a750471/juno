import re

from discord import Member, User
from discord.ext.commands import (
    BadArgument,
    MemberConverter,
    MemberNotFound,
    UserConverter,
    UserNotFound,
)

from bot.core import Context


class StrictUser(User):
    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> User:
        if ctx.command.name.startswith("purge"):
            pattern = r"<@!?\d+>$"
        else:
            pattern = r"\d+$|<@!?\d+>$"

        if re.match(pattern, argument):
            return await UserConverter().convert(ctx, argument)

        raise UserNotFound(argument)


class StrictMember(Member):
    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Member:
        if ctx.command.name.startswith("purge"):
            pattern = r"<@!?\d+>$"
        else:
            pattern = r"\d+$|<@!?\d+>$"

        if re.match(pattern, argument):
            return await MemberConverter().convert(ctx, argument)

        raise MemberNotFound(argument)


class HierarchyMember(Member):
    """Check if a member can be punished."""

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Member:
        member = await MemberConverter().convert(ctx, argument)
        command = ctx.command.qualified_name
        self_commands = ("nickname", "role")

        if member == ctx.author and not command.startswith(self_commands):
            raise BadArgument(f"You aren't able to {command} yourself")

        elif member == ctx.guild.me:
            raise BadArgument(f"I'm not able to {command} myself")

        elif (
            member.top_role >= ctx.guild.me.top_role
            and ctx.guild.me.id != ctx.guild.owner_id
        ):
            raise BadArgument(f"I can't {command} someone with a higher role than me")

        elif ctx.author.id == ctx.guild.owner_id:
            return member

        elif member.id == ctx.guild.owner_id:
            raise BadArgument(f"You can't {command} the server owner")

        elif member.top_role > ctx.author.top_role:
            raise BadArgument(
                f"You can't {command} someone with a higher role than you"
            )

        elif member.top_role == ctx.author.top_role:
            raise BadArgument(f"You can't {command} someone with the same role as you")

        return member
