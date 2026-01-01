from contextlib import suppress
from typing import Annotated, List, Optional, TypedDict, cast

from discord import Embed, HTTPException, Message, Role
from discord.ext.commands import Cog, Range, flag, group, has_permissions

from bot.core import Context, Juno
from bot.shared import codeblock
from bot.shared.converters import FlagConverter, Status
from bot.shared.converters.role import StrictRole
from bot.shared.formatter import vowel
from bot.shared.paginator import Paginator
from bot.shared.script import Script


class Flags(FlagConverter):
    strict: Annotated[bool, Status] = flag(
        description="Only respond to messages that match the trigger exactly.",
        default=False,
    )
    reply: Annotated[bool, Status] = flag(
        description="Reply to the message that triggered the response.",
        default=False,
    )
    delete: Annotated[bool, Status] = flag(
        description="Delete the message that triggered the response.",
        default=False,
    )
    paginate: Annotated[bool, Status] = flag(
        aliases=["pagination", "pages"],
        description="Paginate the response with buttons.",
        default=False,
    )
    delete_after: Range[int, 3, 120] = flag(
        aliases=["self_destruct"],
        description="Delete the response after a certain amount of time.",
        default=0,
    )
    role: Annotated[
        Role,
        StrictRole(
            check_dangerous=True,
        ),
    ] = flag(
        aliases=["grant", "remove"],
        description="Grant or remove a role from the author of the message.",
        default=None,
    )


class Record(TypedDict):
    guild_id: int
    trigger: str
    template: str
    strict: bool
    reply: bool
    delete: bool
    paginate: bool
    delete_after: int
    role_id: int


