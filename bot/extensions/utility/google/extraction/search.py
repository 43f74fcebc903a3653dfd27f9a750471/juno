from __future__ import annotations

import re
from logging import getLogger
from typing import List, Optional, cast, no_type_check

from aiohttp import ClientSession
from bs4 import BeautifulSoup, NavigableString, Tag
from discord.ext.commands import CommandError
from html2text import html2text as h2t
from pydantic import BaseModel, Field
from yarl import URL

from bot.shared.formatter import shorten
from config import config

logger = getLogger("bot.google")


def get_text(tag: Optional[Tag | NavigableString]) -> Optional[str]:
    if not tag:
        return None

    return h2t(str(tag)).strip() if tag.text else None


SAFE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/119.0.0.0 Safari/537.36"
    )
}


class SiteLink(BaseModel):
    url: str
    title: str
    snippet: Optional[str] = Field("...")


class Tweet(BaseModel):
    url: str
    text: str
    footer: Optional[str] = Field(None)


class Suggestion(BaseModel):
    text: str
    url: str


class RichCard(BaseModel):
    title: str

    @property
    def formatted(self) -> str:
        return f"**Rich Card Information:** `{self.title}`"

    @classmethod
    def from_soup(cls, soup: BeautifulSoup) -> Optional[RichCard]:
        raise NotImplementedError


class UnitConversion(RichCard):
    option: str
    formula: str
    unit: str
    value: str
    output_unit: str
    output_value: str

    @property
    def formatted(self) -> str:
        return "\n> ".join(
            [
                f"**Rich Card Information:** `{self.title}`",
                f"**{self.unit}:** `{self.value}`",
                f"**{self.output_unit}:** `{self.output_value}`",
                f"**Formula:** `{self.formula}`",
            ]
        )

    @classmethod
    def from_soup(cls, soup: BeautifulSoup) -> Optional[UnitConversion]:
        card = cast(Optional[Tag], soup.find("div", class_="vk_c"))
        if not card:
            return None

        option = card.find("option", selected=True)
        formula = card.find("div", class_="bjhkR")
        if not option or not formula:
            return None

        conversion = card.find_all("div", class_="rpnBye")
        if not len(conversion) == 2:
            return None

        unit, value = (
            conversion[0].find("option", selected=True).text,
            conversion[0].input.get("value"),
        )
        output_unit, output_value = (
            conversion[1].find("option", selected=True).text,
            conversion[1].input.get("value"),
        )
        return cls(
            title="Unit Conversion",
            option=option.text.title(),
            formula=formula.text.lower().capitalize(),
            unit=unit,
            value=value,
            output_unit=output_unit,
            output_value=output_value,
        )


class Calculator(RichCard):
    expression: str
    result: str

    @property
    def formatted(self) -> str:
        return "\n> ".join(
            [
                f"**Rich Card Information:** `{self.title}`",
                f"`{self.expression}`: `{self.result}`",
            ]
        )

    @classmethod
    def from_soup(cls, soup: BeautifulSoup) -> Optional[Calculator]:
        card = cast(Optional[Tag], soup.find("div", class_="TIGsTb"))
        if not card:
            return None

        expression = card.find("span", class_="vUGUtc")
        result = card.find("span", class_="qv3Wpe")
        if not expression or not result:
            return None

        return cls(
            title="Calculator",
            expression=expression.text.strip("\n").strip()[:-2],
            result=result.text.strip(),
        )


class Map(RichCard):
    url: str
    location: str

    @property
    def formatted(self) -> str:
        return "\n> ".join(
            [
                f"**Rich Card Information:** `{self.title}`",
                f"**Location:** `{self.location}`",
                f"[**View on Google Maps**]({self.url})",
            ]
        )

    @classmethod
    def from_soup(cls, soup: BeautifulSoup) -> Optional[Map]:
        card = cast(Optional[Tag], soup.find("div", class_="vk_c"))
        if not card:
            return None

        url = cast(Optional[Tag], card.find("a"))
        location = card.find("div", class_="aiAXrc")
        if not location or not url:
            return None

        return cls(
            title="Map",
            url=f"https://www.google.com{url.attrs['href']}",
            location=get_text(location) or "Unknown",
        )


