from fastapi import APIRouter, Request
from fastapi.responses import UJSONResponse
from playwright.async_api import Request as PlaywrightRequest, Error as PlaywrightError
from api.shared import BaseModel, services, extract_json
from typing import List
from .models import InstagramUser, SimpleUser, UserStatistics, Story, Highlight
from contextlib import suppress
from ujson import loads
from loguru import logger
import asyncio

router = APIRouter(
    prefix="/instagram",
    tags=["Instagram"],
)

class InstagramStoryResponse(BaseModel):
    user: InstagramUser
    stories: List[Story]

def validate_username(username: str) -> str:
    username = username.replace("'b", "")
    username = username.replace("'", "")
    username = username.lower()
    if len(username) > 30:
        raise ValueError("Usernames must be less than 30 characters")

    username = str(username).removeprefix("@")
    allowed_chars = ("_", ".")
    if username.endswith("."):
        raise ValueError("Usernames cannot end with periods")

    for char in username:
        if not char.isalnum() and char not in allowed_chars:
            raise ValueError(f"{char} is not allowed in Instagram usernames")

    return username


@router.get("/{username}", response_model=InstagramUser)
@services.cache(ttl="1h", key="{username}", prefix="instagram:user")
async def instagram_user(request: Request, username: str):
    """Fetch an Instagram user's profile."""

    try:
        username = validate_username(username)
    except ValueError as exc:
        logger.error("Received request for invalid username {}", username)
        return UJSONResponse({"error": str(exc)}, status_code=400)

    key = f"instagram:user:{username}"
    future, highlights = asyncio.get_running_loop().create_future(), []
    async with services.lock[key], services.browser.borrow_page() as page:

        async def user_request(request: PlaywrightRequest):
            with suppress(PlaywrightError, KeyError):
                if (
                    request.url == "https://www.instagram.com/graphql/query"
                    or "web_profile_info" in request.url
                ):
                    response = await request.response()
                    if not response:
                        return

                    body = await response.body()
                    if (
                        "friendship_status" in body.decode()
                        and username.lower() in body.decode().lower()
                        and not future.done()
                    ):
                        body = loads(body)
                        with suppress(asyncio.InvalidStateError):
                            logger.info("User {} resolved via GraphQL", username)
                            future.set_result(body["data"]["user"])

                    elif "highlights" in body.decode():
                        for edge in loads(body)["data"]["highlights"]["edges"]:
                            highlights.append(edge["node"])

                        logger.info(
                            "Resolved {} highlight(s) for user {}",
                            len(highlights),
                            username,
                        )

                elif request.url == f"https://www.instagram.com/{username}/":
                    await asyncio.sleep(0.5)
                    response = await request.response()
                    if not response:
                        return

                    await response.finished()
                    html = await page.content()
                    if "may be broken" in html:
                        return future.set_result(
                            {"error": "The user provided couldn't be resolved."}
                        )

                    data = await extract_json(html, "biography_with_entities")
                    if data:
                        future.set_result(data)
                        logger.info("User {} resolved via HTML extraction", username)

        page.on("request", user_request)
        try:
            await page.goto(
                f"https://www.instagram.com/{username}/",
                wait_until="domcontentloaded",
            )
            async with asyncio.timeout(12):
                data = await future
                await asyncio.sleep(1)

            if "error" in data:
                return UJSONResponse(data, status_code=400)

        except (asyncio.TimeoutError, Exception):
            return UJSONResponse(
                {"error": "The user provided couldn't be resolved."}, status_code=400
            )

        finally:
            page.remove_listener("request", user_request)

        user = InstagramUser(
            **data,
            avatar=services.passive_save(
                data["profile_pic_url"],
                prefix="Instagram",
                extension="jpg",
                headers=dict(Referer=f"https://www.instagram.com/{username}/"),
            ),
            statistics=UserStatistics(
                posts=data.get("media_count", 0),
                followers=data.get("follower_count", 0),
                following=data.get("following_count", 0),
            ),
        )
        user.highlights = []
        for highlight in highlights:
            highlight["id"] = highlight["id"].split(":")[-1]
            highlight["user"] = user
            user.highlights.append(
                Highlight(
                    **highlight,
                    cover=services.passive_save(
                        highlight["cover_media"]["cropped_image_version"]["url"],
                        prefix="Instagram",
                        extension="jpg",
                        headers=dict(Referer=f"https://www.instagram.com/{username}/"),
                    ),
                )
            )

        return user


@router.get("/{username}/story", response_model=InstagramStoryResponse)
@services.cache(ttl="5m", key="{username}", prefix="instagram:story")
async def instagram_story(request: Request, username: str):
    """Fetch an Instagram user's story."""

    try:
        username = validate_username(username)
    except ValueError as exc:
        logger.error("Received request for invalid username {}", username)
        return UJSONResponse({"error": str(exc)}, status_code=400)

    user = await instagram_user(request, username)
    if isinstance(user, UJSONResponse):
        return user

    key = f"instagram:story:{user.id}"
    data = {}
    async with services.lock[key], services.browser.borrow_page() as page:
        try:
            await page.goto(
                f"https://www.instagram.com/stories/{username}/",
                wait_until="domcontentloaded",
            )
            if f"https://www.instagram.com/{username}/" in page.url:
                return UJSONResponse(
                    {"error": "The user provided doesn't have an active story."},
                    status_code=400,
                )

            with suppress(PlaywrightError):
                if "Page not found" in await page.title():
                    logger.warning("Username {} is likely invalid.", username)
                    return UJSONResponse(
                        {"error": "The user provided couldn't be resolved."},
                        status_code=400,
                    )

            html = await page.content()
            data = await extract_json(html, "xdt_api__v1__feed__reels_media")
            if data:
                data = loads(data)["xdt_api__v1__feed__reels_media"]["reels_media"][0]
                logger.info("Story for user {} resolved via HTML extraction", username)
        except Exception:
            return UJSONResponse(
                {"error": "The user provided doesn't have an active story."},
                status_code=400,
            )

        stories: List[Story] = []
        for item in data["items"]:  # type: ignore
            item["id"] = item.pop("pk")
            item.pop("user", None)
            if item.get("video_versions"):
                media = services.passive_save(
                    item["video_versions"][0]["url"],
                    prefix="Instagram",
                    extension="mp4",
                    headers=dict(
                        Referer=f"https://www.instagram.com/stories/{username}/"
                    ),
                )

            elif item.get("image_versions2"):
                media = services.passive_save(
                    item["image_versions2"]["candidates"][0]["url"],
                    prefix="Instagram",
                    extension="jpg",
                    headers=dict(
                        Referer=f"https://www.instagram.com/stories/{username}/"
                    ),
                )

            story = Story(**item, user=user, media=media)
            stories.append(story)

        return InstagramStoryResponse(user=user, stories=stories)


