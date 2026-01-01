import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from aiohttp import ClientSession
from discord.ext.commands import CommandError
from logging import getLogger
from cashews import cache
from yarl import URL

logger = getLogger("bot.fortnite")
SWITCH_TOKEN = "OThmN2U0MmMyZTNhNGY4NmE3NGViNDNmYmI0MWVkMzk6MGEyNDQ5YTItMDAxYS00NTFlLWFmZWMtM2U4MTI5MDFjNGQ3"


@dataclass
class AuthSession:
    access_token: str
    device_code: str
    user_code: str


@dataclass
class AuthData:
    user_id: int
    display_name: str
    account_id: str
    device_id: str
    secret: str
    access_token: str
    expires_at: datetime
    avatar_url: Optional[str] = None


class CosmeticService:
    identifiers: dict[str, str] = {}

    def __init__(self):
        asyncio.create_task(self.load_identifiers())

    async def load_identifiers(self) -> None:
        async with ClientSession() as client:
            async with client.get(
                URL.build(
                    scheme="https",
                    host="fortnite.gg",
                    path="/api/items.json",
                )
            ) as response:
                data = await response.json()
                self.identifiers = {key.lower(): value for key, value in data.items()}

    @staticmethod
    def create_variant(
        *,
        config_overrides: Dict[str, str] = {},
        **kwargs: Any,
    ) -> List[Dict[str, Union[str, int]]]:
        default_config = {
            "pattern": "Mat{}",
            "numeric": "Numeric.{}",
            "clothing_color": "Mat{}",
            "jersey_color": "Color.{}",
            "parts": "Stage{}",
            "progressive": "Stage{}",
            "particle": "Emissive{}",
            "material": "Mat{}",
            "emissive": "Emissive{}",
            "profile_banner": "{}",
        }
        config = {**default_config, **config_overrides}

        data = []
        for channel, value in kwargs.items():
            v = {
                "c": "".join(x.capitalize() for x in channel.split("_")),
                "dE": 0,
            }

            if channel == "JerseyColor":
                v["v"] = config[channel].format(value.upper())
            else:
                v["v"] = config[channel].format(value)

            data.append(v)

        return data


