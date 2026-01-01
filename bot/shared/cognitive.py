from base64 import b64encode
from logging import getLogger
from typing import Optional

from aiohttp import ClientSession
from cashews import cache
from discord.ext.commands import BadArgument
from yarl import URL

from bot.core import Context
from config import config

logger = getLogger("bot.cognitive")

CLARIFAI_KEY = config.api.clarifai
AZURE = config.api.azure


class AzureVoice(str):
    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> str:
        if not AZURE:
            raise BadArgument("Azure key not found")

        async with ClientSession() as session:
            async with session.get(
                URL.build(
                    scheme="https",
                    host=f"{AZURE.region}.tts.speech.microsoft.com",
                    path="/cognitiveservices/voices/list",
                ),
                headers={"Ocp-Apim-Subscription-Key": AZURE.key},
            ) as response:
                data = await response.json()
                for voice in data:
                    if voice["DisplayName"].lower() == argument.lower():
                        return voice["DisplayName"]

                raise BadArgument(f"Voice {argument} not found")


@cache(ttl="5m", key="cognitive:{key}")
async def extract_text(key: str | int, buffer: bytes) -> str:
    logger.debug(f"Extracting text from image with cache key of {key}")

    async with ClientSession() as session:
        async with session.post(
            URL.build(
                scheme="https",
                host="api.clarifai.com",
                path="/v2/users/clarifai/apps/main/models/ocr-scene-english-paddleocr/versions/46e99516c2d94f58baf2bcaf5a6a53a9/outputs",
            ),
            json={
                "inputs": [
                    {
                        "data": {
                            "image": {"base64": b64encode(buffer).decode()},
                        },
                    }
                ]
            },
            headers={"Authorization": f"Key {CLARIFAI_KEY}"},
        ) as response:
            data = await response.json()
            return "\n".join(
                region["data"]["text"]["raw"]
                for region in data["outputs"][0]["data"]["regions"]
            )


async def transcribe_audio(buffer: bytes, content_type: str) -> Optional[str]:
    if not AZURE:
        return

    async with ClientSession() as session:
        async with session.post(
            URL.build(
                scheme="https",
                host=f"{AZURE.region}.stt.speech.microsoft.com",
                path="/speech/recognition/conversation/cognitiveservices/v1",
            ),
            params={"language": "en-US", "format": "detailed"},
            headers={
                "Ocp-Apim-Subscription-Key": AZURE.key,
                "Content-Type": f"{content_type}; codecs=opus",
                "User-Agent": "juno",
            },
            data=buffer,
        ) as response:
            if not response.ok:
                return

            data = await response.json()
            return data["NBest"][0]["Display"]


async def synthesize_speech(text: str, voice: str) -> bytes:
    if not AZURE:
        return b""

    async with ClientSession() as session:
        async with session.post(
            URL.build(
                scheme="https",
                host=f"{AZURE.region}.tts.speech.microsoft.com",
                path="/cognitiveservices/v1",
            ),
            headers={
                "Ocp-Apim-Subscription-Key": AZURE.key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "ogg-24khz-16bit-mono-opus",
                "User-Agent": "juno",
            },
            data=f"""
            <speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">
                <voice name="en-US-{voice}Neural">
                    {text}
                </voice>
            </speak>
            """,
        ) as response:
            if not response.ok:
                return b""

            return await response.read()

@cache(ttl="5m", key="cognitive:is_image_safe:{buffer}")
async def is_image_safe(buffer: bytes) -> bool:
    async with ClientSession() as session:
        async with session.post(
            URL.build(
                scheme="https",
                host="api.sightengine.com",
                path="/1.0/check.json",
            ),
            params= {
                "models": "nudity-2.1,weapon,recreational_drug,medical,offensive,text-content,face-attributes,gore-2.0,violence,self-harm",
                "api_user": "1398642624",
                "api_secret": "faUxfR48eGjHprUhwc6ktachGaE8Mr4V",
            },
            data={"media": buffer},
        ) as response:
            data = await response.json()
            return not any([
                data["nudity"]["none"] < 0.80,
                data["nudity"]["erotica"] > 0.70,
                data["nudity"]["sexual_activity"] > 0.30,
                data["nudity"]["sexual_display"] > 0.30,
                data["gore"]["prob"] > 0.70,
                data["medical"]["prob"] > 0.70,
                data["recreational_drug"]["prob"] > 0.70,
                data["weapon"]["classes"]["firearm"] > 0.70,
                data["weapon"]["classes"]["knife"] > 0.70,
                data.get("offensive", {}).get("prob", 0) > 0.70,
                data.get("self_harm", {}).get("prob", 0) > 0.70
            ])