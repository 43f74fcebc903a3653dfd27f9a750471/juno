from fastapi import APIRouter, Request, Response
from fastapi.responses import UJSONResponse
from api.shared import services
from filetype import guess_mime

router = APIRouter(
    prefix="/media",
    tags=["Cache"],
)


@router.get(
    "/{filename}",
    description="Retrieve a file from the cache.",
)
@services.cache(ttl="24h", key="{filename}")
async def media_fetch(request: Request, filename: str):
    media = services.passive_cache.get(filename)
    if not media:
        return UJSONResponse(
            {"error": "File not found."},
            status_code=404,
        )

    async with services.session.get(media.original_url, **media.kwargs) as response:
        buffer = await response.read()
        return Response(buffer, media_type=guess_mime(buffer))
