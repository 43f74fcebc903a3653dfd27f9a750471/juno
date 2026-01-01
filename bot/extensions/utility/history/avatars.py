import asyncio
from datetime import datetime
from io import BytesIO
from logging import getLogger
from typing import List, Optional, TypedDict, cast

import aiohttp_jinja2
from aiohttp.web import Request
from discord import DiscordException, HTTPException, Member, Message, User
from discord.ext.commands import BucketType, Cog, cooldown, group, parameter
from xxhash import xxh128_hexdigest

from bot.core import Context, Juno

logger = getLogger("bot.history")


class Record(TypedDict):
    user_id: int
    asset: str
    timestamp: datetime


class MetricsRecord(TypedDict):
    unique_assets: int
    first_record: datetime
    table_size: str


class OverallRecord(TypedDict):
    user_id: int
    count: int


class AvatarHistory(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.backend.router.add_get("/avatars/{user_id}", self.avatarhistory_route)
        return await super().cog_load()

    @aiohttp_jinja2.template("avatars.html")
    async def avatarhistory_route(self, request: Request) -> dict:
        try:
            user_id = int(request.match_info["user_id"])
        except ValueError:
            return {"error": "Invalid user ID"}

        query = "SELECT asset, timestamp FROM avatar_history WHERE user_id = $1 ORDER BY timestamp DESC"
        records = cast(List[Record], await self.bot.db.fetch(query, user_id))
        if not records:
            return {"error": "No avatar history found for this user"}

        user = self.bot.get_user(user_id)
        return {
            "username": user.display_name if user else user_id,
            "avatar_url": f"{self.bot.tixte.public_url}/{records[0]['asset']}",
            "records": [
                f"{self.bot.tixte.public_url}/{record['asset']}" for record in records
            ],
        }

    @Cog.listener("on_user_update")
    async def avatarhistory_save(self, before: User, after: User) -> None:
        if before.avatar == after.avatar or not after.avatar:
            return

        await asyncio.sleep(2)  # we're quicker than discord 😛
        try:
            buffer = await after.avatar.read()
            key = xxh128_hexdigest(buffer)
        except (AttributeError, DiscordException, HTTPException) as exc:
            return logger.warning(
                f"Failed to download avatar for {after} ({after.id})",
                exc_info=exc,
            )

        extension = after.avatar.is_animated() * "gif" or "png"
        key = await self.bot.tixte.upload(f"avatars/{key}.{extension}", BytesIO(buffer))

        query = "INSERT INTO avatar_history (user_id, asset) VALUES ($1, $2) ON CONFLICT DO NOTHING"
        await self.bot.db.execute(query, after.id, key)

    @group(
        aliases=(
            "avatars",
            "avs",
            "avh",
            "ah",
        ),
        invoke_without_command=True,
    )
    @cooldown(1, 5, BucketType.channel)
    async def avatarhistory(
        self,
        ctx: Context,
        *,
        user: Member | User = parameter(default=lambda ctx: ctx.author),
    ) -> Optional[Message]:
        """View a user's avatar history."""

        query = "SELECT asset FROM avatar_history WHERE user_id = $1 ORDER BY timestamp DESC"
        records = cast(List[Record], await self.bot.db.fetch(query, user.id))

        if not records:
            return await ctx.warn("No avatar history found for this user")

        return await ctx.send(
            f"Okay... {self.bot.config.backend.public_url}/avatars/{user.id}"
        )

    @avatarhistory.command(name="clear")
    async def avatarhistory_clear(self, ctx: Context) -> Message:
        """Remove your archived avatar history."""

        return await ctx.send("𝓱𝓮𝓵𝓵 𝓷𝓪 𝓯𝓷")
        # query = "DELETE FROM avatar_history WHERE user_id = $1"
        # await self.bot.db.execute(query, ctx.author.id)

        # return await ctx.respond("Your avatar history has been cleared")