class SessionManager:
    client: ClientSession
    cosmetic_service: CosmeticService

    def __init__(self):
        self.client = ClientSession(
            headers={
                "User-Agent": "Fortnite/++Fortnite+Release-24.10-CL-24850983 Windows/10.0.22621.1.256.64bit"
            }
        )
        self.cosmetic_service = CosmeticService()

    async def initiate_login(self) -> AuthSession:
        async with self.client.post(
            URL.build(
                scheme="https",
                host="account-public-service-prod.ol.epicgames.com",
                path="/account/api/oauth/token",
            ),
            data={"grant_type": "client_credentials"},
            headers=dict(Authorization=f"Basic {SWITCH_TOKEN}"),
        ) as response:
            data = await response.json()
            device_code, user_code = await self.create_device_code(data["access_token"])
            return AuthSession(
                access_token=data["access_token"],
                device_code=device_code,
                user_code=user_code,
            )

    async def create_device_code(self, access_token: str) -> tuple[str, str]:
        async with self.client.post(
            URL.build(
                scheme="https",
                host="account-public-service-prod03.ol.epicgames.com",
                path="/account/api/oauth/deviceAuthorization",
            ),
            data={},
            headers=dict(Authorization=f"Bearer {access_token}"),
        ) as response:
            data = await response.json()
            return data["device_code"], data["user_code"]

    async def poll_device_code(self, device_code: str) -> Optional[AuthData]:
        """Polls the device code until the user has authorized the device."""

        start_time = datetime.now()
        while True:
            if (datetime.now() - start_time).total_seconds() > 240:
                return None

            async with self.client.post(
                URL.build(
                    scheme="https",
                    host="account-public-service-prod03.ol.epicgames.com",
                    path="/account/api/oauth/token",
                ),
                data={
                    "grant_type": "device_code",
                    "device_code": device_code,
                },
                headers=dict(Authorization=f"Basic {SWITCH_TOKEN}"),
                ) as response:
                    data = await response.json()
                    if response.status == 200:
                        break

                    if not data.get("errorCode", "").endswith(("pending", "not_found")):
                        logger.error(f"Error while polling device code: {data}")
                        break

            await asyncio.sleep(10)
            
        async with self.client.post(
            URL.build(
            scheme="https",
            host="account-public-service-prod.ol.epicgames.com",
            path=f"/account/api/public/account/{data['account_id']}/deviceAuth",
            ),
            json={},
            headers=dict(Authorization=f"Bearer {data['access_token']}"),
        ) as response:
            device = await response.json()
            return AuthData(
                user_id=0,
                display_name=data["displayName"],
                account_id=data["account_id"],
                device_id=device["deviceId"],
                secret=device["secret"],
                access_token=data["access_token"],
                expires_at=datetime.fromisoformat(data["expires_at"]),
            )

    async def revalidate(self, data: AuthData) -> AuthData:
        async with self.client.post(
            URL.build(
                scheme="https",
                host="account-public-service-prod.ol.epicgames.com",
                path="/account/api/oauth/token",
            ),
            data={
                "grant_type": "device_auth",
                "account_id": data.account_id,
                "device_id": data.device_id,
                "secret": data.secret,
            },
            headers=dict(Authorization=f"Basic {SWITCH_TOKEN}"),
        ) as response:
            new_data = await response.json()
            return AuthData(
                user_id=data.user_id,
                display_name=new_data["displayName"],
                account_id=new_data["account_id"],
                device_id=data.device_id,
                secret=data.secret,
                access_token=new_data["access_token"],
                expires_at=datetime.fromisoformat(new_data["expires_at"]),
            )

    @cache(ttl="30m")
    async def get_avatar(self, auth: AuthData) -> str:
        async with self.client.get(
            URL.build(
                scheme="https",
                host="avatar-service-prod.identity.live.on.epicgames.com",
                path="/v1/avatar/fortnite/ids",
            ),
            params={"accountIds": auth.account_id},
            headers=dict(Authorization=f"Bearer {auth.access_token}"),
        ) as response:
            data = await response.json()
            if "errorMessage" in data:
                raise CommandError(data["errorMessage"])
            
            avatar_id = "CID_001_Athena_Commando_F_Default"
            if data and (avatar_id := data[0].get("avatarId")):
                avatar_id = avatar_id.replace("ATHENACHARACTER:", "")

            return f"https://fortnite-api.com/images/cosmetics/br/{avatar_id}/icon.png"

    @cache(ttl="10s")
    async def get_party(self, auth: AuthData) -> Optional[str]:
        """Get the user's party ID."""

        async with self.client.get(
            URL.build(
                scheme="https",
                host="party-service-prod.ol.epicgames.com",
                path=f"/party/api/v1/Fortnite/user/{auth.account_id}",
            ),
            headers={
                "Authorization": f"bearer {auth.access_token}",
            },
        ) as response:
            data = await response.json()
            if not data.get("current"):
                return None

            return data["current"][0]["id"]

    async def patch_party(
        self,
        auth: AuthData,
        payload: dict,
        revision: Optional[str] = None,
    ) -> bool:
        """Patch the user's party with the given payload."""

        party_id = await self.get_party(auth)
        if not party_id:
            raise CommandError("You need to be in the lobby to use this command")

        async with self.client.patch(
            URL.build(
                scheme="https",
                host="party-service-prod.ol.epicgames.com",
                path=f"/party/api/v1/Fortnite/parties/{party_id}/members/{auth.account_id}/meta",
            ),
            json={
                "delete": [],
                "revision": int(revision or 1),
                "update": payload,
            },
            headers={
                "Authorization": f"Bearer {auth.access_token}",
            },
        ) as response:
            if response.status == 204:
                return True

            data = await response.json()
            if "stale_revision" in data.get("errorCode", ""):
                return await self.patch_party(auth, payload, max(data["messageVars"]))

            if error := data.get("errorMessage"):
                raise CommandError(
                    f"An error occurred while patching the party\n{error}"
                )

            return True
