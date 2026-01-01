from __future__ import annotations

import asyncio
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, ParamSpec, TypeVar, no_type_check

import aiohttp_cors
import aiohttp_jinja2
import jinja2
from aiohttp import web
from aiohttp.abc import AbstractAccessLogger
from aiohttp.web import Application, Response
from discord.ext.commands import Cog, Command, Group

from config import config

from .gateway import GatewayManager
from .oauth import OAuth, OAuthRequest, auth_middleware

if TYPE_CHECKING:
    from bot.core import Juno

logger = getLogger("bot.backend")
templates = Path(__file__).parent / "templates"
T = TypeVar("T", bound=Response)
P = ParamSpec("P")


class AccessLogger(AbstractAccessLogger):
    def log(
        self,
        request: web.BaseRequest,
        response: web.StreamResponse,
        time: float,
    ) -> None:
        if response.status in (404, 302):
            return

        path = request.path
        if path.startswith("/pubsub"):
            path = path.replace(config.backend.pubsub_key, "******")

        elif request.method == "HEAD" or time <= 0.001:
            return

        logger.info(
            "{method} {path} {code} {size} {ms:0.0f}ms".format(
                method=request.method,
                path=path,
                code=response.status,
                size=self.format_size(response.body_length),
                ms=time * 1000,
            )
        )

    @staticmethod
    def format_size(num):
        if num >= 1024**2:
            return "{:0.1f}MB".format(num / 1024**2)
        elif num >= 1024:
            return "{:0.1f}KB".format(num / 1024)
        else:
            return "{:0.0f}B".format(num)


class Backend(Application):
    bot: Juno
    task: asyncio.Task
    oauth: OAuth
    gateway: GatewayManager

    @no_type_check
    def __init__(self, bot: Juno, *args, **kwargs):
        super().__init__(*args, **kwargs, middlewares=[auth_middleware])
        self.cors = aiohttp_cors.setup(
            self,
            defaults={
                "*": aiohttp_cors.ResourceOptions(
                    allow_credentials=True,
                    expose_headers="*",
                    allow_headers="*",
                )
            },
        )
        aiohttp_jinja2.setup(
            self,
            loader=jinja2.FileSystemLoader(templates),
        )
        self.bot = bot
        self.oauth = OAuth(bot)
        self.gateway = GatewayManager(bot, self.oauth)
        self._state["oauth"] = self.oauth
        self.router.add_get("/", self.index)
        self.router.add_get("/commands", self.commands)
        self.router.add_get("/x2/{tail:.*}", self.x2_redirect)
        self.router.add_static("/c", Path("/tmp/juno"))
        for method, route, handler in (
            ("GET", "/gateway", self.gateway.handle),
            ("GET", "/@me", self.oauth_identify),
            ("GET", "/@me/login", self.oauth_login),
            ("GET", "/@me/callback", self.oauth_callback),
        ):
            self.router.add_route(method, route, handler)

    def setup_cors(self):
        for route in list(self.router.routes()):
            self.cors.add(route)

    def start_task(self):
        self.task = asyncio.create_task(self.start(), name="backend")

    async def start(self):
        self.setup_cors()

        try:
            await web._run_app(
                self,
                host=config.backend.host,
                port=config.backend.port,
                access_log=logger,
                access_log_class=AccessLogger,
                print=None,
            )
        except OSError as e:
            logger.error("Backend failed to start", exc_info=e)

    async def stop(self):
        await self.shutdown()
        await self.cleanup()
        self.task.cancel()

    @aiohttp_jinja2.template("index.html")
    async def index(self, request: web.Request):
        return {}

    async def x2_redirect(self, request: web.Request):
        return web.HTTPFound(
            f"{self.bot.tixte.public_url}/{request.match_info['tail']}"
        )

    async def commands(self, request: web.Request):
        tree = ""
        for cog in sorted(
            self.bot.cogs.values(),
            key=lambda x: len(set(x.walk_commands())),
            reverse=True,
        ):
            if any(
                forbidden in cog.qualified_name.lower()
                for forbidden in ("jishaku", "developer")
            ):
                continue

            tree += self.build_tree(cog, first=len(tree) == 0)
            for command in list(cog.get_commands()):
                tree += self.build_tree(command, 1)

        return web.Response(text=tree, content_type="text/plain")

    async def oauth_identify(self, request: OAuthRequest):
        user, guilds = await asyncio.gather(
            self.oauth.identify(request.authorization),
            self.oauth.guilds(request.authorization),
        )
        if not user or guilds is None:
            return web.json_response({"error": "Unauthorized"}, status=401)

        return web.json_response(
            {
                "user": user.model_dump(mode="json"),
                "guilds": [guild.model_dump(mode="json") for guild in guilds],
            }
        )

    async def oauth_login(self, request: web.Request):
        return web.HTTPFound(self.oauth.login_url)

    async def oauth_callback(self, request: web.Request):
        code = request.query.get("code")
        if not code:
            return web.json_response(
                {"error": "The code parameter is required"},
                status=400,
            )

        authorization = await self.oauth.authorize(code)
        if not authorization:
            return web.json_response(
                {"error": "The code provided is invalid"},
                status=401,
            )

        user = await self.oauth.identify(authorization)
        if not user:
            return web.json_response({"error": "Unauthorized"}, status=401)

        token = await self.oauth.create_session(authorization, user)
        return web.HTTPFound(
            f"https://localhost:3000/callback?token={token}",
            headers={"Set-Cookie": f"token={token}; Max-Age=315360000; Path=/"},
        )

    def build_tree(
        self,
        command: Command | Cog,
        depth: int = 0,
        first: bool = False,
    ) -> str:
        line = "├──" if not first else "┌──"
        if isinstance(command, Cog):
            return f"{'│    ' * depth}{line} {command.qualified_name}\n"

        if command.hidden:
            return ""

        aliases = "|".join(command.aliases)
        if aliases:
            aliases = f"[{aliases}]"

        tree = f"{'│    ' * depth}{line} {command.qualified_name}{aliases}: {command.short_doc}\n"
        if isinstance(command, Group):
            for subcommand in command.commands:
                tree += self.build_tree(subcommand, depth + 1)

        return tree