@router.get("/highlight/{highlight_id}", response_model=Highlight)
@services.cache(ttl="15m", key="{highlight_id}", prefix="instagram:highlight")
async def instagram_highlight(request: Request, highlight_id: str):
    """Fetch an Instagram highlight."""

    highlight_id = highlight_id.split(":")[-1]
    key = f"instagram:highlight:{highlight_id}"
    future = asyncio.get_running_loop().create_future()
    async with services.lock[key], services.browser.borrow_page() as page:

        async def reels_request(request: PlaywrightRequest):
            if future.done():
                return

            with suppress(PlaywrightError, KeyError):
                if request.url == "https://www.instagram.com/graphql/query":
                    response = await request.response()
                    if not response:
                        return

                    body = await response.body()
                    if "xdt_api__v1__feed__reels_media__connection" in body.decode():
                        body = loads(body)
                        future.set_result(
                            {
                                "reels_media": [
                                    item["node"]
                                    for item in body["data"][
                                        "xdt_api__v1__feed__reels_media__connection"
                                    ]["edges"]
                                ],
                                "status": "ok",
                            }
                        )
                        logger.info("Highlight {} resolved via GraphQL", highlight_id)

                elif "feed/reels_media" in request.url:
                    response = await request.response()
                    if not response:
                        return

                    body = await response.body()
                    if "reels_media" in body.decode():
                        body = loads(body)
                        future.set_result(body)
                        logger.info(
                            "Highlight {} resolved via reels tray", highlight_id
                        )

                elif (
                    request.url
                    == f"https://www.instagram.com/stories/highlights/{highlight_id}/"
                ):
                    await asyncio.sleep(0.5)
                    response = await request.response()
                    if not response:
                        return

                    await response.finished()

                    html = await page.content()
                    if "xdt_api__v1__feed__reels_media__connection" in html:
                        data = await extract_json(
                            html, "xdt_api__v1__feed__reels_media__connection"
                        )
                        if data:
                            future.set_result(
                                {
                                    "reels_media": [
                                        item["node"]
                                        for item in loads(data)[
                                            "xdt_api__v1__feed__reels_media__connection"
                                        ]["edges"]
                                    ],
                                    "status": "ok",
                                }
                            )
                            logger.info(
                                "Highlight {} resolved via HTML extraction",
                                highlight_id,
                            )

        page.on("request", reels_request)
        try:
            await page.goto(
                f"https://www.instagram.com/stories/highlights/{highlight_id}/",
                wait_until="domcontentloaded",
            )
            async with asyncio.timeout(6):
                data = await future
                highlight = Highlight(
                    id=data["reels_media"][0]["id"].split(":")[-1],
                    title=data["reels_media"][0]["title"],
                    cover=services.passive_save(
                        data["reels_media"][0]["cover_media"]["cropped_image_version"][
                            "url"
                        ],
                        prefix="Instagram",
                        extension="jpg",
                        headers=dict(
                            Referer=f"https://www.instagram.com/stories/highlights/{highlight_id}/"
                        ),
                    ),
                )
                highlight.user = SimpleUser(
                    **data["reels_media"][0]["user"],
                    avatar=services.passive_save(
                        data["reels_media"][0]["user"]["profile_pic_url"],
                        prefix="Instagram",
                        extension="jpg",
                        headers=dict(
                            Referer=f"https://www.instagram.com/stories/highlights/{highlight_id}/"
                        ),
                    ),
                )
                highlight.items = []
                for item in data["reels_media"][0]["items"]:
                    item["id"] = item.pop("pk")
                    item.pop("user", None)
                    if item.get("video_versions"):
                        media = services.passive_save(
                            item["video_versions"][0]["url"],
                            prefix="Instagram",
                            extension="mp4",
                            headers=dict(
                                Referer=f"https://www.instagram.com/stories/highlights/{highlight_id}/"
                            ),
                        )

                    elif item.get("image_versions2"):
                        media = services.passive_save(
                            item["image_versions2"]["candidates"][0]["url"],
                            prefix="Instagram",
                            extension="jpg",
                            headers=dict(
                                Referer=f"https://www.instagram.com/stories/highlights/{highlight_id}/"
                            ),
                        )

                    story = Story(**item, user=highlight.user, media=media)
                    highlight.items.append(story)

                highlight.items.sort(key=lambda x: x.taken_at, reverse=True)
        except (asyncio.TimeoutError, Exception):
            return UJSONResponse(
                {"error": "The highlight provided couldn't be resolved."},
                status_code=400,
            )

        finally:
            page.remove_listener("request", reels_request)

        return highlight
