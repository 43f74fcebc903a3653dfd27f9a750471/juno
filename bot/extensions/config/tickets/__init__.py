from contextlib import suppress
from datetime import timedelta
from logging import getLogger
from typing import Annotated, List, Literal, Mapping, Optional, TypedDict, cast

from discord import (
    AllowedMentions,
    ButtonStyle,
    CategoryChannel,
    Color,
    Embed,
    HTTPException,
    Interaction,
    Member,
    Message,
    Object,
    PermissionOverwrite,
    Role,
    SelectOption,
    TextChannel,
    Thread,
)
from discord.ext.commands import Cog, Range, flag, group, has_permissions, parameter
from discord.ext.tasks import loop
from discord.ui import Button, Select
from discord.utils import snowflake_time, utcnow
from humanfriendly import format_timespan
from aiohttp.web import Request, Response, json_response

from bot.core import Context, Juno
from bot.shared import codeblock
from bot.shared.converters import FlagConverter, Identifier
from bot.shared.converters.role import StrictRole
from bot.shared.converters.time import Duration
from bot.shared.converters.user import HierarchyMember
from bot.shared.formatter import human_join
from bot.shared.paginator import Paginator
from bot.shared.script import Script, parse

from .checks import TicketContext, in_ticket, staff
from .interface import Interface
from .settings import Settings, Ticket, TicketButton, TicketDropdownOption
from .transcript import Transcript

logger = getLogger("bot.tickets")


async def ephemeral_response(
    interaction: Interaction,
    content: str,
    followup: bool = False,
) -> None:
    """Send an ephemeral response to an interaction."""

    embed = Embed(color=Color.dark_embed(), description=content)
    if followup:
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ButtonFlags(FlagConverter):
    style: Literal["blurple", "grey", "gray", "green", "red"] = flag(
        default="green",
        aliases=["color"],
        description="The color of the button.",
    )
    emoji: Optional[str] = flag(
        aliases=["emote"],
        description="The emoji to display on the button.",
    )


class DropdownFlags(FlagConverter):
    emoji: Optional[str] = flag(
        aliases=["emote"],
        description="The emoji to display on the option.",
    )
    description: Optional[str] = flag(
        aliases=["desc"],
        description="The description of the option.",
    )


class InactivityRecord(TypedDict):
    channel_id: int
    inactivity_timeout: int