class ResponseTrigger(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @Cog.listener("on_message_without_command")
    async def response_trigger(self, ctx: Context) -> None:
        """Respond to messages that match a trigger."""

        if not ctx.message.content:
            return

        query = """
        SELECT * FROM triggers.response
        WHERE guild_id = $1
        AND LOWER($2) LIKE '%' || LOWER(trigger) || '%'
        """
        record = cast(
            Optional[Record],
            await self.bot.db.fetchrow(
                query,
                ctx.guild.id,
                ctx.message.content,
            ),
        )
        if not record:
            return

        elif (
            record["strict"]
            and record["trigger"].lower() != ctx.message.content.lower()
        ):
            return

        key = f"response:{ctx.guild.id}:{ctx.author.id}"
        if await self.bot.redis.ratelimited(key, 1, 4):
            return

        script = Script(record["template"], [ctx.guild, ctx.channel, ctx.author])
        with suppress(HTTPException):
            reference = ctx.message if record["reply"] else None
            if record["paginate"] and len(script.embeds) > 1:
                paginator = Paginator(ctx, script.embeds)
                message = await paginator.start(content=script.content, reference=reference)
            else:
                message = await script.send(ctx, reference=reference)

            if record["delete"] and not message.reference:
                await ctx.message.delete()

            if record["delete_after"]:
                await message.delete(delay=record["delete_after"])

            if (
                role := ctx.guild.get_role(record["role_id"])
            ) and not role >= ctx.guild.me.top_role:
                reason = f"Response trigger {record['trigger']}"
                if role not in ctx.author.roles:
                    await ctx.author.add_roles(role, reason=reason)
                else:
                    await ctx.author.remove_roles(role, reason=reason)

    @group(
        aliases=("autoresponse", "autoresponder", "ar"),
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True)
    async def response(self, ctx: Context) -> Message:
        """Automatically respond to messages that match a trigger."""

        return await ctx.send_help(ctx.command)

    @response.command(
        name="add",
        extras={"flags": Flags},
        aliases=("create", "new"),
    )
    @has_permissions(manage_messages=True)
    async def response_add(
        self,
        ctx: Context,
        trigger: str,
        *,
        script: Script,
    ) -> Message:
        """Create a new response trigger.

        If the trigger contains spaces, it must be wrapped in quotes.
        For example: "trigger with spaces" will be treated as a single trigger.
        """

        if not trigger:
            return await ctx.send_help(ctx.command)

        template, flags = await Flags().find(ctx, script.template)
        if not template:
            return await ctx.warn("Please provide a response script")

        if flags.paginate and len(script.embeds) < 2:
            return await ctx.warn(
                "You can only paginate responses with multiple embeds",
                "Include multiple embeds via `{embed}` to add another embed",
            )

        query = """
        INSERT INTO triggers.response (
            guild_id,
            trigger,
            template,
            strict,
            reply,
            delete,
            paginate,
            delete_after,
            role_id
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (guild_id, trigger)
        DO UPDATE SET
            template = EXCLUDED.template,
            strict = EXCLUDED.strict,
            reply = EXCLUDED.reply,
            delete = EXCLUDED.delete,
            paginate = EXCLUDED.paginate,
            delete_after = EXCLUDED.delete_after,
            role_id = EXCLUDED.role_id
        """
        await self.bot.db.execute(
            query,
            ctx.guild.id,
            trigger,
            template,
            flags.strict,
            flags.reply,
            flags.delete,
            flags.paginate,
            flags.delete_after,
            flags.role.id if flags.role else None,
        )

        return await ctx.approve(
            f"Now responding with {vowel(script.format)} message for `{trigger}`"
            + (
                " "
                + " ".join(
                    f"(`{flag.name}`)"
                    for flag in flags.values
                    if getattr(flags, flag.attribute)
                )
                if any(getattr(flags, flag.attribute) for flag in flags.values)
                else ""
            )
        )

    @response.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_messages=True)
    async def response_remove(self, ctx: Context, *, trigger: str) -> Message:
        """Remove a response trigger."""

        query = """
        DELETE FROM triggers.response
        WHERE guild_id = $1
        AND LOWER(trigger) = LOWER($2)
        """
        result = await self.bot.db.execute(query, ctx.guild.id, trigger)
        if result == "DELETE 0":
            return await ctx.warn(f"No response trigger found for `{trigger}`")

        return await ctx.approve(f"Removed the response trigger for `{trigger}`")

    @response.command(name="view", aliases=("show",))
    @has_permissions(manage_messages=True)
    async def response_view(self, ctx: Context, *, trigger: str) -> Message:
        """View a response trigger."""

        query = """
        SELECT * FROM triggers.response
        WHERE guild_id = $1
        AND LOWER(trigger) = LOWER($2)
        """
        record = cast(
            Optional[Record],
            await self.bot.db.fetchrow(query, ctx.guild.id, trigger),
        )
        if not record:
            return await ctx.warn(f"No response trigger found for `{trigger}`")

        script = Script(record["template"], [ctx.guild, ctx.author, ctx.channel])
        embed = Embed(
            title=f"Response Trigger / {record['trigger']}",
            description=codeblock(script.template),
        )
        embed.add_field(
            name="Properties",
            value=">>> "
            + ", ".join(
                f"`{name}`"
                for name, value in record.items()
                if name not in ("guild_id", "trigger", "template") and value
            ),
        )

        await ctx.reply(embed=embed)
        return await script.send(ctx.channel)

    @response.command(name="clear", aliases=("reset", "purge"))
    @has_permissions(manage_roles=True)
    async def response_clear(self, ctx: Context) -> Optional[Message]:
        """Remove all response triggers."""

        query = "DELETE FROM triggers.response WHERE guild_id = $1"
        result = await self.bot.db.execute(query, ctx.guild.id)
        if result == "DELETE 0":
            return await ctx.warn("No response triggers have been set up")

        return await ctx.add_check()

    @response.command(name="list")
    @has_permissions(manage_messages=True)
    async def response_list(self, ctx: Context) -> Message:
        """View all response triggers in the server."""

        query = "SELECT * FROM triggers.response WHERE guild_id = $1"
        records = cast(List[Record], await self.bot.db.fetch(query, ctx.guild.id))
        flags = ("strict", "reply", "delete", "paginate", "delete_after")
        triggers = [
            f"{record['trigger']!r}"
            + (
                " (" + ", ".join(f"`{flag}`" for flag in flags if record[flag]) + ")"
                if any(record[flag] for flag in flags)
                else ""
            )
            for record in records
        ]
        if not triggers:
            return await ctx.warn("No response triggers have been set up")

        embed = Embed(title="Response Triggers")
        paginator = Paginator(ctx, triggers, embed)
        return await paginator.start()
