import asyncio
import re
from contextlib import suppress
from json import loads
from pathlib import Path
from typing import Annotated, Optional, cast

import discord
from discord import Embed, HTTPException, Member, Message, PartialEmoji, Role
from discord.ext.commands import (
    BucketType,
    Cog,
    Converter,
    Range,
    group,
    has_permissions,
    max_concurrency,
    parameter,
)
from rapidfuzz import process
from rapidfuzz.distance import DamerauLevenshtein

from bot.core import Context, Juno
from bot.shared import codeblock, dominant_color
from bot.shared.converters.attachment import PartialAttachment
from bot.shared.converters.role import StrictRole
from bot.shared.paginator import Paginator


def load_colors() -> dict[str, str]:
    file = Path(__file__).parent.joinpath("colors.json")
    if not file.exists():
        raise FileNotFoundError("colors.json not found")

    return loads(file.read_bytes())


HEX_PATTERN = r"^(?:[0-9a-fA-F]{3}){1,2}$"
COLORS = load_colors()


class Color(Converter[discord.Color]):
    async def convert(self, ctx: Context, argument: str) -> discord.Color:
        argument = argument.lower().replace("color", "").strip()
        if argument.startswith("#"):
            argument = argument[1:]

        if not argument:
            raise ValueError("You must provide a color")

        elif argument in ("black", "nigga", "nigger"):
            argument = "010101"

        if re.match(HEX_PATTERN, argument):
            return discord.Color(int(argument, 16))

        fuzzer = process.extract_iter(
            argument,
            choices=COLORS,
            scorer=DamerauLevenshtein.normalized_distance,
        )
        final = [pred for pred in fuzzer if pred[1] < 1.0]

        if not final:
            raise ValueError(f"Color `{argument}` wasn't found")

        best_match = min(final, key=lambda x: x[1])
        return discord.Color(int(best_match[2], 16))


def is_allowed(ctx: Context) -> bool:
    """Check if the invoker can assign a booster role."""

    if not ctx.command.qualified_name.startswith("boosterrole"):
        return True

    member = ctx.author
    if (
        member.premium_since
        or member.guild_permissions.administrator
        or member.id in ctx.bot.config.owner_ids
    ):
        return True

    return any(role in member.roles for role in ctx.settings.booster_role_include)


async def get_role(ctx: Context) -> Optional[Role]:
    """Get the booster role for a member."""

    query = "SELECT role_id FROM roles.booster WHERE guild_id = $1 AND user_id = $2"
    role_id = cast(
        Optional[int],
        await ctx.bot.db.fetchval(query, ctx.guild.id, ctx.author.id),
    )
    return ctx.guild.get_role(role_id or 0)