class Tickets(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        try:
            self.bot.backend.router.add_get(
                "/transcripts/{identifier}",
                self.ticket_transcripts_route,
            )
        except RuntimeError:
            ...

        self.check_ticket_inactivity.start()
        return await super().cog_load()

    async def cog_unload(self) -> None:
        self.check_ticket_inactivity.cancel()
        return await super().cog_unload()

    @loop(minutes=5)
    async def check_ticket_inactivity(self) -> None:
        query = """
        SELECT 
            tickets.open.channel_id AS channel_id,
            tickets.settings.inactivity_timeout AS inactivity_timeout
        FROM tickets.open
        JOIN tickets.settings
            ON tickets.open.guild_id = tickets.settings.guild_id
        WHERE tickets.settings.inactivity_timeout IS NOT NULL
        """
        records = cast(List[InactivityRecord], await self.bot.db.fetch(query))

        scheduled_deletion: List[int] = []
        for record in records:
            if not record["inactivity_timeout"]:
                continue

            channel = cast(
                Optional[TextChannel],
                self.bot.get_channel(record["channel_id"]),
            )
            if not channel:
                scheduled_deletion.append(record["channel_id"])
                continue

            if not channel.last_message_id:
                continue

            if (
                utcnow() - snowflake_time(channel.last_message_id)
            ).total_seconds() >= record["inactivity_timeout"]:
                with suppress(HTTPException):
                    await channel.delete(reason="Ticket has been inactive for too long")

                logger.info(
                    f"Deleted inactive ticket {channel} in {channel.guild} ({channel.guild.id})"
                )

        if scheduled_deletion:
            query = "DELETE FROM tickets.open WHERE channel_id = ANY($1::BIGINT[])"
            await self.bot.db.execute(query, scheduled_deletion)

    @Cog.listener("on_interaction")
    async def ticket_create(self, interaction: Interaction):
        """Listen for ticket creation interactions."""

        if not interaction.data or not interaction.guild:
            return

        elif not isinstance(interaction.user, Member):
            return

        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("ticket.create"):
            return

        elif not interaction.guild.me.guild_permissions.manage_channels:
            return await ephemeral_response(
                interaction,
                "I am missing the `Manage Channels` permission",
            )

        component_type = interaction.data.get("component_type", 0)
        identifier = custom_id.split(":", 2)[-1]
        settings = await Settings.fetch(self.bot, interaction.guild)
        if not settings:
            return await ephemeral_response(
                interaction,
                "Ticket settings were not found",
            )

        elif settings.is_blacklisted(interaction.user):
            return await ephemeral_response(
                interaction,
                "You are not allowed to create tickets",
            )

        record: Optional[TicketButton | TicketDropdownOption] = None
        if component_type == 2:
            record = await settings.fetch_button(identifier)
        elif component_type == 3:
            record = await settings.fetch_dropdown(identifier, interaction.data)

        if not record:
            return await ephemeral_response(
                interaction,
                "This button is no longer configured",
            )

        query = """
        SELECT *
        FROM tickets.open
        WHERE id = $1
        AND guild_id = $2
        AND user_id = $3
        """
        ticket = cast(
            Optional[Ticket],
            await self.bot.db.fetchrow(
                query,
                identifier,
                interaction.guild.id,
                interaction.user.id,
            ),
        )
        if ticket:
            channel = interaction.guild.get_channel(ticket["channel_id"])
            if channel:
                return await ephemeral_response(
                    interaction,
                    f"You already have an open ticket in {channel.mention}",
                )

        query = "SELECT COUNT(*) FROM tickets.open WHERE guild_id = $1"
        tickets = cast(int, await self.bot.db.fetchval(query, interaction.guild.id))
        if settings.record["max_tickets"] and tickets >= settings.record["max_tickets"]:
            return await ephemeral_response(
                interaction,
                f"The maximum number of tickets has been reached ({settings.record['max_tickets']})",
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        category = cast(
            Optional[CategoryChannel],
            interaction.guild.get_channel(record["category_id"] or 0),
        )
        overwrites: Mapping[Role | Member | Object, PermissionOverwrite] = {
            interaction.guild.default_role: PermissionOverwrite(
                view_channel=False,
                read_message_history=True,
                send_messages=True,
                attach_files=True,
                embed_links=True,
                mention_everyone=False,
            ),
        }
        for entity in {interaction.user, *settings.staff_roles}:
            overwrites[entity] = PermissionOverwrite(view_channel=True)

        try:
            channel = await interaction.guild.create_text_channel(
                name=f"ticket-{interaction.user.name}"[:100],
                category=category,
                overwrites=overwrites,
                topic=parse(
                    record["topic"] or "", [interaction.guild, interaction.user]
                ),
                reason=f"Ticket created by {interaction.user} ({interaction.user.id})",
            )
        except HTTPException as exc:
            return await ephemeral_response(
                interaction,
                f"Failed to create the ticket\n{codeblock(exc.text)}",
                followup=True,
            )
        else:
            logger.info(
                f"Created ticket {channel} for {interaction.user} in {interaction.guild} ({interaction.guild.id})"
            )

        query = """
        INSERT INTO tickets.open (
            id,
            guild_id,
            channel_id,
            user_id
        ) VALUES ($1, $2, $3, $4)
        ON CONFLICT (id, guild_id, user_id)
        DO UPDATE SET channel_id = EXCLUDED.channel_id
        """
        await self.bot.db.execute(
            query,
            identifier,
            interaction.guild.id,
            channel.id,
            interaction.user.id,
        )
        await ephemeral_response(
            interaction,
            f"Created a new ticket in {channel.mention}",
            followup=True,
        )

        if record["template"]:
            script = Script(
                record["template"],
                [
                    interaction.guild,
                    interaction.user,
                    channel,
                ],
            )
            with suppress(HTTPException):
                await script.send(channel, allowed_mentions=AllowedMentions.all())
        else:
            embed = Embed(
                title="Ticket Created",
                description="Staff will be with you shortly",
            )
            with suppress(HTTPException):
                await channel.send(content=interaction.user.mention, embed=embed)

    @Cog.listener("on_guild_channel_delete")
    async def ticket_channel_delete(self, channel: TextChannel):
        if not isinstance(channel, TextChannel):
            return

        query = "DELETE FROM tickets.open WHERE channel_id = $1"
        await self.bot.db.execute(query, channel.id)

    @group(aliases=("tickets", "tck"), invoke_without_command=True)
    @has_permissions(manage_channels=True)
    async def ticket(self, ctx: Context) -> Message:
        """Ticket management commands."""

        return await ctx.send_help(ctx.command)

    @ticket.group(
        name="panel",
        aliases=("message", "link"),
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def ticket_panel(self, ctx: Context, message: Message) -> Message:
        """Set the ticket panel message."""

        if message.guild != ctx.guild:
            return await ctx.warn("The message must be from this server")

        elif message.author != ctx.guild.me:
            return await ctx.warn("The message must be sent by me")

        settings = await Settings.fetch(self.bot, ctx.guild)
        await settings.upsert(channel_id=message.channel.id, message_id=message.id)
        return await ctx.approve(
            f"Successfully set that [`message`]({message.jump_url}) as a ticket panel",
            f"Use `{ctx.clean_prefix}ticket button/dropdown` to configure the interface",
        )

    @ticket.group(name="inactivity", aliases=("inactive", "timeout"))
    @has_permissions(manage_channels=True)
    async def ticket_inactivity(
        self,
        ctx: Context,
        *,
        timeout: timedelta = parameter(
            converter=Duration(
                min=timedelta(minutes=30),
            ),
        ),
    ) -> Message:
        """Automatically close tickets after a period of inactivity."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        await settings.upsert(inactivity_timeout=int(timeout.total_seconds()))
        return await ctx.approve(
            f"Tickets will now be closed after {format_timespan(timeout)} of inactivity"
        )

    @ticket_inactivity.command(name="disable", aliases=("remove", "off"))
    @has_permissions(manage_channels=True)
    async def ticket_inactivity_disable(self, ctx: Context) -> Message:
        """Disable the ticket inactivity timeout."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        await settings.upsert(inactivity_timeout=None)
        return await ctx.approve("No longer closing tickets due to inactivity")

    @ticket.group(name="limit", aliases=("max", "maximum"))
    @has_permissions(manage_channels=True)
    async def ticket_limit(self, ctx: Context, limit: Range[int, 0, 200]) -> Message:
        """Set the maximum amount of open tickets."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        await settings.upsert(max_tickets=limit)
        if not limit:
            return await ctx.approve("No longer limiting the number of open tickets")

        return await ctx.approve(f"Set the maximum number of tickets to `{limit}`")

    @ticket_limit.command(name="disable", aliases=("remove", "off"))
    @has_permissions(manage_channels=True)
    async def ticket_limit_disable(self, ctx: Context) -> Message:
        """Remove the ticket limit."""

        return await self.ticket_limit(ctx, 0)

    @ticket.group(name="button", aliases=("btn",), invoke_without_command=True)
    @has_permissions(manage_channels=True)
    async def ticket_button(self, ctx: Context) -> Message:
        """Configure the buttons on the ticket panel."""

        return await ctx.send_help(ctx.command)

    @ticket_button.command(name="add", extras={"flags": ButtonFlags})
    @has_permissions(manage_channels=True)
    async def ticket_button_add(self, ctx: Context, *, label: str) -> Message:
        """Add a button to the ticket panel."""

        label, flags = await ButtonFlags().find(ctx, label)
        if not label:
            return await ctx.send_help(ctx.command)

        settings = await Settings.fetch(self.bot, ctx.guild)
        message = await settings.fetch_message()
        if not message:
            return await ctx.warn(
                "The ticket panel has not been set yet",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        interface = Interface.from_message(message)
        identifier = Identifier.create()
        interface.add_item(
            Button(
                label=label,
                emoji=flags.emoji,
                style=getattr(ButtonStyle, flags.style, ButtonStyle.green),
                custom_id=f"ticket.create:button:{identifier.id}",
            ),
        )

        try:
            await message.edit(view=interface)
        except HTTPException as exc:
            return await ctx.warn(
                "Failed to update the ticket panel",
                codeblock(exc.text),
            )

        query = "INSERT INTO tickets.button (id, guild_id) VALUES ($1, $2)"
        await ctx.bot.db.execute(query, identifier.id, ctx.guild.id)

        return await ctx.approve(
            f"Added the button **{f'{flags.emoji} ' if flags.emoji else ''}{label}** with identifier {identifier}",
            "This identifier above is important & used to configure the button",
        )

    @ticket_button.command(
        name="edit",
        aliases=("update",),
        extras={"flags": ButtonFlags},
    )
    @has_permissions(manage_channels=True)
    async def ticket_button_edit(
        self,
        ctx: Context,
        identifier: Identifier,
        *,
        label: str,
    ) -> Message:
        """Update an existing button on the ticket panel."""

        label, flags = await ButtonFlags().find(ctx, label)
        if not label:
            return await ctx.send_help(ctx.command)

        settings = await Settings.fetch(self.bot, ctx.guild)
        message = await settings.fetch_message()
        if not message:
            return await ctx.warn(
                "The ticket panel has not been set yet",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        interface = Interface.from_message(message)
        for button in list(interface.children):
            if not isinstance(button, Button):
                continue

            if (button.custom_id or "").endswith(identifier.id):
                button.label = label
                button.emoji = flags.emoji
                button.style = getattr(ButtonStyle, flags.style, ButtonStyle.green)
                break
        else:
            return await ctx.warn(
                "The button with that identifier was not found",
                "Use `ticket button list` to find the correct identifier",
            )

        try:
            await message.edit(view=interface)
        except HTTPException as exc:
            return await ctx.warn(
                "Failed to update the ticket panel",
                codeblock(exc.text),
            )

        return await ctx.approve(f"Updated the button with identifier {identifier}")

    @ticket_button.group(
        name="category",
        aliases=("redirect",),
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def ticket_button_category(
        self,
        ctx: Context,
        identifier: Identifier,
        *,
        channel: CategoryChannel,
    ) -> Message:
        """Set the category for a ticket button."""

        query = """
        UPDATE tickets.button
        SET category_id = $3
        WHERE guild_id = $1
        AND id = $2
        """
        result = await ctx.bot.db.execute(
            query,
            ctx.guild.id,
            identifier.id,
            channel.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(
                "The button with that identifier was not found",
                "Use `ticket button list` to find the correct identifier",
            )

        return await ctx.approve(
            f"Now redirecting tickets from the button {identifier} to [`{channel.name}`]({channel.jump_url})"
        )

    @ticket_button_category.command(name="remove", aliases=("delete", "rm"))
    @has_permissions(manage_channels=True)
    async def ticket_button_category_remove(
        self,
        ctx: Context,
        identifier: Identifier,
    ) -> Message:
        """Remove the category from a ticket button."""

        query = """
        UPDATE tickets.button
        SET category_id = NULL
        WHERE guild_id = $1
        AND id = $2
        """
        result = await ctx.bot.db.execute(query, ctx.guild.id, identifier.id)
        if result == "UPDATE 0":
            return await ctx.warn(
                "The button with that identifier was not found",
                "Use `ticket button list` to find the correct identifier",
            )

        return await ctx.approve(f"Removed the category from the button {identifier}")

    @ticket_button.command(name="remove", aliases=("delete", "rm"))
    @has_permissions(manage_channels=True)
    async def ticket_button_remove(
        self,
        ctx: Context,
        identifier: Identifier,
    ) -> Message:
        """Remove a button from the ticket panel.

        The identifier can be found via `ticket button list`."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        message = await settings.fetch_message()
        if not message:
            return await ctx.warn(
                "The ticket panel has not been set yet",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        query = "DELETE FROM tickets.button WHERE identifier = $1"
        result = await ctx.bot.db.execute(query, identifier.id)
        if result == "DELETE 0":
            return await ctx.warn(
                "The button with that identifier was not found",
                "Use `ticket button list` to find the correct identifier",
            )

        interface = Interface.from_message(message)
        for button in list(interface.children):
            if not isinstance(button, Button):
                continue

            if (button.custom_id or "").endswith(identifier.id):
                interface.remove_item(button)
                break

        try:
            await message.edit(view=interface)
        except HTTPException as exc:
            return await ctx.warn(
                "Failed to update the ticket panel",
                codeblock(exc.text),
            )

        return await ctx.approve(f"Removed the button with identifier {identifier}")

    @ticket_button.command(name="list")
    @has_permissions(manage_channels=True)
    async def ticket_button_list(self, ctx: Context) -> Message:
        """View all the buttons on the ticket panel."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        message = await settings.fetch_message()
        if not message:
            return await ctx.warn(
                "The ticket panel has not been set yet",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        interface = Interface.from_message(message)
        buttons = [
            f"**{f'{button.emoji} ' if button.emoji else ''}{button.label}** [{identifier}]"
            for button in interface.children
            if isinstance(button, Button)
            and button.custom_id
            and button.custom_id.startswith("ticket.create:button:")
            and (identifier := Identifier(id=button.custom_id.split(":")[-1]))
        ]
        if not buttons:
            return await ctx.warn("There are no buttons on the ticket panel")

        embed = Embed(title="Ticket Buttons")
        paginator = Paginator(ctx, buttons, embed)
        return await paginator.start()

    @ticket.group(
        name="dropdown",
        aliases=("select", "menu"),
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def ticket_dropdown(self, ctx: Context) -> Message:
        """Configure the dropdowns on the ticket panel."""

        return await ctx.send_help(ctx.command)

    @ticket_dropdown.command(name="create")
    @has_permissions(manage_channels=True)
    async def ticket_dropdown_create(
        self,
        ctx: Context,
        *,
        placeholder: Optional[str] = None,
    ) -> Message:
        """Create the initial dropdown for the ticket panel."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        message = await settings.fetch_message()
        if not message:
            return await ctx.warn(
                "The ticket panel has not been set yet",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        interface = Interface.from_message(message)
        identifier = Identifier.create()
        interface.add_item(
            Select(
                placeholder=placeholder or "Select a subject..",
                custom_id=f"ticket.create:dropdown:{identifier.id}",
                min_values=1,
                max_values=1,
                options=[SelectOption(label="..", value="..")],
            ),
        )

        try:
            await message.edit(view=interface)
        except HTTPException as exc:
            return await ctx.warn(
                "Failed to update the ticket panel",
                codeblock(exc.text),
            )

        query = "INSERT INTO tickets.dropdown (id, guild_id) VALUES ($1, $2)"
        await ctx.bot.db.execute(query, identifier.id, ctx.guild.id)

        return await ctx.approve(
            f"Added the dropdown with identifier {identifier}",
            "This identifier above is important & used to configure the dropdown",
        )

    @ticket_dropdown.group(
        name="option",
        aliases=("item",),
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def ticket_dropdown_option(self, ctx: Context) -> Message:
        """Configure the dropdown options."""

        return await ctx.send_help(ctx.command)

    @ticket_dropdown_option.command(name="add", extras={"flags": DropdownFlags})
    @has_permissions(manage_channels=True)
    async def ticket_dropdown_option_add(
        self,
        ctx: Context,
        identifier: Identifier,
        *,
        label: str,
    ) -> Message:
        """Add an option to the dropdown on the ticket panel."""

        label, flags = await DropdownFlags().find(ctx, label)
        if not label:
            return await ctx.send_help(ctx.command)

        settings = await Settings.fetch(self.bot, ctx.guild)
        message = await settings.fetch_message()
        if not message:
            return await ctx.warn(
                "The ticket panel has not been set yet",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        interface = Interface.from_message(message)
        option_identifier = Identifier.create()
        for dropdown in list(interface.children):
            if not isinstance(dropdown, Select):
                continue

            for option in list(dropdown.options):
                if option.value == "..":
                    dropdown.options.remove(option)

            if (dropdown.custom_id or "").endswith(identifier.id):
                dropdown.add_option(
                    label=label,
                    emoji=flags.emoji,
                    description=flags.description,
                    value=option_identifier.id,
                )
                break
        else:
            return await ctx.warn(
                "The dropdown with that identifier was not found",
                "Use `ticket dropdown list` to find the correct identifier",
            )

        try:
            await message.edit(view=interface)
        except HTTPException as exc:
            return await ctx.warn(
                "Failed to update the ticket panel",
                codeblock(exc.text),
            )

        return await ctx.approve(
            f"Added the option **{f'{flags.emoji} ' if flags.emoji else ''}{label}** with identifier {option_identifier}",
        )

    @ticket_dropdown_option.command(
        name="edit",
        aliases=("update",),
        extras={"flags": DropdownFlags},
    )
    @has_permissions(manage_channels=True)
    async def ticket_dropdown_option_edit(
        self,
        ctx: Context,
        identifier: Identifier,
        option_identifier: Identifier,
        *,
        label: str,
    ) -> Message:
        """Update an existing option on the dropdown on the ticket panel."""

        label, flags = await DropdownFlags().find(ctx, label)
        if not label:
            return await ctx.send_help(ctx.command)

        settings = await Settings.fetch(self.bot, ctx.guild)
        message = await settings.fetch_message()
        if not message:
            return await ctx.warn(
                "The ticket panel has not been set yet",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        interface = Interface.from_message(message)
        for dropdown in list(interface.children):
            if not isinstance(dropdown, Select):
                continue

            if (dropdown.custom_id or "").endswith(identifier.id):
                for option in dropdown.options:
                    if option.value != option_identifier.id:
                        continue

                    option.label = label
                    option.emoji = flags.emoji
                    option.description = flags.description
                    break

                break
        else:
            return await ctx.warn(
                "The dropdown with that identifier was not found",
                "Use `ticket dropdown list` to find the correct identifier",
            )

        try:
            await message.edit(view=interface)
        except HTTPException as exc:
            return await ctx.warn(
                "Failed to update the ticket panel",
                codeblock(exc.text),
            )

        return await ctx.approve(
            f"Updated the option with identifier {option_identifier}"
        )

    @ticket_dropdown_option.command(name="move")
    @has_permissions(manage_channels=True)
    async def ticket_dropdown_option_move(
        self,
        ctx: Context,
        identifier: Identifier,
        option_identifier: Identifier,
        position: Range[int, 1, 25],
    ) -> Message:
        """Move an option to a specific position on the dropdown."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        message = await settings.fetch_message()
        if not message:
            return await ctx.warn(
                "The ticket panel has not been set yet",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        interface = Interface.from_message(message)
        for dropdown in list(interface.children):
            if not isinstance(dropdown, Select):
                continue

            if (dropdown.custom_id or "").endswith(identifier.id):
                for option in list(dropdown.options):
                    if option.value != option_identifier.id:
                        continue

                    dropdown.options.remove(option)
                    dropdown.options.insert(position - 1, option)
                    break

                break
        else:
            return await ctx.warn(
                "The dropdown with that identifier was not found",
                "Use `ticket dropdown list` to find the correct identifier",
            )

        try:
            await message.edit(view=interface)
        except HTTPException as exc:
            return await ctx.warn(
                "Failed to update the ticket panel",
                codeblock(exc.text),
            )

        return await ctx.approve(
            f"Moved the option with identifier {option_identifier} to position `{position}`"
        )

    @ticket_dropdown_option.command(
        name="remove",
        aliases=("delete", "rm"),
    )
    @has_permissions(manage_channels=True)
    async def ticket_dropdown_option_remove(
        self,
        ctx: Context,
        identifier: Identifier,
        option_identifier: Identifier,
    ) -> Message:
        """Remove an option from the dropdown on the ticket panel."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        message = await settings.fetch_message()
        if not message:
            return await ctx.warn(
                "The ticket panel has not been set yet",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        interface = Interface.from_message(message)
        for dropdown in list(interface.children):
            if not isinstance(dropdown, Select):
                continue

            if (dropdown.custom_id or "").endswith(identifier.id):
                for option in list(dropdown.options):
                    if option.value != option_identifier.id:
                        continue

                    dropdown.options.remove(option)
                    if not dropdown.options:
                        return await self.ticket_dropdown_remove(ctx, identifier)

                    break

                break
        else:
            return await ctx.warn(
                "The dropdown with that identifier was not found",
                "Use `ticket dropdown list` to find the correct identifier",
            )

        try:
            await message.edit(view=interface)
        except HTTPException as exc:
            return await ctx.warn(
                "Failed to update the ticket panel",
                codeblock(exc.text),
            )

        return await ctx.approve(
            f"Removed the option with identifier {option_identifier}"
        )

    @ticket_dropdown_option.command(name="list")
    @has_permissions(manage_channels=True)
    async def ticket_dropdown_option_list(
        self,
        ctx: Context,
        identifier: Identifier,
    ) -> Message:
        """View all the options on the dropdown."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        message = await settings.fetch_message()
        if not message:
            return await ctx.warn(
                "The ticket panel has not been set yet",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        interface = Interface.from_message(message)
        options = [
            f"**{f'{option.emoji} ' if option.emoji else ''}{option.label}** [{option_identifier}]"
            for dropdown in list(interface.children)
            if isinstance(dropdown, Select)
            and (dropdown.custom_id or "").endswith(identifier.id)
            for option in dropdown.options
            if (option_identifier := Identifier(id=option.value))
        ]
        if not options:
            return await ctx.warn("There are no options on the dropdown")

        embed = Embed(title="Ticket Dropdown Options")
        paginator = Paginator(ctx, options, embed)
        return await paginator.start()

    @ticket_dropdown.command(name="remove", aliases=("delete", "rm"))
    @has_permissions(manage_channels=True)
    async def ticket_dropdown_remove(
        self,
        ctx: Context,
        identifier: Identifier,
    ) -> Message:
        """Remove the dropdown from the ticket panel.

        The identifier can be found via `ticket dropdown list`."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        message = await settings.fetch_message()
        if not message:
            return await ctx.warn(
                "The ticket panel has not been set yet",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        query = "DELETE FROM tickets.dropdown WHERE identifier = $1"
        result = await ctx.bot.db.execute(query, identifier.id)
        if result == "DELETE 0":
            return await ctx.warn(
                "The dropdown with that identifier was not found",
                "Use `ticket dropdown list` to find the correct identifier",
            )

        interface = Interface.from_message(message)
        for dropdown in list(interface.children):
            if not isinstance(dropdown, Select):
                continue

            if (dropdown.custom_id or "").endswith(identifier.id):
                interface.remove_item(dropdown)
                break

        try:
            await message.edit(view=interface)
        except HTTPException as exc:
            return await ctx.warn(
                "Failed to update the ticket panel",
                codeblock(exc.text),
            )

        return await ctx.approve(f"Removed the dropdown with identifier {identifier}")

    @ticket_dropdown.command(name="list")
    @has_permissions(manage_channels=True)
    async def ticket_dropdown_list(self, ctx: Context) -> Message:
        """View all the dropdowns on the ticket panel."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        message = await settings.fetch_message()
        if not message:
            return await ctx.warn(
                "The ticket panel has not been set yet",
                f"Use `{ctx.clean_prefix}ticket panel <message>` to set it",
            )

        interface = Interface.from_message(message)
        dropdowns = [
            f"**{dropdown.placeholder}** [{identifier}]"
            for dropdown in interface.children
            if isinstance(dropdown, Select)
            and dropdown.custom_id
            and dropdown.custom_id.startswith("ticket.create:dropdown:")
            if (identifier := Identifier(id=dropdown.custom_id.split(":")[-1]))
        ]
        if not dropdowns:
            return await ctx.warn("There are no dropdowns on the ticket panel")

        embed = Embed(title="Ticket Dropdowns")
        paginator = Paginator(ctx, dropdowns, embed)
        return await paginator.start()

    @ticket.group(
        name="transcripts",
        aliases=("transcript", "logs"),
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def ticket_transcripts(
        self,
        ctx: Context,
        channel: TextChannel | Thread | Literal["dm"] = parameter(
            displayed_name="channel|dm"
        ),
    ) -> Message:
        """Set a destination for ticket transcripts.

        This can be a text channel, thread, or DMs,
        re-run the command to remove the destination."""

        channel_id = (
            str(channel.id) if isinstance(channel, (TextChannel, Thread)) else channel
        )
        settings = await Settings.fetch(self.bot, ctx.guild)
        if channel in settings.transcript_destinations:
            await ctx.prompt(
                "This destination is already set, do you want to remove it?"
            )
            settings.record["transcript_destinations"].remove(channel_id)
        else:
            settings.record["transcript_destinations"].append(channel_id)

        await settings.upsert()
        return await ctx.approve(
            f"{'Now' if channel in settings.transcript_destinations else 'No longer'} sending transcripts to {channel.mention if isinstance(channel, (TextChannel, Thread)) else 'DMs'}"
        )

    @ticket_transcripts.command(name="view")
    @has_permissions(manage_channels=True)
    async def ticket_transcripts_view(self, ctx: Context) -> Message:
        """View all the destinations for ticket transcripts."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        if not settings.transcript_destinations:
            return await ctx.warn(
                "There aren't any destinations for ticket transcripts"
            )

        destinations = human_join(
            [
                destination.mention
                if isinstance(destination, (TextChannel, Thread))
                else "DMs"
                for destination in settings.transcript_destinations
            ],
            final="and",
        )
        return await ctx.respond(f"Ticket transcripts are being sent to {destinations}")

    async def ticket_transcripts_route(self, request: Request):
        """Handle the backend route for ticket transcripts."""

        identifier = request.match_info.get("identifier")
        if not identifier:
            return json_response({"error": "No identifier provided"}, status=400)

        transcript = await Transcript.fetch(self.bot, identifier)
        if not transcript:
            return json_response({"error": "Transcript not found"}, status=404)

        guild = self.bot.get_guild(transcript.guild_id)
        user = self.bot.get_user(transcript.user_id)
        fmt: list[str] = [
            f"Transcript for {user or 'Unknown User'} ({transcript.user_id}) in {guild or 'Unknown Guild'} ({transcript.guild_id})"
        ]
        for message in transcript.messages:
            fmt.append(
                f"[{message.created_at:%d/%m/%Y - %H:%M}] {message.author.username} ({message.author.id}): {message.content or "No content available"}"
            )

        return Response(text="\n".join(fmt), content_type="text/plain")

    @ticket.group(
        name="blacklist",
        aliases=("block", "ignore"),
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def ticket_blacklist(
        self,
        ctx: Context,
        *,
        target: Annotated[Role, StrictRole] | Annotated[Member, HierarchyMember],
    ) -> Message:
        """Prevent a role or member from creating tickets."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        if target in settings.blacklisted:
            await ctx.prompt(
                f"That {'role' if isinstance(target, Role) else 'member'} is already blacklisted, do you want to unblacklist {target.mention}?"
            )
            settings.record["blacklisted_ids"].remove(target.id)
        else:
            settings.record["blacklisted_ids"].append(target.id)

        await settings.upsert()
        return await ctx.approve(
            f"{'Now' if target in settings.blacklisted else 'No longer'} allowing "
            f"{'members with ' if isinstance(target, Role) else ''}{target.mention} to create tickets"
        )

    @ticket_blacklist.command(name="list")
    @has_permissions(manage_channels=True)
    async def ticket_blacklist_list(self, ctx: Context) -> Message:
        """View all the blacklisted roles and members."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        blacklisted = [
            f"{target.mention} [`{target.id}`]" for target in settings.blacklisted
        ]
        if not blacklisted:
            return await ctx.warn("There aren't any blacklisted roles or members")

        embed = Embed(title="Blacklisted Roles & Members")
        paginator = Paginator(ctx, blacklisted, embed)
        return await paginator.start()

    @ticket.group(
        name="staff",
        aliases=("admin", "mod"),
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def ticket_staff(
        self,
        ctx: Context,
        *,
        role: Annotated[Role, StrictRole],
    ) -> Message:
        """Allow a role to view & manage tickets."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        if role in settings.staff_roles:
            await ctx.prompt("That role is already staff, do you want to remove it?")
            settings.record["staff_role_ids"].remove(role.id)
        else:
            settings.record["staff_role_ids"].append(role.id)

        await settings.upsert()
        return await ctx.approve(
            f"{'Now' if role in settings.staff_roles else 'No longer'} allowing {role.mention} to manage tickets"
        )

    @ticket_staff.command(name="list")
    @has_permissions(manage_channels=True)
    async def ticket_staff_list(self, ctx: Context) -> Message:
        """View all the staff roles."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        staff_roles = [f"{role.mention} [`{role.id}`]" for role in settings.staff_roles]
        if not staff_roles:
            return await ctx.warn("There aren't any staff roles")

        embed = Embed(title="Staff Roles")
        paginator = Paginator(ctx, staff_roles, embed)
        return await paginator.start()

    @ticket.command(name="add", aliases=("permit", "allow"))
    @in_ticket()
    @staff()
    async def ticket_add(
        self,
        ctx: TicketContext,
        *,
        target: Annotated[Role, StrictRole] | Annotated[Member, HierarchyMember],
    ) -> Message:
        """Add a role or member to the ticket."""

        await ctx.channel.set_permissions(
            target,
            view_channel=True,
            reason=f"Added to the ticket by {ctx.author} ({ctx.author.id})",
        )
        return await ctx.approve(f"Added {target.mention} to the ticket")

    @ticket.command(name="remove", aliases=("hide", "deny"))
    @in_ticket()
    @staff()
    async def ticket_remove(
        self,
        ctx: TicketContext,
        *,
        target: Annotated[Role, StrictRole] | Annotated[Member, HierarchyMember],
    ) -> Message:
        """Remove a role or member from the ticket."""

        await ctx.channel.set_permissions(
            target,
            overwrite=None,
            reason=f"Removed from the ticket by {ctx.author} ({ctx.author.id})",
        )
        return await ctx.approve(f"Removed {target.mention} from the ticket")

    @ticket.command(name="close", aliases=("end", "delete"))
    @in_ticket()
    @staff()
    async def ticket_close(self, ctx: TicketContext) -> Optional[Message]:
        """Close the ticket and forward the transcript."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        if settings.transcript_destinations:
            async with ctx.typing():
                await Transcript.create(settings, ctx.guild, ctx.channel, ctx.ticket)

        await ctx.channel.delete(
            reason=f"Ticket closed by {ctx.author} ({ctx.author.id})"
        )
