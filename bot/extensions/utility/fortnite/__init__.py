import asyncio
from base64 import urlsafe_b64encode
from datetime import datetime, timedelta
from io import BytesIO
from json import loads, dumps, JSONDecodeError
from logging import getLogger
from typing import List, Literal, Optional, TypedDict, cast
import zlib

import stackprinter
from yarl import URL
from asyncpg import UniqueViolationError
from discord import Embed, File, HTTPException, Message
from discord.ext.commands import (
    CommandError,
    BucketType,
    Cog,
    max_concurrency,
    cooldown,
    group,
    flag,
)
from discord.ext.tasks import loop
from discord.ui import View, Button
from discord.utils import format_dt, sleep_until, utcnow
from humanize import naturaldelta

from bot.core import Context, Juno
from bot.shared import Paginator, codeblock
from bot.shared.converters import FlagConverter
from bot.shared.formatter import plural

from .models import MOTD, Cosmetic, Map, Shop
from .manager import SessionManager, AuthData

logger = getLogger("bot.fortnite")
session = SessionManager()


class CosmeticRecord(TypedDict):
    user_id: int
    cosmetic_id: int
    cosmetic_name: str
    cosmetic_type: str


class AggregatedReminderRecord(TypedDict):
    user_id: int
    cosmetic_ids: List[str]


class AuthorizationRecord(TypedDict):
    user_id: int
    display_name: str
    account_id: str
    device_id: str
    secret: str
    access_token: str
    expires_at: datetime


class DeviceContext(Context):
    auth: AuthData


class MCPFlags(FlagConverter):
    operation: str = flag(
        description="The operation to perform",
    )
    route: Literal["client", "public", "dedicated"] = flag(
        default="client",
        description="The MCP route to request",
    )
    profile: Literal["athena", "common_core", "common_public", "campaign"] = flag(
        default="common_core",
        description="The MCP profile to request",
    )


CURRENCY_TYPES = {
    "Currency:MtxComplimentary": "Epic Games & Refunds",
    "Currency:MtxGiveaway": "Battlepass & Challenges",
    "Currency:MtxPurchased": "Purchased",
}