class BoosterRoles(Cog):
    def __init__(self, bot: Juno):
        self.bot = bot

    @Cog.listener("on_member_unboost")
    async def boosterrole_delete_unboost(self, member: Member) -> None:
        """Remove the member's booster role if they unboost."""

        query = "DELETE FROM roles.booster WHERE guild_id = $1 AND user_id = $2 RETURNING role_id"
        role_id = cast(
            Optional[int],
            await self.bot.db.fetchval(query, member.guild.id, member.id),
        )
        if not role_id:
            return

        role = member.guild.get_role(role_id)
        if not role:
            return

        with suppress(HTTPException):
            await role.delete(
                reason=f"Member no longer boosting. {member} ({member.id})"
            )

    @group(aliases=("color", "br"), invoke_without_command=True)
    async def boosterrole(
        self,
        ctx: Context,
        *,
        color: Annotated[discord.Color, Color],
    ) -> Message:
        """Assign a custom color to yourself."""

        if not ctx.settings.booster_role_base:
            return await ctx.warn(
                "This server doesn't have a base role set yet",
                f"Use the `{ctx.prefix}boosterrole base` command to set one",
            )

        if not is_allowed(ctx):
            return await ctx.warn("You must be boosting the server to use this")

        reason = f"Booster role assigned by {ctx.author} ({ctx.author.id})"
        role = await get_role(ctx)
        if role:
            await role.edit(color=color, reason=reason)
            if role not in ctx.author.roles:
                await ctx.author.add_roles(role, reason=reason)

        elif len(ctx.guild.roles) >= 200:
            return await ctx.warn("This server is approaching the maximum role limit")

        else:
            name = f"color:{ctx.author.display_name.lower()}"
            role = await ctx.guild.create_role(
                name=name,
                color=color,
                reason=reason,
            )

            query = """
            INSERT INTO roles.booster
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET role_id = EXCLUDED.role_id
            """
            await asyncio.gather(
                ctx.bot.db.execute(query, ctx.guild.id, ctx.author.id, role.id),
                ctx.guild.edit_role_positions(
                    positions={
                        role: ctx.settings.booster_role_base.position - 1,
                    },
                ),
                ctx.author.add_roles(role, reason=reason),
            )

        return await ctx.approve(f"Your color has been set to `{color}`")

    @boosterrole.command(name="remove", aliases=("delete", "del", "rm"))
    async def boosterrole_remove(self, ctx: Context) -> Message:
        """Remove your booster role."""

        role = await get_role(ctx)
        if not role:
            return await ctx.warn("You don't have a booster role")

        reason = f"Booster role removed by {ctx.author} ({ctx.author.id})"
        query = "DELETE FROM roles.booster WHERE guild_id = $1 AND user_id = $2"
        await asyncio.gather(
            ctx.bot.db.execute(query, ctx.guild.id, ctx.author.id),
            role.delete(reason=reason),
        )
        return await ctx.approve("Your booster role has been removed")

    @boosterrole.command(name="dominant", aliases=("avatar", "pfp", "av"))
    async def boosterrole_dominant(
        self,
        ctx: Context,
        *,
        member: Member = parameter(
            default=lambda ctx: ctx.author,
        ),
    ) -> Message:
        """Use the dominant color of your avatar."""

        async with ctx.typing():
            buffer = await member.display_avatar.read()
            color = await dominant_color(buffer)

        return await ctx.invoke(self.boosterrole, color=color)

    @boosterrole.command(name="rename", aliases=("name",))
    async def boosterrole_rename(
        self,
        ctx: Context,
        *,
        name: Range[str, 1, 100],
    ) -> Message:
        """Change the name of your booster role."""

        role = await get_role(ctx)
        if not role:
            return await ctx.warn("You don't have a booster role")

        reason = f"Booster role for {ctx.author} ({ctx.author.id})"
        await role.edit(name=name, reason=reason)
        return await ctx.approve(f"Changed your booster role's name to `{name}`")

    @boosterrole.group(
        name="icon",
        aliases=("image", "img"),
        invoke_without_command=True,
    )
    async def boosterrole_icon(
        self,
        ctx: Context,
        icon: PartialEmoji | PartialAttachment | str = parameter(
            default=PartialAttachment.fallback,
        ),
    ) -> Message:
        """Change the icon of your booster role."""

        if "ROLE_ICONS" not in ctx.guild.features:
            return await ctx.warn(
                "This server doesn't have enough boosts for role icons"
            )

        role = await get_role(ctx)
        if not role:
            return await ctx.warn(
                "You don't have a booster role",
                f"Use `{ctx.prefix}boosterrole` to create one",
            )

        if isinstance(icon, PartialEmoji) and icon.animated:
            return await ctx.warn("The provided emoji is animated and can't be used")
        elif isinstance(icon, PartialAttachment) and icon.format != "image":
            return await ctx.warn("The provided attachment isn't an image")

        reason = f"Booster role for {ctx.author} ({ctx.author.id})"
        buffer = await icon.read() if not isinstance(icon, str) else icon

        try:
            await role.edit(display_icon=buffer, reason=reason)
        except HTTPException as exc:
            return await ctx.warn(
                "The provided icon can't be used",
                codeblock(exc.text),
            )

        return await ctx.approve("Changed your booster role's icon")

    @boosterrole_icon.command(name="remove", aliases=("delete", "del", "rm"))
    async def boosterrole_icon_remove(self, ctx: Context) -> Message:
        """Remove the icon of your booster role."""

        role = await get_role(ctx)
        if not role:
            return await ctx.warn("You don't have a booster role")
        elif not role.display_icon:
            return await ctx.warn("Your booster role doesn't have an icon")

        reason = f"Booster role for {ctx.author} ({ctx.author.id})"
        await role.edit(display_icon=None, reason=reason)
        return await ctx.approve("Removed your booster role's icon")

    @boosterrole.command(name="base", aliases=("set",))
    @has_permissions(manage_roles=True)
    async def boosterrole_base(
        self,
        ctx: Context,
        *,
        role: Annotated[
            Role,
            StrictRole(check_integrated=False),
        ],
    ) -> Message:
        """Set the base position for booster roles."""

        await ctx.settings.upsert(booster_role_base_id=role.id)
        return await ctx.approve(f"Now placing color roles below {role.mention}")

    @boosterrole.group(name="include", aliases=("allow",), invoke_without_command=True)
    @has_permissions(manage_roles=True)
    async def boosterrole_include(
        self,
        ctx: Context,
        *,
        role: Annotated[
            Role,
            StrictRole(check_integrated=False),
        ],
    ) -> Message:
        """Allow a role to create booster roles."""

        if role in ctx.settings.booster_role_include:
            return await ctx.warn(
                f"Already allowing {role.mention} to create booster roles"
            )

        ctx.settings.record["booster_role_include"].append(role.id)
        await ctx.settings.upsert()
        return await ctx.approve(f"Now allowing {role.mention} to create booster roles")

    @boosterrole_include.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_roles=True)
    async def boosterrole_include_remove(
        self,
        ctx: Context,
        *,
        role: Annotated[
            Role,
            StrictRole(check_integrated=False),
        ],
    ) -> Message:
        """Disallow a role from creating booster roles."""

        if role not in ctx.settings.booster_role_include:
            return await ctx.warn(
                f"{role.mention} isn't allowed to create booster roles"
            )

        ctx.settings.record["booster_role_include"].remove(role.id)
        await ctx.settings.upsert()
        return await ctx.approve(
            f"No longer allowing {role.mention} to create booster roles"
        )

    @boosterrole_include.command(name="list")
    async def boosterrole_include_list(self, ctx: Context) -> Message:
        """View the roles that can create booster roles."""

        if not ctx.settings.booster_role_include:
            return await ctx.warn("No roles are allowed to create booster roles")

        roles = [
            f"{role.mention} [`{role.id}`]"
            for role in ctx.settings.booster_role_include
        ]
        embed = Embed(title="Allowed Roles")

        paginator = Paginator(ctx, roles, embed)
        return await paginator.start()

    @boosterrole_include.command(
        name="clear",
        aliases=(
            "reset",
            "clean",
        ),
    )
    @has_permissions(manage_roles=True)
    async def boosterrole_include_clear(self, ctx: Context) -> Message:
        """Disallow all roles from creating booster roles."""

        if not ctx.settings.booster_role_include:
            return await ctx.warn("No roles are allowed to create booster roles")

        ctx.settings.record["booster_role_include"].clear()
        await ctx.settings.upsert()
        return await ctx.approve("Removed all roles that were included")

    @boosterrole.command(name="list")
    @has_permissions(manage_roles=True)
    async def boosterrole_list(self, ctx: Context) -> Message:
        """View all booster roles."""

        query = "SELECT user_id, role_id FROM roles.booster WHERE guild_id = $1"
        roles = [
            f"**{member}** - {role.mention}"
            for record in await ctx.bot.db.fetch(query, ctx.guild.id)
            if (member := ctx.guild.get_member(record["user_id"]))
            and (role := ctx.guild.get_role(record["role_id"]))
        ]
        if not roles:
            return await ctx.warn("No booster roles have been created")

        embed = Embed(title="Booster Roles")
        paginator = Paginator(ctx, roles, embed)
        return await paginator.start()

    @boosterrole.command(
        name="clear",
        aliases=(
            "reset",
            "clean",
        ),
    )
    @has_permissions(administrator=True)
    @max_concurrency(1, BucketType.guild)
    async def boosterrole_clear(self, ctx: Context) -> Message:
        """Remove all booster roles."""

        await ctx.prompt(
            "Are you sure you want to remove all booster roles?",
            "This will delete all booster roles that have been created",
        )

        query = "DELETE FROM roles.booster WHERE guild_id = $1 RETURNING role_id"
        roles = [
            role
            for record in await ctx.bot.db.fetch(query, ctx.guild.id)
            if (role := ctx.guild.get_role(record["role_id"]))
        ]
        if not roles:
            return await ctx.warn("No booster roles have been created")

        async with ctx.typing():
            for role in roles:
                with suppress(HTTPException):
                    await role.delete(reason=f"Booster roles cleared by {ctx.author}")

        return await ctx.approve("All booster roles have been removed")