class Translation(RichCard):
    source: str
    destination: str
    source_text: str
    destination_text: str

    @property
    def formatted(self) -> str:
        return "\n> ".join(
            [
                f"**Rich Card Information:** `{self.title}`",
                f"**{self.source}:** `{self.source_text}`",
                f"**{self.destination}:** `{self.destination_text}`",
            ]
        )

    @classmethod
    @no_type_check
    def from_soup(cls, soup: BeautifulSoup) -> Optional[Translation]:
        card = cast(Optional[Tag], soup.find("div", class_="tw-src-ltr"))
        if not card:
            return None

        languages = soup.find("div", class_="pcCUmf")
        source = (
            languages.find("span", class_="source-language")
            .text.split("-", 1)[0]
            .strip()
        )
        source_text = card.find("div", id="KnM9nf").find("pre").text.replace("\n", " ")
        destination = languages.find("span", class_="target-language").text
        destination_text = (
            card.find("div", id="kAz1tf").find("pre").text.replace("\n", " ")
        )
        return cls(
            title="Translation",
            source=source,
            destination=destination,
            source_text=source_text,
            destination_text=destination_text,
        )


class Definition(RichCard):
    word: str
    pronunciation: str
    definitions: List[str]

    @property
    def formatted(self) -> str:
        return "\n> ".join(
            [
                f"**Rich Card Information:** `{self.title}`",
                f"**Word:** `{self.word}`",
                f"**Pronunciation:** `{self.pronunciation}`",
                "**Definitions:**",
                "\n".join([definition for definition in self.definitions]),
            ]
        )

    @classmethod
    @no_type_check
    def from_soup(cls, soup: BeautifulSoup) -> Optional[Definition]:
        card = cast(Optional[Tag], soup.find("div", class_=re.compile("KIy09e")))
        if not card:
            return None

        word = card.find("div", class_=re.compile("c8d6zd"))
        if not word:
            return None

        if sup := word.find("sup"):
            sup.decompose()

        pronounciate = card.find("div", class_="qexShd")
        definitions = []
        if definition := card.find("div", class_="LTKOO sY7ric"):
            for text in definition.find_all("span"):
                text = h2t(str(text))
                if text.count("\n") < 5:
                    definitions.append(text.replace("\n", ""))

        word = word.text.replace("·", "")
        pronounciate = pronounciate.text
        return cls(
            title="Definition",
            word=word,
            pronunciation=pronounciate,
            definitions=definitions,
        )


class Result(BaseModel):
    url: str
    cite: str
    title: str
    description: Optional[str] = Field("...")
    suggestions: List[Suggestion] = Field([])
    highlights: List[str] = Field([])
    extended_links: List[SiteLink] = Field([])
    tweets: List[Tweet] = Field([])

    @classmethod
    def get_extended_links(cls, tag: Tag) -> List[SiteLink]:
        links: List[SiteLink] = []

        link: Tag
        for link in tag.find_all("div", class_="usJj9c"):
            if not (link.a and link.div):
                continue

            url = cast(str, link.a["href"])
            title = link.a.text
            snippet = link.div.text[len(link.a.text) :]

            links.append(SiteLink(url=url, title=title, snippet=snippet))

        return links

    @classmethod
    def get_tweets(cls, tag: Tag) -> List[Tweet]:
        tweets: List[Tweet] = []

        tweet: Tag
        for tweet in tag.select(".fy7gGf"):
            if not tweet.a:
                continue

            elif any(tweet.a["href"] == t.url for t in tweets):
                continue

            url = cast(str, tweet.a["href"])
            heading = tweet.find("div", role="heading")
            if not heading or not heading.text.strip():
                continue

            footer = tweet.select_one(".ZYHQ7e")
            tweets.append(
                Tweet(url=url, text=heading.text.strip(), footer=get_text(footer))
            )

        return tweets

    @classmethod
    def from_soup(cls, soup: BeautifulSoup) -> List[Result]:
        results: List[Result] = []

        tag: Tag
        for tag in soup.select("div.g > div"):
            cite = tag.select_one("div.yuRUbf")
            if not (cite and cite.a and cite.a.cite and cite.h3):
                continue

            url = cast(str, cite.a["href"])
            if not url.startswith("http"):
                continue

            title = cite.h3.text
            cite = cite.a.cite.text.split("//", 1)[-1]
            description = tag.select_one("div.VwiC3b")
            if not description:
                continue

            results.append(
                cls(
                    url=url,
                    cite=cite,
                    title=title,
                    description=get_text(description),
                    suggestions=[],
                    highlights=[highlight.text for highlight in tag.find_all("em")],
                    extended_links=cls.get_extended_links(tag),
                    tweets=cls.get_tweets(tag),
                )
            )

        return results