class Fortnite(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.check_fortnite_shop.start()
        return await super().cog_load()

    async def cog_unload(self) -> None:
        await session.client.close()
        self.check_fortnite_shop.cancel()
        return await super().cog_unload()

    async def cog_command_error(self, ctx: Context, error: Exception) -> None:
        if not ctx.command.qualified_name.startswith("fortnite"):
            return await super().cog_command_error(ctx, error)

        if "profileChanges" in stackprinter.format(error):
            query = "DELETE FROM fortnite.authorization WHERE user_id = $1"
            await self.bot.db.execute(query, ctx.author.id)
            raise CommandError("Your Fortnite account is banned or you're unauthenticated")
        
        return await super().cog_command_error(ctx, error)
    
    async def cog_before_invoke(self, ctx: Context) -> None:
        if not ctx.command.qualified_name.startswith("fortnite"):
            return await super().cog_before_invoke(ctx)

        requires_auth = (
            self.fortnite_compose,
            self.fortnite_equip,
            self.fortnite_spoof,
            self.fortnite_locker,
            self.fortnite_summary,
            self.fortnite_unadd,
            self.fortnite_humans,
        )
        if ctx.command not in requires_auth and (
            not ctx.command.parent or ctx.command.parent not in requires_auth
        ):
            return await super().cog_before_invoke(ctx)

        query = "SELECT * FROM fortnite.authorization WHERE user_id = $1"
        record = await self.bot.db.fetchrow(query, ctx.author.id)
        if not record:
            raise CommandError("You haven't linked your Epic Games account yet")

        ctx = cast(DeviceContext, ctx)
        ctx.auth = AuthData(**record)
        if ctx.auth.expires_at < utcnow():
            try:
                ctx.auth = await session.revalidate(ctx.auth)
            except (KeyError, ValueError):
                query = "DELETE FROM fortnite.authorization WHERE user_id = $1"
                await self.bot.db.execute(query, ctx.author.id)
                raise CommandError("Your session has expired, please re-authenticate")

            query = """
            UPDATE fortnite.authorization
            SET access_token = $2, expires_at = $3
            WHERE user_id = $1
            """
            await self.bot.db.execute(
                query,
                ctx.author.id,
                ctx.auth.access_token,
                ctx.auth.expires_at,
            )

        return await super().cog_before_invoke(ctx)
            
    @loop(hours=24)
    async def check_fortnite_shop(self) -> None:
        """Dispatch cosmetic reminders for items in the shop."""

        shop: Optional[Shop] = None
        for _ in range(3):
            await asyncio.sleep(160)
            try:
                shop = await Shop.fetch()
            except ValueError:
                continue

            break

        if not shop:
            logger.error("The Fortnite API didn't return a shop rotation")
            return

        query = """
        SELECT
            user_id,
            ARRAY_AGG(cosmetic_id) AS cosmetic_ids
        FROM fortnite.reminder
        WHERE cosmetic_id = ANY($1::TEXT[])
        GROUP BY user_id
        """
        records = cast(
            List[AggregatedReminderRecord],
            await self.bot.db.fetch(query, shop.cosmetic_ids) or [],
        )

        scheduled_deletion: List[int] = []
        for record in records:
            user = self.bot.get_user(record["user_id"])
            if not user:
                logger.info(
                    "User %s not found, skipping reminder dispatch",
                    record["user_id"],
                )
                continue

            cosmetics = list(
                filter(
                    lambda x: x.id in record["cosmetic_ids"],
                    shop.cosmetics,
                )
            )
            if not cosmetics:
                continue

            phrase = "cosmetic is" if len(cosmetics) == 1 else "cosmetics are"
            embed = Embed(
                url="https://fortnite.com/shop",
                title="Fortnite Shop",
                description=(
                    f"The following {phrase} now in the shop\n"
                    + "\n".join(
                        f"> {cosmetic} for **{self.bot.config.emojis.vbucks} {cosmetic.price}**"
                        for cosmetic in cosmetics
                    )
                ),
            )
            if len(cosmetics) == 1:
                embed.set_image(url=cosmetics[0].images.icon)

            try:
                await user.send(embed=embed)
            except HTTPException as exc:
                if exc.code == 50007:
                    scheduled_deletion.append(user.id)
                else:
                    logger.exception("Failed to send reminder to user %s", user)

        if scheduled_deletion:
            query = "DELETE FROM fortnite.reminder WHERE user_id = ANY($1::BIGINT[])"
            await self.bot.db.execute(query, scheduled_deletion)

    @check_fortnite_shop.before_loop
    async def before_check_fortnite_shop(self) -> None:
        """Wait until 00:00 UTC."""

        await self.bot.wait_until_ready()

        now = utcnow()
        next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if now > next_midnight:
            next_midnight += timedelta(days=1)

        logger.info(
            f"Waiting {naturaldelta(next_midnight - now)} for the next shop rotation"
        )
        await sleep_until(next_midnight)

    @group(aliases=("fort", "fn"), invoke_without_command=True)
    async def fortnite(self, ctx: Context) -> Message:
        """Fortnite related commands."""

        return await ctx.send_help(ctx.command)

    @fortnite.command(
        name="login",
        aliases=(
            "signin",
            "connect",
        ),
    )
    @max_concurrency(1, BucketType.user)
    async def fortnite_login(self, ctx: Context) -> Message:
        """Authenticate with Epic Games."""

        embed = Embed(
            title="Epic Games Authentication",
            description="\n".join(
                [
                    "Click the button below to link your Epic Games account to the bot",
                    "Once you've authenticated, you'll be able to use various Fortnite commands",
                ]
            ),
        )

        auth = await session.initiate_login()
        view = View()
        view.add_item(
            Button(
                emoji="🔗",
                label="Authenticate",
                url=URL.build(
                    scheme="https",
                    host="www.epicgames.com",
                    path="/activate",
                    query={"userCode": auth.user_code},
                ).human_repr(),
            )
        )

        try:
            prompt = await ctx.author.send(embed=embed, view=view)
        except HTTPException as exc:
            if exc.code == 50007:
                return await ctx.warn(
                    "I couldn't DM you. Please enable DMs and try again."
                )
            raise

        await ctx.message.add_reaction("📩")
        data = await session.poll_device_code(auth.device_code)
        if not data:
            embed.description = "The authentication request has expired"
            return await prompt.edit(embed=embed, view=None)

        logger.info(
            "Authenticated account %r (%s) for %s (%s)",
            data.display_name,
            data.account_id,
            ctx.author,
            ctx.author.id,
        )
        await self.bot.db.execute(
            """
            INSERT INTO fortnite.authorization (
                user_id,
                display_name,
                account_id,
                device_id,
                secret,
                access_token,
                expires_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (user_id) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                account_id = EXCLUDED.account_id,
                device_id = EXCLUDED.device_id,
                secret = EXCLUDED.secret,
                access_token = EXCLUDED.access_token,
                expires_at = EXCLUDED.expires_at
            """,
            ctx.author.id,
            data.display_name,
            data.account_id,
            data.device_id,
            data.secret,
            data.access_token,
            data.expires_at,
        )

        embed.description = "\n".join(
            [
                f"Successfully authenticated as `{data.display_name}`",
                "You can now use the various Fortnite commands",
            ]
        )
        return await prompt.edit(embed=embed, view=None)

    @fortnite.command(name="unadd", aliases=("unaddall",))
    @max_concurrency(1, BucketType.user)
    async def fortnite_unadd(self, ctx: DeviceContext) -> Message:
        """Remove all friends from your account."""

        await ctx.prompt("Are you sure you want to remove all friends from your account?")
        async with ctx.typing(), session.client.delete(
            URL.build(
                scheme="https",
                host="friends-public-service-prod.ol.epicgames.com",
                path=f"/friends/api/v1/{ctx.auth.account_id}/friends",
            ),
            headers={
                "Authorization": f"bearer {ctx.auth.access_token}",
            },
        ) as response:
            if not response.ok:
                data = await response.json()
                return await ctx.warn(
                    data.get(
                        "errorMessage",
                        f"An error occurred while removing friends (`{response.status}`)",
                    )
                )

            return await ctx.approve("Successfully removed all friends")

    @fortnite.command(
        name="compose",
        aliases=("composemcp", "mcp"),
        extras={"flags": MCPFlags},
    )
    @max_concurrency(1, BucketType.user)
    async def fortnite_compose(self, ctx: DeviceContext, *, payload: str) -> Message:
        """Compose an MCP request operation."""

        payload, flags = await MCPFlags().find(ctx, payload)
        if not payload:
            return await ctx.warn("You need to provide a payload to dispatch")

        try:
            payload = loads(payload)
        except JSONDecodeError:
            return await ctx.warn("The payload is not a valid JSON object")

        route = flags.route
        if route == "dedicated":
            route += "_server"

        async with ctx.typing(), session.client.post(
            URL.build(
                scheme="https",
                host="fortnite-public-service-prod11.ol.epicgames.com",
                path=f"/fortnite/api/game/v2/profile/{ctx.auth.account_id}/{route}/{flags.operation}",
            ),
            params={"profileId": flags.profile, "rvn": -1},
            json=payload,
            headers={
                "Authorization": f"bearer {ctx.auth.access_token}",
            },
        ) as response:
            data = await response.json()
            if not response.ok:
                return await ctx.warn(
                    data.get(
                        "errorMessage",
                        f"An error occurred while composing the MCP request (`{response.status}`)",
                    )
                )

            output = dumps(data, indent=2)
            try:
                if len(output) > 128:
                    embed = Embed(
                        description=f"> Request to `{flags.operation}` as `{flags.route}`"
                    )
                    file = File(BytesIO(output.encode()), filename="output.json")
                    await ctx.author.send(embed=embed, file=file)
                else:
                    await ctx.author.send(codeblock(output, "json"))
            except HTTPException as exc:
                if exc.code == 50007:
                    return await ctx.warn("I couldn't DM you the output")
                raise

            return await ctx.approve("The MCP output has been sent to your DMs")

    @fortnite.command(name="equip", aliases=("ghost", "ghostequip", "geq"))
    @max_concurrency(1, BucketType.user)
    async def fortnite_equip(
        self,
        ctx: DeviceContext,
        *,
        cosmetic: Cosmetic,
    ) -> Message:
        """Equip any cosmetic in the lobby."""

        payload = {}
        if cosmetic.type.lower() in ("outfit", "backpack", "pickaxe"):
            _def = "character" if cosmetic.type == "Outfit" else cosmetic.type
            payload = {
                "Default:AthenaCosmeticLoadout_j": dumps(
                    {
                        "AthenaCosmeticLoadout": {
                            f"{_def.lower()}PrimaryAssetId": f"Athena{_def.title()}:{cosmetic.id.lower()}"
                        }
                    }
                )
            }

        elif cosmetic.type.lower() == "emote":
            payload = {
                "Default:FrontendEmote_j": dumps(
                    {
                        "FrontendEmote": {
                            "emoteItemDef": f"/BRCosmetics/Athena/Items/Cosmetics/Dances/{cosmetic.id.lower()}.{cosmetic.id.lower()}",
                            "emoteSection": -2,
                        }
                    }
                )
            }

        variant = {}
        if cosmetic.name == "Skull Trooper":
            variant = session.cosmetic_service.create_variant(clothing_color=1)

        elif cosmetic.name == "Ghoul Trooper":
            variant = session.cosmetic_service.create_variant(material=3)

        if variant:
            payload["Default:AthenaCosmeticLoadoutVariants_j"] = dumps(
                {
                    "AthenaCosmeticLoadoutVariants": {
                        "vL": {"athenaCharacter": {"i": variant}}
                    }
                }
            )

        if not payload:
            return await ctx.warn("That cosmetic can't be equipped")

        async with ctx.typing():
            await session.patch_party(ctx.auth, payload)
            return await ctx.approve(
                f"Successfully equipped {cosmetic}",
                "-# This is only visible to other players, not you",
            )

    @fortnite.group(
        name="spoof",
        aliases=("fake", "set"),
        invoke_without_command=True,
    )
    async def fortnite_spoof(self, ctx: DeviceContext) -> Message:
        """Spoof various Battle Royale stats."""

        return await ctx.send_help(ctx.command)

    @fortnite_spoof.command(name="level", aliases=("xp",))
    async def fortnite_spoof_level(self, ctx: DeviceContext, amount: int) -> Message:
        """Spoof your level for the current season."""

        async with ctx.typing():
            await session.patch_party(
                ctx.auth,
                {
                    "Default:AthenaBannerInfo_j": dumps(
                        {"AthenaBannerInfo": {"seasonLevel": amount}}
                    )
                },
            )
            return await ctx.approve(
                f"Successfully set your level to `{amount:,}`",
                "-# This is only visible to other players, not you",
            )

    @fortnite_spoof.command(name="crowns", aliases=("crown", "wins"))
    async def fortnite_spoof_crowns(self, ctx: DeviceContext, amount: int) -> Message:
        """Spoof your crown count for the current season."""

        async with ctx.typing():
            await session.patch_party(
                ctx.auth,
                {
                    "Default:AthenaCosmeticLoadout_j": dumps(
                        {
                            "AthenaCosmeticLoadout": {
                                "cosmeticStats": [
                                    {
                                        "statName": "TotalVictoryCrowns",
                                        "statValue": 0,
                                    },
                                    {
                                        "statName": "TotalRoyalRoyales",
                                        "statValue": amount,
                                    },
                                    {"statName": "HasCrown", "statValue": 1},
                                ]
                            }
                        }
                    ),
                },
            )
            await session.patch_party(
                ctx.auth,
                {
                    "Default:FrontendEmote_j": dumps(
                    {
                        "FrontendEmote": {
                            "emoteItemDef": "None",
                        }
                    }
                )
                },
            )
            await session.patch_party(
                ctx.auth,
                {
                    "Default:FrontendEmote_j": dumps(
                    {
                        "FrontendEmote": {
                            "emoteItemDef": "/BRCosmetics/Athena/Items/Cosmetics/Dances/eid_coronet.eid_coronet",
                            "emoteSection": -2,
                        }
                    }
                )
                },
            )
            return await ctx.approve(
                f"Successfully set your crown wins to `{amount:,}`",
                "-# This is only visible to other players, not you",
            )

    @fortnite.command(name="locker", aliases=("cosmetics", "items"))
    @cooldown(2, 30, BucketType.channel)
    async def fortnite_locker(self, ctx: DeviceContext) -> Message:
        """View your locker on fortnite.gg."""

        await ctx.respond("Generating your locker preview..")
        async with ctx.typing(), session.client.post(
            URL.build(
                scheme="https",
                host="fortnite-public-service-prod11.ol.epicgames.com",
                path=f"/fortnite/api/game/v2/profile/{ctx.auth.account_id}/client/QueryProfile",
            ),
            params={"profileId": "athena"},
            json={},
            headers={
                "Authorization": f"bearer {ctx.auth.access_token}",
            },
        ) as response:
            data = await response.json()
            profile = data["profileChanges"][0]["profile"]
            cosmetics = profile["items"]
            if not cosmetics:
                return await ctx.warn(
                    "You don't have any items in your locker",
                    edit_response=True,
                )

            identifiers = []
            for cosmetic in cosmetics.values():
                template_id = cosmetic["templateId"].split(":")[-1].lower()
                if template_id in session.cosmetic_service.identifiers:
                    identifiers.append(
                        int(session.cosmetic_service.identifiers[template_id])
                    )

            identifiers.sort()
            diff = [
                identifiers[i] - identifiers[i - 1] if i > 0 else identifiers[i]
                for i in range(len(identifiers))
            ]
            created_at = profile["created"]
            compress = zlib.compressobj(
                level=-1,
                method=zlib.DEFLATED,
                wbits=-9,
                memLevel=zlib.DEF_MEM_LEVEL,
                strategy=zlib.Z_DEFAULT_STRATEGY,
            )
            compressed = compress.compress(
                f"{created_at},{','.join(map(str, diff))}".encode()
            )
            compressed += compress.flush()
            encoded = urlsafe_b64encode(compressed).decode().rstrip("=")
            final_url = f"https://fortnite.gg/my-locker?items={encoded}&bot=juno&game=br&type=outfit&sort=rarity"

        return await ctx.respond(
            f"Click [**here**]({final_url}) to view your locker with {plural(len(identifiers), md='`'):cosmetic}",
            edit_response=True,
        )

    @fortnite.command(name="summary", aliases=("purchases", "vbucks"))
    async def fortnite_summary(self, ctx: DeviceContext) -> Message:
        """View how much V-Bucks you've bought."""

        async with ctx.typing(), session.client.post(
            URL.build(
                scheme="https",
                host="fortnite-public-service-prod11.ol.epicgames.com",
                path=f"/fortnite/api/game/v2/profile/{ctx.auth.account_id}/client/QueryProfile",
            ),
            params={"profileId": "common_core"},
            json={},
            headers={
                "Authorization": f"bearer {ctx.auth.access_token}",
            },
        ) as response:
            avatar_url = await session.get_avatar(ctx.auth)
            data = await response.json()
            profile = data["profileChanges"][0]["profile"]
            stats = profile["stats"]["attributes"]
            platform = stats["current_mtx_platform"]
            currency = [
                {
                    "platform": (
                        CURRENCY_TYPES[item["templateId"]]
                        if "Purchased" not in item["templateId"]
                        else f"{item['attributes']['platform']} {CURRENCY_TYPES[item['templateId']]}"
                    ),
                    "amount": item["quantity"],
                }
                for item in profile["items"].values()
                if item["templateId"].startswith("Currency:Mtx")
            ]
            fulfillment_counts = stats.get("in_app_purchases", {}).get(
                "fulfillmentCounts", {}
            )
            CURRENCY_PRICES = {
                "FN_1000_POINTS": 8.99,
                "FN_2800_POINTS": 22.99,
                "FN_5000_POINTS": 36.99,
                "FN_7500_POINTS": 67.42,
                "FN_13500_POINTS": 89.99,
            }
            usd_spent = sum(
                CURRENCY_PRICES[k] * amount
                for k, amount in fulfillment_counts.items()
                if k.startswith("FN_")
            )
            total_spent = sum(
                int(k.split("_")[1]) * amount
                for k, amount in fulfillment_counts.items()
                if k.startswith("FN_")
            )

        embed = Embed(title="V-Bucks Summary")
        embed.set_author(name=ctx.auth.display_name, icon_url=avatar_url)
        embed.add_field(name="Platform", value=platform)
        embed.add_field(
            name="Total Spent",
            value=f"{self.bot.config.emojis.vbucks} {total_spent:,} (${int(usd_spent):,} USD)",
        )
        embed.add_field(
            name=f"{sum(item['amount'] for item in currency)} V-Bucks Currently",
            value="\n".join(
                f"> {item['platform']} - {self.bot.config.emojis.vbucks} {item['amount']:,}"
                for item in currency
            ),
            inline=False,
        )
        return await ctx.send(embed=embed)

    @fortnite.command(name="humans", aliases=("realplayers", "real"))
    async def fortnite_humans(self, ctx: DeviceContext) -> Message:
        """View how many real players are in your game."""

        async with ctx.typing(), session.client.get(
            URL.build(
                scheme="https",
                host="fngw-mcp-gc-livefn.ol.epicgames.com",
                path=f"/fortnite/api/matchmaking/session/findPlayer/{ctx.auth.account_id}",
            ),
            headers={
                "Authorization": f"bearer {ctx.auth.access_token}",
            },
        ) as response:
            data = await response.json()
            if not data:
                return await ctx.warn("You must be in a game to use this command")

            players = data[0]["totalPlayers"]
            server = data[0]["serverAddress"]
            port = data[0]["serverPort"]
            return await ctx.respond(
                f"There are `{players}` real players in your game on `{server}:{port}`"
            )

    @fortnite.command(name="map", aliases=("pois",))
    @cooldown(2, 30, BucketType.channel)
    async def fortnite_map(self, ctx: Context) -> Message:
        """View the Fortnite map."""

        _map = await Map.fetch()
        file = await _map.file()

        embed = Embed(
            title="Fortnite Map",
            description=f"There are currently {len(_map.pois)} locations on the map",
        )
        embed.set_image(url="attachment://map.png")

        return await ctx.send(embed=embed, file=file)

    @fortnite.command(name="news", aliases=("motd",))
    @cooldown(2, 30, BucketType.channel)
    async def fortnite_news(self, ctx: Context) -> Message:
        """View the latest Fortnite news."""

        news = await MOTD.fetch()
        embeds: List[Embed] = []
        for motd in news:
            embed = Embed(title=motd.title, description=motd.body)
            embed.set_image(url=motd.image)
            embeds.append(embed)

        paginator = Paginator(ctx, embeds)
        return await paginator.start()

    @fortnite.command(name="view", aliases=("cosmetic", "show"))
    async def fortnite_view(self, ctx: Context, *, cosmetic: Cosmetic) -> Message:
        """View information about a cosmetic."""

        embed = Embed(
            color=cosmetic.color, title=f"{cosmetic.name} ({cosmetic.pretty_type})"
        )
        embed.description = cosmetic.description or ""
        embed.set_thumbnail(url=cosmetic.images.icon)

        if cosmetic.history:
            embed.description += (
                f"\nIntroduced {format_dt(cosmetic.history.first_seen, style='R')}"
            )

        if cosmetic.price_icon == "vbucks":
            embed.set_footer(
                text=f"{cosmetic.price} V-Bucks",
                icon_url=cosmetic.price_icon_url,
            )
        else:
            embed.set_footer(
                text=cosmetic.price, icon_url=cosmetic.price_icon_url or None
            )

        if cosmetic.history and cosmetic.history.dates:
            embed.add_field(
                name="History",
                value=(
                    "\n".join(
                        f"{format_dt(date, 'D')} ({format_dt(date, 'R')})"
                        for date in sorted(cosmetic.history.dates, reverse=True)[:5]
                    )
                    + (
                        f"\n> +{plural(len(cosmetic.history.dates) - 5, md='`'):other occurrence}"
                        if len(cosmetic.history.dates) > 5
                        else ""
                    )
                ),
            )

        return await ctx.send(embed=embed)

    @fortnite.group(name="remind", aliases=("reminder",), invoke_without_command=True)
    async def fortnite_remind(
        self,
        ctx: Context,
        *,
        cosmetic: Optional[Cosmetic] = None,
    ) -> Message:
        """Receive notifications when an item is in the shop."""

        if not cosmetic:
            return await ctx.send_help(ctx.command)

        return await self.fortnite_remind_add(ctx, cosmetic=cosmetic)

    @fortnite_remind.command(name="add", aliases=("create",))
    async def fortnite_remind_add(self, ctx: Context, *, cosmetic: Cosmetic) -> Message:
        """Add a reminder for a cosmetic."""

        if cosmetic.price_icon != "vbucks":
            return await ctx.warn("That cosmetic isn't returning in the shop")

        query = """
        INSERT INTO fortnite.reminder (
            user_id,
            cosmetic_id,
            cosmetic_name,
            cosmetic_type
        ) VALUES (
            $1,
            $2,
            $3,
            $4
        )
        """
        try:
            await self.bot.db.execute(
                query,
                ctx.author.id,
                cosmetic.id,
                cosmetic.name,
                cosmetic.type,
            )
        except UniqueViolationError:
            return await ctx.warn(f"You're already receiving reminders for {cosmetic}")

        return await ctx.approve(
            f"You'll now be notified when {cosmetic} is in the shop"
        )

    @fortnite_remind.command(name="remove", aliases=("delete", "del", "rm"))
    async def fortnite_remind_remove(
        self,
        ctx: Context,
        *,
        cosmetic: Cosmetic,
    ) -> Message:
        """Remove a reminder for a cosmetic."""

        query = "DELETE FROM fortnite.reminder WHERE user_id = $1 AND cosmetic_id = $2"
        result = await self.bot.db.execute(query, ctx.author.id, cosmetic.id)
        if result == "DELETE 0":
            return await ctx.warn(f"You're not receiving reminders for {cosmetic}")

        return await ctx.approve(
            f"You'll no longer be notified when {cosmetic} is in the shop"
        )

    @fortnite_remind.command(name="clear", aliases=("reset",))
    async def fortnite_remind_clear(self, ctx: Context) -> Message:
        """Remove all cosmetic reminders."""

        await ctx.prompt("Are you sure you want to clear all your reminders?")

        query = "DELETE FROM fortnite.reminder WHERE user_id = $1"
        await self.bot.db.execute(query, ctx.author.id)
        return await ctx.approve("You'll no longer receive any reminders")

    @fortnite_remind.command(name="list")
    async def fortnite_remind_list(self, ctx: Context) -> Message:
        """View your cosmetic reminders."""

        query = "SELECT * FROM fortnite.reminder WHERE user_id = $1"
        records = cast(
            List[CosmeticRecord],
            await self.bot.db.fetch(query, ctx.author.id),
        )
        if not records:
            return await ctx.warn("You have no cosmetic reminders")

        cosmetics = [
            f"**{record['cosmetic_name']}** ({record['cosmetic_type'].replace('_', ' ').title()})"
            for record in records
        ]
        embed = Embed(title="Cosmetic Reminders")
        paginator = Paginator(ctx, cosmetics, embed=embed)
        return await paginator.start()
