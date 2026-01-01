from io import BytesIO
import re
from secrets import token_urlsafe
from typing import List, Optional

from discord import File

from bot.core import Juno
from bot.extensions.social.reposters import Information, Reposter
from config import cookies


class TikTok(Reposter):
    def __init__(self, bot: Juno, **kwargs):
        super().__init__(
            bot,
            regex=[
                r"(?:https?://(?:vt|vm|www)\.tiktok\.com/(?:t/)?[a-zA-Z\d]+\/?)",
                r"(?:https?://(?:www\.)?tiktok\.com/[@\w.]+/(?:video|photo)/(\d+)(?:\?|\/?)?)"
            ],
            **kwargs,
        )

    async def fetch(self, url: str) -> Optional[Information]:
        if not any(substring in url for substring in ["video", "photo"]):
            response = await self.bot.session.get(
                url,
                allow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
                    "Cookie": "; ".join(
                        f"{cookie.name}={cookie.value}" for cookie in cookies
                    ),
                },
            )
            if not response.ok:
                return None
            
            url = str(response.url)

        match = re.match(self.regex[-1], url)
        if not match:
            return None
        
        post_id = match.group(1)
        response = await self.bot.session.get(
            "https://www.tiktok.com/player/api/v1/items",
            params={"item_ids": post_id},
        )
        if not response.ok:
            return None
        
        data = await response.json()
        post = data["items"][0]
        files: List[File] = []
        if (image := post.get("image_post_info")):
            for image in image["images"]:
                response = await self.bot.session.get(
                    image["display_image"]["url_list"][-1], allow_redirects=True
                )
                buffer = await response.read()
                if len(buffer) == 0:
                    continue

                files.append(File(BytesIO(buffer), filename=f"{token_urlsafe(16)}.jpg"))

        elif (video := post.get("video_info")):
            response = await self.bot.session.get(video["url_list"][-1], allow_redirects=True)
            buffer = await response.read()
            files.append(File(BytesIO(buffer), filename=f"{token_urlsafe(16)}.mp4"))

        return Information(
            id=post["id"],
            title=post["desc"],
            files=files,
        )  # type: ignore
