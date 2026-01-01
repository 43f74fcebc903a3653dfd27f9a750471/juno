from contextlib import suppress
from datetime import timedelta
from typing import Annotated, List, Literal, Optional, TypedDict, cast

from discord import Embed, HTTPException, Member, Message, User
from discord.ext.commands import Cog, Greedy, Range, flag, group, has_permissions

from bot.core import Context, Juno
from bot.shared import Paginator
from bot.shared.converters import FlagConverter, Status
from bot.shared.formatter import human_join, plural


class Record(TypedDict):
    guild_id: int
    status: bool
    action: Literal["kick", "ban"]
    limit: int


class WhitelistFlags(FlagConverter):
    action: Literal["kick", "ban"] = flag(
        aliases=["punishment"],
        description="The punishment the member will receive.",
        default="kick",
    )
    limit: Range[int, 2, 5] = flag(
        aliases=["attempts", "rejoins"],
        description="The maximum attempts a member can rejoin the server before being banned.",
        default=3,
    )


class Whitelist(Cog):
    def __init__(self, bot: Juno):
        self.bot = bot

    @Cog.listener("on_member_join")
    async def whitelist_listener(self, member: Member) -> None:
        guild = member.guild

        permitted = await self.bot.redis.sismember(
            f"whitelist:{guild.id}",
            str(member.id),
        )
        if permitted:
            return

        query = "SELECT * FROM whitelist WHERE guild_id = $1"
        record = cast(Optional[Record], await self.bot.db.fetchrow(query, guild.id))
        if not record or not record["status"]:
            return

        if record["limit"] > 1:
            reached_limit = await self.bot.redis.ratelimited(
                f"whitelist.attempts:{guild.id}:{member.id}",
                limit=record["limit"] - 1,
                timespan=60,
            )
            if reached_limit:
                with suppress(HTTPException):
                    await member.ban(
                        reason=f"Member banned for exceeding the rejoin limit of {record['limit']} attempts"
                    )

                return

        with suppress(HTTPException):
            if record["action"] == "kick":
                await member.kick(
                    reason="Member hasn't been permitted to join the server (whitelist)"
                )
            else:
                await member.ban(
                    reason="Member hasn't been permitted to join the server (whitelist)"
                )

    @Cog.listener("on_member_remove")
    async def whitelist_remove(self, member: Member) -> None:
        if member.bot:
            return

        await self.bot.redis.srem(
            f"whitelist:{member.guild.id}",
            str(member.id),
        )

    @group(aliases=("wl", "access"), invoke_without_command=True)
    @has_permissions(administrator=True)
    async def whitelist(self, ctx: Context) -> None:
        """Restrict access to your server."""

        return await ctx.send_help(ctx.command)

    @whitelist.command(name="settings", aliases=("configuration", "config"))
    @has_permissions(administrator=True)
    async def whitelist_settings(self, ctx: Context) -> Message:
        """View the whitelist settings for the server."""

        query = "SELECT * FROM whitelist WHERE guild_id = $1"
        record = cast(Optional[Record], await self.bot.db.fetchrow(query, ctx.guild.id))
        if not record:
            return await ctx.warn("The whitelist restrictions haven't been set up yet")

        embed = Embed(title="Whitelist Settings")
        embed.description = f"The server is currently `{'public' if not record['status'] else 'whitelist-only'}`"
        if record["status"]:
            embed.description += f"\n> `{ctx.clean_prefix}whitelist permit <user>`"
            punishment = "banned" if record["action"] == "ban" else "kicked"
            action = f"New members will be `{punishment}` when they join the server"

        else:
            action = "Members are able to join the server without any restrictions"

        embed.add_field(name="Action", value=action)
        if record["status"] and record["limit"] and record["action"] != "kick":
            embed.set_footer(
                text=f"After {record['limit']} rejoin attempts, the member will be banned from the server"
            )

        return await ctx.send(embed=embed)

    @whitelist.command(name="toggle", aliases=("switch",))
    @has_permissions(administrator=True)
    async def whitelist_toggle(
        self,
        ctx: Context,
        status: Annotated[bool, Status],
        *,
        flags: WhitelistFlags,
    ) -> Message:
        """Toggle the server's whitelist restriction status."""

        query = """
        INSERT INTO whitelist (guild_id, status, action, "limit")
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (guild_id)
        DO UPDATE SET
            status = EXCLUDED.status,
            action = EXCLUDED.action,
            "limit" = EXCLUDED."limit"
        RETURNING *
        """
        record = cast(
            Record,
            await self.bot.db.fetchrow(
                query,
                ctx.guild.id,
                status,
                flags.action,
                flags.limit,
            ),
        )

        if not record["status"]:
            return await ctx.approve(
                "The server is now `public` and no longer requires members to be whitelisted"
            )

        punishment = "banned" if record["action"] == "ban" else "kicked"
        return await ctx.approve(
            f"New members will now be `{punishment}` whenever they join the server",
            (
                f"After {plural(record['limit'], '`'):attempt} to rejoin, the member will be permanently banned"
                if record["action"] == "kick" and record["limit"] > 1
                else ""
            ),
        )

    @whitelist.group(
        name="permit",
        aliases=("add", "allow", "grant"),
        invoke_without_command=True,
    )
    @has_permissions(administrator=True)
    async def whitelist_permit(
        self,
        ctx: Context,
        users: Greedy[Member | User],
    ) -> Message:
        """Allow a user to join the server."""

        permitted = await self.bot.redis.smembers(f"whitelist:{ctx.guild.id}")
        for user in list(users):
            if isinstance(user, Member) or user.bot:
                if len(users) == 1:
                    return await ctx.warn("That user is already in the server")

                users.remove(user)

            elif str(user.id) in permitted:
                if len(users) == 1:
                    return await ctx.warn(
                        "That user already has an active whitelist permit"
                    )

                users.remove(user)

        await self.bot.redis.sadd(
            f"whitelist:{ctx.guild.id}",
            *[str(user.id) for user in users],
            ex=timedelta(hours=12),
        )
        human_users = human_join([f"`{user}`" for user in users], final="and")
        return await ctx.approve(f"Granted {human_users} access to join the server")

    @whitelist_permit.command(name="list", aliases=("view", "permits"), hidden=True)
    @has_permissions(administrator=True)
    async def whitelist_permit_list(self, ctx: Context) -> Message:
        """View the users with an active whitelist permit."""

        return await self.whitelist_list(ctx)

    @whitelist.command(name="revoke", aliases=("remove", "deny", "kick"))
    @has_permissions(administrator=True)
    async def whitelist_revoke(
        self,
        ctx: Context,
        users: Greedy[Member | User],
    ) -> Message:
        """Revoke a user's whitelist permit."""

        permitted = await self.bot.redis.smembers(f"whitelist:{ctx.guild.id}")
        for user in list(users):
            if isinstance(user, User) and str(user.id) not in permitted:
                if len(users) == 1:
                    return await ctx.warn(
                        "That user doesn't have an active whitelist permit"
                    )

                users.remove(user)

        members = [user for user in users if isinstance(user, Member)]
        if members:
            human_members = human_join(
                [member.mention for member in members],
                final="and",
            )
            try:
                await ctx.prompt(
                    f"Would you also like to kick {human_members} from the server?"
                )
            except Exception:
                ...
            else:
                reason = f"Whitelist revoked by {ctx.author} ({ctx.author.id})"
                for member in members:
                    with suppress(HTTPException):
                        await member.kick(reason=reason)

        await self.bot.redis.srem(
            f"whitelist:{ctx.guild.id}",
            *[str(user.id) for user in users],
        )

        human_users = human_join([f"`{user}`" for user in users], final="and")
        return await ctx.approve(f"Revoked {human_users} from the whitelist")

    @whitelist.command(name="list", aliases=("view", "permits"))
    @has_permissions(administrator=True)
    async def whitelist_list(self, ctx: Context) -> Message:
        """View the users with an active whitelist permit."""

        permitted = await self.bot.redis.smembers(f"whitelist:{ctx.guild.id}")
        if not permitted:
            return await ctx.warn("No users have an active whitelist permit")

        users: List[str] = []
        for user_id in permitted:
            user: Optional[User] = None
            if len(permitted) < 5:
                with suppress(HTTPException):
                    user = await self.bot.get_or_fetch_user(int(user_id))
            else:
                user = self.bot.get_user(int(user_id))

            users.append(f"{user or 'Unknown User'} [`{user_id}`]")

        embed = Embed(title="Whitelist Permits")
        paginator = Paginator(ctx, users, embed)
        return await paginator.start()
