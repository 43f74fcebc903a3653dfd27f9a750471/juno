from __future__ import annotations

from typing import List, Optional, TypedDict

from pydantic import BaseModel
from yarl import URL

from bot.core import Context, Juno

SAFE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/119.0.0.0 Safari/537.36"
    )
}
LANGUAGES = {
    "af": "Afrikaans",
    "sq": "Albanian",
    "am": "Amharic",
    "ar": "Arabic",
    "hy": "Armenian",
    "az": "Azerbaijani",
    "eu": "Basque",
    "be": "Belarusian",
    "bn": "Bengali",
    "bs": "Bosnian",
    "bg": "Bulgarian",
    "ca": "Catalan",
    "ceb": "Cebuano",
    "ny": "Chichewa",
    "zh-cn": "Chinese (Simplified)",
    "zh-tw": "Chinese (Traditional)",
    "co": "Corsican",
    "hr": "Croatian",
    "cs": "Czech",
    "da": "Danish",
    "nl": "Dutch",
    "en": "English",
    "eo": "Esperanto",
    "et": "Estonian",
    "tl": "Filipino",
    "fi": "Finnish",
    "fr": "French",
    "fy": "Frisian",
    "gl": "Galician",
    "ka": "Georgian",
    "de": "German",
    "el": "Greek",
    "gu": "Gujarati",
    "ht": "Haitian Creole",
    "ha": "Hausa",
    "haw": "Hawaiian",
    "iw": "Hebrew",
    "he": "Hebrew",
    "hi": "Hindi",
    "hmn": "Hmong",
    "hu": "Hungarian",
    "is": "Icelandic",
    "ig": "Igbo",
    "id": "Indonesian",
    "ga": "Irish",
    "it": "Italian",
    "ja": "Japanese",
    "jw": "Javanese",
    "kn": "Kannada",
    "kk": "Kazakh",
    "km": "Khmer",
    "ko": "Korean",
    "ku": "Kurdish (Kurmanji)",
    "ky": "Kyrgyz",
    "lo": "Lao",
    "la": "Latin",
    "lv": "Latvian",
    "lt": "Lithuanian",
    "lb": "Luxembourgish",
    "mk": "Macedonian",
    "mg": "Malagasy",
    "ms": "Malay",
    "ml": "Malayalam",
    "mt": "Maltese",
    "mi": "Maori",
    "mr": "Marathi",
    "mn": "Mongolian",
    "my": "Myanmar (Burmese)",
    "ne": "Nepali",
    "no": "Norwegian",
    "or": "Odia",
    "ps": "Pashto",
    "fa": "Persian",
    "pl": "Polish",
    "pt": "Portuguese",
    "pa": "Punjabi",
    "ro": "Romanian",
    "ru": "Russian",
    "sm": "Samoan",
    "gd": "Scots Gaelic",
    "sr": "Serbian",
    "st": "Sesotho",
    "sn": "Shona",
    "sd": "Sindhi",
    "si": "Sinhala",
    "sk": "Slovak",
    "sl": "Slovenian",
    "so": "Somali",
    "es": "Spanish",
    "su": "Sundanese",
    "sw": "Swahili",
    "sv": "Swedish",
    "tg": "Tajik",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "ug": "Uyghur",
    "uz": "Uzbek",
    "vi": "Vietnamese",
    "cy": "Welsh",
    "xh": "Xhosa",
    "yi": "Yiddish",
    "yo": "Yoruba",
    "zu": "Zulu",
}


class TranslatedSentence(TypedDict):
    trans: str
    orig: str

class TranslationResult(BaseModel):
    text: str
    lang: str # language code
    language: str
    speech: Optional[str] # part of speech

    @property
    def details(self) -> str:
        return URL.build(
            scheme="https",
            host="translate.google.com",
            path="/details",
            query={
                "hl": "en",
                "sl": "auto",
                "tl": self.lang,
                "text": self.text,
                "op": "translate",
            },
        ).human_repr()
    
class GoogleTranslate(BaseModel):
    original: TranslationResult
    translated: TranslationResult

    @classmethod
    async def translate(
        cls,
        bot: Juno,
        query: str,
        *,
        source: str = "auto",
        target: str = "en",
    ) -> GoogleTranslate:
        async with bot.session.get(
            URL.build(
                scheme="https",
                host="translate.google.com",
                path="/translate_a/single",
            ),
            params={
                "dj": "1",
                "dt": ["sp", "t", "ld", "bd"],
                "client": "dict-chrome-ex",
                "sl": source,
                "tl": target,
                "q": query,
            },
            headers=SAFE_HEADERS,
        ) as response:
            if not response.ok:
                raise ValueError("Google doesn't fw us -_-")

            data = await response.json()
            sentences: List[TranslatedSentence] = data.get("sentences", [])
            if not sentences:
                raise ValueError("Google Translate returned no information")

            return cls(
                original=TranslationResult(
                    text="".join(sentence.get("orig", "") for sentence in sentences),
                    lang=data["src"],
                    language=LANGUAGES.get(data["src"], "Unknown"),
                    speech=data.get("dict", [{}])[0].get("pos"),
                ),
                translated=TranslationResult(
                    text="".join(sentence.get("trans", "") for sentence in sentences),
                    lang=target,
                    language=LANGUAGES.get(target, "Unknown"),
                    speech=data.get("ldict", [{}])[0].get("pos"),
                ),
            )

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> str:
        if argument in LANGUAGES:
            return argument

        for code, language in LANGUAGES.items():
            if argument.lower() == language.lower():
                return code

        raise ValueError(f"The language {argument} is not supported")