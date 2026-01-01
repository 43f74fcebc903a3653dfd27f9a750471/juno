from __future__ import annotations

from contextlib import suppress
from json import dumps
from logging import getLogger
from typing import TYPE_CHECKING, Any, Optional, Set, TypedDict, cast

from aiohttp import WSCloseCode
from aiohttp import WSMsgType as MessageType
from aiohttp.web import Request, Response
from aiohttp.web import WebSocketResponse as WebSocket
from discord import Guild, Member

from ..oauth import OAuth
from .interfaces import PartialGuild, PartialMember

if TYPE_CHECKING:
    from bot.core import Juno


logger = getLogger("bot.backend.gateway")


class Message(TypedDict):
    event: str
    data: dict[str, Any]


class GatewayManager:
    bot: Juno
    oauth: OAuth
    connections: dict[int, Set[WebSocket]] = {}

    def __init__(self, bot: Juno, oauth: OAuth) -> None:
        self.bot = bot
        self.oauth = oauth
        self.connections = {}

    async def handle(self, request: Request) -> WebSocket | Response:
        websocket = WebSocket()
        await websocket.prepare(request)
        if not all(key in request.query for key in ("token", "guild_id")):
            await websocket.close(
                code=WSCloseCode.UNSUPPORTED_DATA,
                message=b"Missing token or guild_id",
            )
            return websocket

        try:
            guild, member = await self.verify(request)
        except ValueError as exc:
            logger.warning(f"Verification for {request.remote} failed", exc_info=exc)
            await websocket.close(
                code=WSCloseCode.UNSUPPORTED_DATA,
                message=str(exc).encode(),
            )
            return websocket

        await self.connect(guild, member, websocket)

        received_heartbeats = 0
        await self.send(
            websocket,
            Message(
                event="PREPARE",
                data={
                    "interval": 45000,
                    "id": guild.id,
                },  # 45 seconds
            ),
        )
        await self.send(
            websocket,
            {
                "event": "IDENTIFY",
                "data": {
                    "guild": PartialGuild.parse(guild).model_dump(mode="json"),
                    "member": PartialMember.parse(member).model_dump(mode="json"),
                },
            },
        )

        async for msg in websocket:
            if msg.type in (MessageType.ERROR, MessageType.CLOSED):
                logger.info(f"Websocket closed with code {websocket.close_code}")
                await self.disconnect(guild, websocket)

                break

            if msg.type == MessageType.TEXT:
                message: Message = msg.json()
                if not isinstance(message, dict) or not all(
                    key in message for key in ("event", "data")
                ):
                    logger.warning("Invalid message received")
                    continue

                if message["event"] == "HEARTBEAT":
                    received_heartbeats += 1
                    logger.debug(f"Heartbeat for {guild} received by {member}")
                    await self.send(
                        websocket,
                        {
                            "event": "HEARTBEAT_ACK",
                            "data": {"received": received_heartbeats},
                        },
                    )

                else:
                    logger.warning(f"Unknown event {message['event']} received")

        return websocket

    async def verify(self, request: Request) -> tuple[Guild, Member]:
        try:
            token = request.query["token"]
            guild_id = int(request.query["guild_id"])
        except ValueError:
            raise ValueError("Invalid token or guild_id")

        query = "SELECT user_id FROM oauth WHERE token = $1"
        user_id = cast(Optional[int], await self.bot.db.fetchval(query, token))
        if not user_id:
            raise ValueError("The token provided isn't valid")

        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(user_id) if guild else None
        if not guild or not member:
            raise ValueError("You are not a member of this guild")

        return guild, member

    async def connect(self, guild: Guild, member: Member, websocket: WebSocket) -> None:
        if guild.id not in self.connections:
            self.connections[guild.id] = set()

        self.connections[guild.id].add(websocket)

    async def disconnect(self, guild: Guild, websocket: WebSocket) -> None:
        with suppress(KeyError):
            if guild.id in self.connections:
                self.connections[guild.id].discard(websocket)
                if not self.connections[guild.id]:
                    del self.connections[guild.id]

    async def send(self, websocket: WebSocket, message: Message) -> None:
        try:
            await websocket.send_str(dumps(message))
        except ConnectionResetError:
            for websocket_set in self.connections.values():
                websocket_set.discard(websocket)

    async def broadcast(self, guild_id: int, message: Message) -> None:
        for websocket in set(self.connections.get(guild_id, [])):
            await self.send(websocket, message)

    async def broadcast_all(self, message: Message) -> None:
        for guild_id in self.connections.keys():
            await self.broadcast(guild_id, message)
