from datetime import datetime
from typing import List, Optional
from discord import Embed, Message
from yarl import URL
from aiohttp import ClientSession
from discord.ext.commands import Cog, BucketType, group, cooldown, max_concurrency
from bot.core import Juno, Context
from discord.utils import format_dt
from humanfriendly import format_timespan
from bot.shared.formatter import shorten
from bot.shared.paginator import Paginator
from cashews import cache
from config import config

IMAGE_URL = "https://image.tmdb.org/t/p/original"


@cache(ttl="3m")
async def fetch(endpoint: str, **params) -> Optional[dict]:
    """Fetch data from TMDB API."""

    async with ClientSession(
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {config.api.tmdb}",
        }
    ) as session:
        async with session.get(
            URL.build(
                scheme="https",
                host="api.themoviedb.org",
                path=f"/3/{endpoint}",
            ),
            params=params,
        ) as response:
            if not response.ok:
                return None

            return await response.json()


class TMDB(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @group(aliases=("tmdb",), invoke_without_command=True)
    @max_concurrency(1, BucketType.user)
    @cooldown(1, 5, BucketType.user)
    async def movie(self, ctx: Context, *, query: str) -> Message:
        """View details about a movie."""

        results = await fetch("search/movie", query=query)
        if not results or not results["results"]:
            return await ctx.warn(f"No movies were found for **{query}**")

        result = results["results"][0]
        data = await fetch(f"movie/{result['id']}")
        if not data:
            return await ctx.warn("An error occurred while fetching movie details")

        release_date = datetime.strptime(result["release_date"], "%Y-%m-%d")
        embed = Embed(
            title=result["title"],
            description=shorten(result["overview"], 950) + f"\n-# *{data['tagline']}*",
            url=f"https://www.themoviedb.org/movie/{result['id']}",
        )
        embed.set_thumbnail(url=f"{IMAGE_URL}{result['poster_path']}")
        embed.add_field(name="Release Date", value=format_dt(release_date, "d"))
        embed.add_field(name="Rating", value=f"{result['vote_average']:.2f}/10")
        embed.add_field(
            name="Runtime",
            value=format_timespan(data["runtime"] * 60, max_units=2),
        )
        embed.add_field(name="Budget", value=f"${data['budget']:,}")
        embed.add_field(name="Revenue", value=f"${data['revenue']:,}")
        embed.add_field(
            name="Genre" + ("s" if len(data["genres"]) > 1 else ""),
            value=", ".join(
                f"[{genre['name']}](https://www.themoviedb.org/genre/{genre['id']})"
                for genre in data["genres"]
            ),
        )

        return await ctx.send(embed=embed)

    @movie.command(name="cast", liases=("actors",))
    @max_concurrency(1, BucketType.user)
    @cooldown(1, 5, BucketType.user)
    async def movie_cast(self, ctx: Context, *, query: str) -> Message:
        """View the cast of a movie."""

        results = await fetch("search/movie", query=query)
        if not results or not results["results"]:
            return await ctx.warn(f"No movies were found for **{query}**")

        result = results["results"][0]
        data = await fetch(f"movie/{result['id']}/credits")
        if not data:
            return await ctx.warn("An error occurred while fetching movie credits")

        cast = [
            f"[{actor['name']}](https://www.themoviedb.org/person/{actor['id']}) as **{actor['character']}**"
            for actor in data["cast"]
        ]

        paginator = Paginator(ctx, cast, embed=Embed(title=f"{result['title']} Cast"))
        return await paginator.start()

    @movie.command(name="reviews")
    @max_concurrency(1, BucketType.user)
    @cooldown(1, 5, BucketType.user)
    async def movie_reviews(self, ctx: Context, *, query: str) -> Message:
        """View reviews of a movie."""

        results = await fetch("search/movie", query=query)
        if not results or not results["results"]:
            return await ctx.warn(f"No movies were found for **{query}**")

        result = results["results"][0]
        data = await fetch(f"movie/{result['id']}/reviews")
        if not data:
            return await ctx.warn("An error occurred while fetching movie reviews")

        embeds: List[Embed] = []
        for review in data["results"]:
            embed = Embed(
                description=shorten(review["content"], 1024),
                timestamp=datetime.strptime(
                    review["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ"
                ),
            )
            embed.set_author(
                name=f"{review['author']} ({review['author_details']['rating']}/10)",
                url=review["url"],
                icon_url=(
                    f"https://www.themoviedb.org/t/p/w64_and_h64_face{review['author_details']['avatar_path']}"
                    if review["author_details"]["avatar_path"]
                    else None
                ),
            )
            embed.set_footer(text="Written on")
            embeds.append(embed)

        paginator = Paginator(ctx, embeds)
        return await paginator.start()

    @movie.command(name="similar", aliases=("related", "recommendations", "recommend"))
    @max_concurrency(1, BucketType.user)
    @cooldown(1, 5, BucketType.user)
    async def movie_similar(self, ctx: Context, *, query: str) -> Message:
        """View similar movies to a movie."""

        results = await fetch("search/movie", query=query)
        if not results or not results["results"]:
            return await ctx.warn(f"No movies were found for **{query}**")

        result = results["results"][0]
        data = await fetch(f"movie/{result['id']}/recommendations")
        if not data:
            return await ctx.warn("An error occurred while fetching similar movies")

        embeds: List[Embed] = []
        for movie in data["results"]:
            release_date = datetime.strptime(movie["release_date"], "%Y-%m-%d")
            embed = Embed(
                title=movie["title"],
                description=shorten(movie["overview"], 1024),
                url=f"https://www.themoviedb.org/movie/{movie['id']}",
            )
            embed.set_image(url=f"{IMAGE_URL}{movie['backdrop_path']}")
            embed.add_field(name="Release Date", value=format_dt(release_date, "d"))
            embed.add_field(name="Rating", value=f"{movie['vote_average']:.2f}/10")
            embeds.append(embed)

        paginator = Paginator(ctx, embeds)
        return await paginator.start()

    @group(aliases=("tv", "show"), invoke_without_command=True)
    @max_concurrency(1, BucketType.user)
    @cooldown(1, 5, BucketType.user)
    async def tvshow(self, ctx: Context, *, query: str) -> Message:
        """View details about a TV show."""

        results = await fetch("search/tv", query=query)
        if not results or not results["results"]:
            return await ctx.warn(f"No TV shows were found for **{query}**")

        result = results["results"][0]
        data = await fetch(f"tv/{result['id']}")
        if not data:
            return await ctx.warn("An error occurred while fetching TV show details")

        first_air_date = datetime.strptime(result["first_air_date"], "%Y-%m-%d")
        embed = Embed(
            title=result["name"],
            description=shorten(result["overview"], 950) + f"\n-# *{data['tagline']}*",
            url=f"https://www.themoviedb.org/tv/{result['id']}",
        )
        embed.set_thumbnail(url=f"{IMAGE_URL}{result['poster_path']}")
        embed.add_field(
            name="First Air Date",
            value=format_dt(first_air_date, "d"),
        )
        embed.add_field(name="Rating", value=f"{result['vote_average']:.2f}/10")
        embed.add_field(
            name="Seasons & Episodes",
            value=f"{data['number_of_seasons']} seasons, {data['number_of_episodes']} episodes",
        )
        embed.add_field(
            name="Networks",
            value=", ".join(
                f"[{network['name']}](https://www.themoviedb.org/network/{network['id']})"
                for network in data["networks"]
            ),
        )
        embed.add_field(
            name="Creator" + ("s" if len(data["created_by"]) > 1 else ""),
            value=", ".join(
                f"[{creator['name']}](https://www.themoviedb.org/person/{creator['id']})"
                for creator in data["created_by"]
            ),
        )
        embed.add_field(
            name="Genres",
            value=", ".join(
                f"[{genre['name']}](https://www.themoviedb.org/genre/{genre['id']})"
                for genre in data["genres"]
            ),
        )
        embed.add_field(
            name="Seasons",
            value=">>> "
            + "\n".join(
                [
                    f"`{air_date:%m/%d/%Y}` "
                    f"[{season['name']}](https://www.themoviedb.org/tv/{result['id']}/season/{season['season_number']}) "
                    f"has {season['episode_count']} episodes"
                    for season in data["seasons"][:12]
                    if season["season_number"] != 0
                    and (air_date := datetime.strptime(season["air_date"], "%Y-%m-%d"))
                ]
            )
            + (
                f"\n-# ... [and {len(data['seasons']) - 12} more seasons]"
                f"(https://www.themoviedb.org/tv/{result['id']}/seasons)"
                if len(data["seasons"]) > 12
                else ""
            ),
            inline=False,
        )
        return await ctx.send(embed=embed)

    @tvshow.command(name="cast", aliases=("actors",))
    @max_concurrency(1, BucketType.user)
    @cooldown(1, 5, BucketType.user)
    async def tvshow_cast(self, ctx: Context, *, query: str) -> Message:
        """View the cast of a TV show."""

        results = await fetch("search/tv", query=query)
        if not results or not results["results"]:
            return await ctx.warn(f"No TV shows were found for **{query}**")

        result = results["results"][0]
        data = await fetch(f"tv/{result['id']}/credits")
        if not data:
            return await ctx.warn("An error occurred while fetching TV show credits")

        cast = [
            f"[{actor['name']}](https://www.themoviedb.org/person/{actor['id']}) as **{actor['character']}**"
            for actor in data["cast"]
        ]

        paginator = Paginator(ctx, cast, embed=Embed(title=f"{result['name']} Cast"))
        return await paginator.start()

    @tvshow.command(name="reviews")
    @max_concurrency(1, BucketType.user)
    @cooldown(1, 5, BucketType.user)
    async def tvshow_reviews(self, ctx: Context, *, query: str) -> Message:
        """View reviews of a TV show."""

        results = await fetch("search/tv", query=query)
        if not results or not results["results"]:
            return await ctx.warn(f"No TV shows were found for **{query}**")

        result = results["results"][0]
        data = await fetch(f"tv/{result['id']}/reviews")
        if not data:
            return await ctx.warn("An error occurred while fetching TV show reviews")

        embeds: List[Embed] = []
        for review in data["results"]:
            embed = Embed(
                description=shorten(review["content"], 1024),
                timestamp=datetime.strptime(
                    review["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ"
                ),
            )
            embed.set_author(
                name=f"{review['author']} ({review['author_details']['rating']}/10)",
                url=review["url"],
                icon_url=(
                    f"https://www.themoviedb.org/t/p/w64_and_h64_face{review['author_details']['avatar_path']}"
                    if review["author_details"]["avatar_path"]
                    else None
                ),
            )
            embed.set_footer(text="Written on")
            embeds.append(embed)

        paginator = Paginator(ctx, embeds)
        return await paginator.start()

    @tvshow.command(name="similar", aliases=("related", "recommendations", "recommend"))
    @max_concurrency(1, BucketType.user)
    @cooldown(1, 5, BucketType.user)
    async def tvshow_similar(self, ctx: Context, *, query: str) -> Message:
        """View similar TV shows to a TV show."""

        results = await fetch("search/tv", query=query)
        if not results or not results["results"]:
            return await ctx.warn(f"No TV shows were found for **{query}**")

        result = results["results"][0]
        data = await fetch(f"tv/{result['id']}/recommendations")
        if not data:
            return await ctx.warn("An error occurred while fetching similar TV shows")

        embeds: List[Embed] = []
        for show in data["results"]:
            if not show["first_air_date"]:
                continue

            first_aired = datetime.strptime(show["first_air_date"], "%Y-%m-%d")
            embed = Embed(
                title=show["name"],
                description=shorten(show["overview"], 1024),
                url=f"https://www.themoviedb.org/tv/{show['id']}",
            )
            embed.set_image(url=f"{IMAGE_URL}{show['backdrop_path']}")
            embed.add_field(name="First Air Date", value=format_dt(first_aired, "d"))
            embed.add_field(name="Rating", value=f"{show['vote_average']:.2f}/10 ")
            embeds.append(embed)

        paginator = Paginator(ctx, embeds)
        return await paginator.start()
