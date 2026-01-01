from fastapi import APIRouter
from . import media, instagram, tiktok

router = APIRouter()
for route in (media, instagram, tiktok):
    router.include_router(route.router)