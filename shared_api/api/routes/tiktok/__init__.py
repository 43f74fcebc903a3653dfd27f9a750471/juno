import re
from fastapi import APIRouter, Request
from fastapi.responses import UJSONResponse
from loguru import logger
from api.shared import services, retry
from pydantic import BaseModel
from typing import List
from contextlib import suppress
from ujson import JSONDecodeError, loads
from bs4 import BeautifulSoup
from yarl import URL
from .models import TikTokUser, TikTokPost, query

router = APIRouter(
    prefix="/tiktok",
    tags=["TikTok"],
)


class TiktokPostRequest(BaseModel):
    url: str


class TikTokPostsResponse(BaseModel):
    user: TikTokUser
    posts: List[TikTokPost]


POST_PATTERNS = [
    r"(?:https?://(?:vt|vm|www)\.tiktok\.com/(?:t/)?[a-zA-Z\d]+\/?)",
    r"(?:https?://(?:www\.)?tiktok\.com/[@\w.]+/(?:video|photo)/(\d+)(?:\?|\/?)?)",
]


@router.get("/post", response_model=TikTokPost)
@services.cache(ttl="30m", key="{url}", prefix="tiktok:post")
async def tiktok_post(request: Request, url: str):
    """Fetch a TikTok video or slideshow."""

    if not any(re.fullmatch(pattern, url) for pattern in POST_PATTERNS):
        return UJSONResponse(
            {"error": "The URL provided doesn't appear to be a TikTok post."},
            status_code=400,
        )

    # ensure the URL has the ID in it
    if not any(substring in url for substring in ("video", "photo")):
        response = await services.session.get(url, allow_redirects=True)
        if not response.ok:
            return UJSONResponse(
                {"error": "The URL provided doesn't appear to be a TikTok post."},
                status_code=400,
            )

        url = str(response.url)

    match = re.match(POST_PATTERNS[-1], url)
    if not match:
        return UJSONResponse(
            {"error": "The URL provided doesn't appear to be a TikTok post."},
            status_code=400,
        )

    post_id = match.group(1)
    logger.info("Fetching TikTok post with ID: {}", post_id)
    try:
        async with retry(3, wait=2):
            response = await services.session.get(
                "https://www.tiktok.com/player/api/v1/items",
                params={"item_ids": post_id},
            )
            response.raise_for_status()
    except Exception:
        return UJSONResponse(
            {"error": "The URL provided couldn't be resolved."}, status_code=400
        )

    data = await response.json()
    if not data["items"]:
        return UJSONResponse(
            {"error": "The URL provided couldn't be resolved."}, status_code=400
        )

    post = data["items"][0]
    post["statistics"] = {
        "likes": post["statistics_info"]["digg_count"],
        "comments": post["statistics_info"]["comment_count"],
        "shares": post["statistics_info"]["share_count"],
        "views": 0,
    }
    post["author"] = {
        "id": "0",
        "sec_uid": post["author_info"]["secret_id"],
        "username": post["author_info"]["unique_id"],
        "full_name": post["author_info"]["nickname"],
        "avatar": services.passive_save(
            post["author_info"]["avatar_url_list"][-1],
            prefix="TikTok",
            extension="jpg",
        ),
    }

    final_post = TikTokPost(**post)
    if slideshow := post.get("image_post_info"):
        final_post.images = [
            services.passive_save(
                image["display_image"]["url_list"][-1],
                prefix="TikTok",
                extension="jpg",
            )
            for image in slideshow["images"]
        ]
    else:
        final_post.video = services.passive_save(
            f"https://tikwm.com/video/media/play/{final_post.id}.mp4",
            prefix="TikTok",
            extension="mp4",
        )

    return final_post


