from contextlib import suppress

import discord.utils
from discord import Role
from discord.ext.commands import BadArgument, CommandError, RoleConverter, RoleNotFound

from bot.core import Context


class FuzzyRole(RoleConverter):
    async def convert(self, ctx: Context, argument: str) -> Role:
        with suppress(CommandError):
            return await RoleConverter().convert(ctx, argument)

        def predicate(role: Role) -> bool:
            return (
                role.name.lower() == argument.lower()
                or role.name.lower() in argument.lower()
            )

        role = discord.utils.find(predicate, ctx.guild.roles)
        if not role:
            raise RoleNotFound(argument)

        return role


class StrictRole(FuzzyRole):
    check_dangerous: bool
    check_integrated: bool
    allow_default: bool

    def __init__(
        self,
        *,
        check_dangerous: bool = False,
        check_integrated: bool = True,
        allow_default: bool = False,
    ) -> None:
        self.check_dangerous = check_dangerous
        self.check_integrated = check_integrated
        self.allow_default = allow_default

    @staticmethod
    def dangerous(role: Role) -> bool:
        return any(
            value
            and permission
            in (
                "administrator",
                "kick_members",
                "ban_members",
                "manage_guild",
                "manage_roles",
                "manage_channels",
                "manage_emojis",
                "manage_webhooks",
                "manage_nicknames",
                "mention_everyone",
            )
            for permission, value in role.permissions
        )

    async def convert(self, ctx: Context, argument: str) -> Role:
        role = await super().convert(ctx, argument)
        if not self.allow_default and role.is_default():
            raise BadArgument(
                f"The {role.mention} role is the default role and can't be managed"
            )

        if self.check_dangerous and self.dangerous(role):
            raise BadArgument(
                f"The {role.mention} role is dangerous and can't be managed"
            )

        if self.check_integrated and role.managed:
            raise BadArgument(
                f"The {role.mention} role is integrated and can't be managed"
            )

        elif role >= ctx.guild.me.top_role and ctx.guild.me.id != ctx.guild.owner_id:
            raise BadArgument("I can't manage a role higher than my top role")

        elif ctx.author == ctx.guild.owner:
            return role

        elif role > ctx.author.top_role:
            raise BadArgument("You can't manage a role higher than your top role")

        elif role == ctx.author.top_role:
            raise BadArgument("You can't manage your highest role")

        return role