class KnowledgeItem(BaseModel):
    name: str
    value: str
    url: Optional[str] = Field(None)

    @property
    def hyperlink(self) -> str:
        return (
            f"[`{shorten(self.value, 46)}`]({self.url})"
            if self.url
            else f"`{shorten(self.value, 46)}`"
        )

    @classmethod
    def from_soup(cls, soup: BeautifulSoup) -> List[KnowledgeItem]:
        items: List[KnowledgeItem] = []

        tag: Tag
        for tag in soup.find_all("div", class_="wDYxhc"):
            name = tag.find("span", class_="w8qArf")
            value = tag.select_one(".kno-fv")
            url = (
                f"https://www.google.com{a['href']}"
                if (a := tag.select_one("a"))
                else None
            )

            if name and value:
                items.append(
                    KnowledgeItem(
                        name=name.text[:-2],
                        value=value.text,
                        url=url,
                    )
                )

        return items


class KnowledgeSource(BaseModel):
    url: str
    name: str


class KnowledgePanel(BaseModel):
    description: str = Field("...")
    source: Optional[KnowledgeSource] = Field(...)
    items: List[KnowledgeItem] = Field([])

    @classmethod
    @no_type_check
    def from_soup(cls, soup: BeautifulSoup) -> Optional[KnowledgePanel]:
        panel = soup.find("div", class_="kno-rdesc")
        if not isinstance(panel, Tag):
            return None

        return cls(
            description=get_text(panel.span) or "...",
            source=(
                KnowledgeSource(
                    url=panel.a["href"],
                    name=panel.a.text,
                )
                if panel.a
                else None
            ),
            items=KnowledgeItem.from_soup(soup),
        )


class GoogleSearch(BaseModel):
    header: Optional[str] = Field(None)
    description: Optional[str] = Field(None)
    panel: Optional[KnowledgePanel] = Field(None)
    rich_card: Optional[
        RichCard | UnitConversion | Calculator | Map | Translation | Definition
    ] = Field(None)
    results: List[Result] = Field([])
    suggestion: Optional[Suggestion] = Field(None)
    total_results: int = Field(0)

    @classmethod
    async def search(
        cls,
        query: str,
        safe: bool = True,
        locale: str = "en",
    ) -> GoogleSearch:
        async with ClientSession(headers=SAFE_HEADERS) as session:
            async with session.get(
                URL.build(
                    scheme="https",
                    host="notsobot.com",
                    path="/api/search/google",
                ),
                params={
                    "query": query[:2000],
                    "safe": str(safe).lower(),
                    "locale": locale,
                },
                headers={"Authorization": f"Bot {config.api.notsobot}"},
            ) as response:
                data = await response.json()
                if not response.ok:
                    if data["message"] == "Invalid Form Body":
                        raise CommandError("The provided locale isn't valid")

                    logger.warning(
                        f"Google search failed with status code {response.status} {response.reason}"
                    )
                    raise CommandError("Google search failed to return results")

                soup = BeautifulSoup(data["raw"], "html.parser")
                header = soup.find("div", class_="PZPZlf ssJ7i B5dxMb")
                description = soup.select_one(".iAIpCb")
                rich_card = None
                for card in (
                    UnitConversion,
                    Calculator,
                    Map,
                    Translation,
                    Definition,
                ):
                    if rich_card := card.from_soup(soup):
                        break

                return cls(
                    header=get_text(header),
                    description=get_text(description),
                    panel=KnowledgePanel.from_soup(soup),
                    rich_card=rich_card,
                    results=Result.from_soup(soup),
                    suggestion=(
                        Suggestion(**data["suggestion"]) if data["suggestion"] else None
                    ),
                    total_results=data["total_result_count"],
                )
