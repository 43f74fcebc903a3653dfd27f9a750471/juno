from typing import List, Optional, TypedDict, cast
from aiohttp.web import json_response
import zon
from bot.core import Juno
from bot.shared.formatter import human_join
from .boost import Boost
from .goodbye import Goodbye
from .schedule import Schedule
from .welcome import Welcome
from bot.core.backend.oauth import (
    OAuthRequest,
    has_permissions as has_oauth_permissions,
)

__all__ = ("System", "Welcome", "Goodbye", "Boost", "Schedule")
tables = ("welcome", "rejoin", "goodbye", "boost")


class Record(TypedDict):
    guild_id: int
    channel_id: int
    template: str
    delete_after: Optional[int]


schema = zon.record(
    {
        "guild_id": zon.number(),
        "channel_id": zon.number(),
        "template": zon.string(),
        "delete_after": zon.optional(zon.number().min(3).max(360)),
    }
)


class System(Welcome, Goodbye, Boost, Schedule):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        for method, route, handler in (
            (
                "GET",
                "/@auth/guilds/{guild_id}/system/{table}",
                self.oauth_system,
            ),
            (
                "PATCH",
                "/@auth/guilds/{guild_id}/system/{table}",
                self.oauth_system_update,
            ),
        ):
            self.bot.backend.router.add_route(method, route, handler)  # type: ignore

        return await super().cog_load()

    @has_oauth_permissions("manage_channels", "manage_messages")
    async def oauth_system(self, request: OAuthRequest):
        table = request.match_info["table"]
        if table not in tables:
            human_tables = human_join(tables)
            return json_response(
                {"error": f"The table must be {human_tables}"}, status=400
            )

        query = f"SELECT * FROM system.{table} WHERE guild_id = $1"
        records: List[Record] = [
            dict(record) # type: ignore
            for record in await self.bot.db.fetch(query, request.guild.id)
            if request.guild.get_channel_or_thread(record["channel_id"])
        ]
        return json_response(records)

    @has_oauth_permissions("manage_channels", "manage_messages")
    async def oauth_system_update(self, request: OAuthRequest):
        table = request.match_info["table"]
        if table not in tables:
            human_tables = human_join(tables)
            return json_response(
                {"error": f"The table must be {human_tables}"}, status=400
            )

        data = cast(Record, await request.json())
        try:
            schema.validate(data)
        except zon.ZonError as exc:
            return json_response({"error": exc.issues}, status=400)

        channel = request.guild.get_channel_or_thread(data["channel_id"])
        if not channel:
            return json_response(
                {"error": "The provided channel no longer exists"}, status=400
            )

        query = f"SELECT * FROM system.{table} WHERE guild_id = $1"
        records: List[Record] = [
            dict(record) # type: ignore
            for record in await self.bot.db.fetch(query, request.guild.id)
            if request.guild.get_channel_or_thread(record["channel_id"])
        ]
        if len(records) >= 3:
            return json_response(
                {"error": "You can only have up to 3 welcome messages"}, status=400
            )

        status = await self.bot.db.execute(
            f"""
            INSERT INTO system.{table} (
                guild_id,
                channel_id,
                template,
                delete_after
            ) VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, channel_id)
            DO UPDATE SET
                template = EXCLUDED.template,
                delete_after = EXCLUDED.delete_after
            """,
            request.guild.id,
            channel.id,
            data["template"],
            data["delete_after"],
        )
        if status.startswith("INSERT 0 1"):
            records.append(data)
        else:
            for record in records:
                if record["channel_id"] == channel.id:
                    record.update(data)
                    break

        return json_response(records)
