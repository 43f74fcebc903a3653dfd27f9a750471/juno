from datetime import datetime
from typing import List, Optional
from discord import Embed, Message
from discord.ext.commands import Cog, group, command
from bot.core import Juno, Context
from yarl import URL

from bot.shared.paginator import Paginator

ESPN_URL = URL.build(
    scheme="https",
    host="site.api.espn.com",
    path="/apis/site/v2/sports",
)
RSBLABS_URL = URL.build(
    scheme="https",
    host="api.rsblabs.com",
)


class Sports(Cog):
    def __init__(self, bot: Juno):
        self.bot = bot

    async def sport(self, ctx: Context, sport: str) -> Message:
        url = ESPN_URL / sport / "scoreboard"
        response = await self.bot.session.get(url)
        data = await response.json()
        if not data.get("events"):
            return await ctx.warn("There isn't any ongoing event for this sport.")

        embeds: List[Embed] = []
        for event in data["events"]:
            competitors = event["competitions"][0]["competitors"]
            teams = list(map(lambda t: t["team"], competitors))
            embed = Embed(
                url=event["links"][0]["href"],
                title=event["name"],
                timestamp=datetime.fromisoformat(event["date"].replace("Z", "+00:00")),
            )
            embed.set_author(name=teams[0]["displayName"], icon_url=teams[0]["logo"])
            embed.set_thumbnail(url=teams[1]["logo"])

            embed.add_field(name="Status", value=event["status"]["type"]["shortDetail"])
            embed.add_field(
                name="Teams",
                value=f"{teams[0]['abbreviation']} vs {teams[1]['abbreviation']}",
            )
            embed.add_field(
                name="Score",
                value=f"{competitors[0]['score']} - {competitors[1]['score']}",
            )
            embeds.append(embed)

        paginator = Paginator(ctx, embeds)
        return await paginator.start()

    async def sport_odds(
        self,
        ctx: Context,
        sport: str,
        team: Optional[str] = None,
    ) -> Message:
        await ctx.typing()
        sport, league = sport.split("/")
        response = await self.bot.session.get(RSBLABS_URL)
        if not response.ok:
            return await ctx.warn(
                "The API didn't provide the necessary data, try again later"
            )

        data = await response.json()
        events = list(
            filter(
                lambda s: s["sport_slug"].replace("amer-", "") == sport
                and s["league_slug"] == league
                and all(
                    [
                        s["odds"],
                        s["money_data"],
                        s["money_data"].get("total_over_public"),
                    ]
                ),
                data,
            )
        )
        embeds: List[Embed] = []
        for event in events:
            if team and not any(
                team.lower() in name.lower()
                for name in (
                    event["home_team"],
                    event["away_team"],
                    event["home_team_meta"]["team_abbr"],
                    event["away_team_meta"]["team_abbr"],
                )
            ):
                continue

            winner = (
                "home"
                if event["odds"][0]["home_moneyline_us"]
                < event["odds"][0]["away_moneyline_us"]
                else "away"
            )
            embed = Embed(
                url=(
                    "https://oddscrowd.com/odds-comparison/"
                    f"{sport}/leagues/{league}/bet-types/moneyline-fullgame"
                ),
                title=f"{event['home_team']} vs {event['away_team']}",
                timestamp=datetime.fromisoformat(event["start_time"]),
            )
            embed.add_field(
                name=f"Public -> {winner.capitalize()}",
                value=(
                    event[f"{winner}_team_meta"]["team_abbr"]
                    + f" (`{event['odds'][0][f'{winner}_spread']}`)"
                    + f" [*`{event['money_data'].get(f'spread_{winner}_public', 'UNKNOWN')}%`*]"
                    + "(https://oddscrowd.link/)"
                    + f" `o{event['odds'][0]['total']}` [*`{event['money_data']['total_over_public']}%`*]"
                    + "(https://oddscrowd.link/)"
                ),
            )
            embed.add_field(
                name="Recent Win/Loss",
                value=(
                    f"{event['home_team_meta']['team_abbr']} ("
                    + ", ".join(
                        [
                            f"[`{status}`](https://oddscrowd.link/)"
                            for status in event["home_team_recent_winloss"]
                        ]
                    )
                    + ")\n"
                    + f"{event['away_team_meta']['team_abbr']} ("
                    + ", ".join(
                        [
                            f"[`{status}`](https://oddscrowd.link/)"
                            for status in event["away_team_recent_winloss"]
                        ]
                    ) + ")"
                ),
                inline=False,
            )
            embeds.append(embed)
            
        if not embeds:
            return await ctx.warn(
                f"There isn't an ongoing event for `{league.upper()} {sport.title()}`"
            )
        paginator = Paginator(ctx, embeds)
        return await paginator.start()

    @group(name="basketball", aliases=("nba", "bb"), invoke_without_command=True)
    async def basketball(self, ctx: Context) -> Message:
        """National Basketball Association scores."""

        return await self.sport(ctx, "basketball/nba")

    @basketball.command(name="women", aliases=("wnba", "wbb"))
    async def basketball_women(self, ctx: Context) -> Message:
        """Women's National Basketball Association scores."""

        return await self.sport(ctx, "basketball/wnba")

    @basketball.group(name="college", aliases=("cb", "cbb"))
    async def basketball_college(self, ctx: Context) -> Message:
        """College Basketball scores."""

        return await self.sport(ctx, "basketball/mens-college-basketball")

    @basketball_college.command(name="women", aliases=("cbbw", "cbw"))
    async def basketball_college_women(self, ctx: Context) -> Message:
        """Women's College Basketball scores."""

        return await self.sport(ctx, "basketball/womens-college-basketball")

    @basketball.command(
        name="odds",
        aliases=(
            "public",
            "odd",
        ),
    )
    async def basketball_odds(
        self,
        ctx: Context,
        *,
        team: Optional[str] = None,
    ) -> Message:
        """View public odds for NBA games."""

        return await self.sport_odds(ctx, "basketball/nba", team)

    @command(name="wnba", aliases=("wbb",), hidden=True)
    async def wnba_hidden(self, ctx: Context) -> Message:
        """Women's National Basketball Association scores."""

        return await self.sport(ctx, "basketball/wnba")

    @group(
        name="football",
        aliases=("nfl", "fb"),
        invoke_without_command=True,
    )
    async def football(self, ctx: Context) -> Message:
        """National Football League scores."""

        return await self.sport(ctx, "football/nfl")

    @football.command(
        name="odds",
        aliases=(
            "public",
            "odd",
        ),
    )
    async def football_odds(
        self,
        ctx: Context,
        *,
        team: Optional[str] = None,
    ) -> Message:
        """View public odds for NFL games."""

        return await self.sport_odds(ctx, "football/nfl", team)

    @football.group(name="college", aliases=("cfb", "cf"), invoke_without_command=True)
    async def football_college(self, ctx: Context) -> Message:
        """College Football scores."""

        return await self.sport(ctx, "football/college-football")

    @football_college.command(name="odds", aliases=("public", "odd"))
    async def football_college_odds(
        self,
        ctx: Context,
        team: Optional[str] = None,
    ) -> Message:
        """View public odds for College Football games."""

        return await self.sport_odds(ctx, "football/ncaaf", team)

    @command(name="collegefootball", aliases=("cfb",), hidden=True)
    async def college_football_hidden(self, ctx: Context) -> Message:
        """College Football scores."""

        return await self.sport(ctx, "football/college-football")

    @command(name="soccer", aliases=("futbol",))
    async def soccer(self, ctx: Context) -> Message:
        """Soccer scores."""

        return await self.sport(ctx, "soccer/eng.1")

    @command(name="hockey", aliases=("nhl", "hock"))
    async def hockey(self, ctx: Context) -> Message:
        """National Hockey League scores."""

        return await self.sport(ctx, "hockey/nhl")

    @command(name="baseball", aliases=("mlb", "baseb"))
    async def baseball(self, ctx: Context) -> Message:
        """Major League Baseball scores."""

        return await self.sport(ctx, "baseball/mlb")