@router.get("/{username}", response_model=TikTokUser)
@services.cache(ttl="30m", key="{username}", prefix="tiktok:user")
async def tiktok_user(request: Request, username: str):
    """Fetch a TikTok user's profile."""

    username = username.lstrip("@")
    response = await services.session.get(
        URL.build(
            scheme="https",
            host="www.tiktok.com",
            path=f"/@{username}",
        ),
    )
    html = await response.text()
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
    if not script:
        return UJSONResponse(
            {"error": "The user provided couldn't be resolved."}, status_code=400
        )

    with suppress(JSONDecodeError, KeyError):
        data = loads(script.text)
        user = data["__DEFAULT_SCOPE__"]["webapp.user-detail"]["userInfo"]
        return TikTokUser(
            **user["user"],
            avatar=services.passive_save(
                user["user"]["avatarLarger"],
                prefix="TikTok",
                extension="jpg",
            ),
            statistics=user["stats"],
        )

    return UJSONResponse(
        {"error": "The user provided couldn't be resolved."}, status_code=400
    )


@router.get("/{username}/posts", response_model=TikTokPostsResponse)
@services.cache(ttl="5m", key="{username}", prefix="tiktok:posts")
async def tiktok_posts(request: Request, username: str):
    """Fetch a TikTok user's posts."""
    
    username = username.lstrip("@")
    user = await tiktok_user(request, username)
    if isinstance(user, UJSONResponse):
        return user

    response = await services.session.get(
        URL.build(
            scheme="https",
            host="www.tiktok.com",
            path="/api/creator/item_list/",
        ),
        params=query(user.sec_uid, limit=15),
    )
    data = await response.json()
    if data["status_code"] != 0:
        return TikTokPostsResponse(user=user, posts=[])

    posts: List[TikTokPost] = []
    for post in data.get("itemList", []):
        required = ("author", "stats", "video")
        if not all(key in post for key in required):
            continue

        post.pop("video", None)
        post["author"]["avatar"] = services.passive_save(
            post["author"]["avatarThumb"],
            prefix="TikTok",
            extension="jpg",
        )

        final_post = TikTokPost(**post)
        if slideshow := post.get("imagePost", {}).get("images", []):
            final_post.images = [
                services.passive_save(
                    image["imageURL"]["urlList"][-1],
                    prefix="TikTok",
                    extension="jpg",
                )
                for image in slideshow
            ]
        else:
            final_post.video = services.passive_save(
                # post.video["playAddr"],
                f"https://tikwm.com/video/media/play/{final_post.id}.mp4",
                prefix="TikTok",
                extension="mp4",
            )

        posts.append(final_post)

    return TikTokPostsResponse(user=user, posts=posts)


@router.get("/{username}/reposts", response_model=TikTokPostsResponse)
@services.cache(ttl="5m", key="{username}", prefix="tiktok:reposts")
async def tiktok_reposts(request: Request, username: str):
    """Fetch a TikTok user's repost"""

    username = username.lstrip("@")
    user = await tiktok_user(request, username)
    if isinstance(user, UJSONResponse):
        return user

    response = await services.session.get(
        URL.build(
            scheme="https",
            host="www.tiktok.com",
            path="/api/repost/item_list/",
        ),
        params=query(user.sec_uid, limit=15),
    )
    data = await response.json()
    if data["status_code"] != 0:
        return TikTokPostsResponse(user=user, posts=[])

    posts: List[TikTokPost] = []
    for post in data.get("itemList", []):
        required = ("author", "stats", "video")
        if not all(key in post for key in required):
            continue

        post.pop("video", None)
        post["author"]["avatar"] = services.passive_save(
            post["author"]["avatarThumb"],
            prefix="TikTok",
            extension="jpg",
        )

        final_post = TikTokPost(**post)
        if slideshow := post.get("imagePost", {}).get("images", []):
            final_post.images = [
                services.passive_save(
                    image["imageURL"]["urlList"][-1],
                    prefix="TikTok",
                    extension="jpg",
                )
                for image in slideshow
            ]
        else:
            final_post.video = services.passive_save(
                # post.video["playAddr"],
                f"https://tikwm.com/video/media/play/{final_post.id}.mp4",
                prefix="TikTok",
                extension="mp4",
            )

        posts.append(final_post)

    return TikTokPostsResponse(user=user, posts=posts)
