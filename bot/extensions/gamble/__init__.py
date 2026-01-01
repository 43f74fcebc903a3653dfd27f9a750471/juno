from __future__ import annotations

import asyncio
from random import randint
import random
from typing import Annotated, List, Literal, cast

from discord import Color, Embed, Member, Message
from discord.ext.commands import (
    Range,
    Author,
    BucketType,
    max_concurrency,
    command,
)
from discord.utils import format_dt, utcnow, get
from datetime import timedelta

from bot.core import Juno
from bot.shared.formatter import plural
from bot.shared.paginator import Paginator
from .schema import Bet, EconomyUser, Rakeback
from .meta import Cog, Context, Amount


class Gamble(Cog):
    @command(aliases=("bal", "money", "wallet", "bank"))
    async def balance(self, ctx: Context, *, member: Member = Author) -> Message:
        """Check your balance or someone else's."""

        profile = (
            ctx.profile
            if member == ctx.author
            else await EconomyUser.fetch(self.bot, member.id)
        )
        query = "SELECT * FROM economy.bet WHERE user_id = $1 ORDER BY created_at DESC"
        bets = cast(List[Bet], await self.bot.db.fetch(query, member.id))

        embed = Embed()
        embed.set_author(
            name=f"{member.display_name}'s balance",
            icon_url=member.display_avatar,
        )
        embed.description = f"Balance of `${profile.balance:,.2f}` out of `${(profile.wagered):,.2f}` wagered"
        if bets:
            embed.add_field(
                name="Recent Bets",
                value="\n".join(
                    [
                        f"{format_dt(bet['created_at'], 'R')} **{bet['game']}** for `${bet['amount']:,.2f}` resulting in `${bet['payout']:,.2f}`"
                        for bet in bets[:5]
                    ]
                )
                + (
                    f"\n> -# ... and {len(bets) - 5} more bets since {format_dt(bets[-1]['created_at'], 'D')}"
                    if len(bets) > 5
                    else ""
                ),
                inline=False,
            )

        return await ctx.send(embed=embed)

    @command()
    @max_concurrency(1, BucketType.channel)
    async def rain(self, ctx: Context, amount: Annotated[float, Amount]) -> Message:
        """Let people catch some scraps."""

        await ctx.prompt(
            f"Are you sure you want to rain `${amount:,.2f}`?",
            "You won't be able to take place in the rain or cancel it",
        )
        ctx.profile.balance -= amount
        await ctx.profile.save(self.bot)

        embed = Embed(
            title=f"Rain of ${amount:,.2f}!",
            description="\n".join(
                [
                    f"{ctx.author.mention} has started a rain",
                    "React with `💸` to take place in this rain",
                ]
            ),
        )
        embed.set_footer(text="This rain will end in 30 seconds")
        message = await ctx.channel.send(embed=embed)
        await message.add_reaction("💸")

        await asyncio.sleep(30)
        message = await ctx.channel.fetch_message(message.id)
        reaction = get(message.reactions, emoji="💸")
        if not reaction:
            return await message.reply(
                "The rain has ended without any takers",
                reference=message,
            )

        users = [
            user
            async for user in reaction.users()
            if not user.bot and user != ctx.author
        ]
        if not users:
            return await ctx.warn(
                "The rain has ended without any takers",
                reference=message,
            )

        amount_per_user = amount / len(users)
        for user in users:
            profile = await EconomyUser.fetch(self.bot, user.id)
            profile.balance += amount_per_user
            await profile.save(self.bot)

        return await ctx.approve(
            f"The rain has distributed `${amount_per_user:,.2f}` to {plural(len(users), '`'):user} out of `${amount:,.2f}`",
            content=", ".join(user.mention for user in users),
            reference=message,
        )

    @command(aliases=("lb", "top"))
    async def leaderboard(self, ctx: Context) -> Message:
        """Check the leaderboard of the richest users."""

        query = """
        SELECT user_id, balance
        FROM economy.user
        WHERE balance > 0
        ORDER BY balance DESC
        LIMIT 10
        """
        records = [
            f"**{user}** has `${record['balance']:,.2f}`"
            for record in await self.bot.db.fetch(query)
            if (user := self.bot.get_user(record["user_id"]))
        ]
        query = "SELECT SUM(wagered) FROM economy.user"
        total_wagered = await self.bot.db.fetchval(query)

        embed = Embed(title="Economy Leaderboard")
        embed.set_footer(text=f"Cumulative wagered of ${total_wagered:,.2f}")
        paginator = Paginator(ctx, records, embed)
        return await paginator.start()

    @command(aliases=("chest", "claim"))
    @max_concurrency(1, BucketType.user)
    async def daily(self, ctx: Context) -> Message:
        """Claim your daily chest for free money."""

        if ctx.profile.last_daily and utcnow() - ctx.profile.last_daily < timedelta(
            days=1
        ):
            return await ctx.warn(
                f"You've already claimed your daily chest, try again {format_dt(ctx.profile.last_daily + timedelta(days=1), 'R')}"
            )

        amount = random.randint(100, 300)
        ctx.profile.balance += amount
        ctx.profile.last_daily = utcnow()
        await ctx.profile.save(self.bot)

        return await ctx.approve(f"Your daily chest has been claimed for `${amount}`")

    @command(aliases=("job",))
    async def work(self, ctx: Context) -> Message:
        """Work for some money."""

        if ctx.profile.last_worked and utcnow() - ctx.profile.last_worked < timedelta(
            hours=1
        ):
            return await ctx.warn(
                f"You can work again {format_dt(ctx.profile.last_worked + timedelta(hours=1), 'R')}"
            )

        amount = random.randint(60, 200)
        ctx.profile.balance += amount
        ctx.profile.last_worked = utcnow()
        await ctx.profile.save(self.bot)
        return await ctx.approve(f"You've worked and earned `${amount}`")

    @command(aliases=("rake", "rb"))
    async def rakeback(self, ctx: Context) -> Message:
        """Claim your rakeback for a percentage of your bets."""

        query = "SELECT * FROM economy.rakeback WHERE user_id = $1"
        record = cast(Rakeback, await self.bot.db.fetchrow(query, ctx.author.id))
        if not record or not record["amount"]:
            return await ctx.warn("You don't have any rakeback to claim")

        elif record["last_claimed"] and utcnow() - record["last_claimed"] < timedelta(
            minutes=2
        ):
            return await ctx.warn(
                f"You can claim rakeback again {format_dt(record['last_claimed'] + timedelta(minutes=5), 'R')}"
            )

        query = """
        UPDATE economy.rakeback
        SET amount = 0, last_claimed = NOW()
        WHERE user_id = $1
        """
        ctx.profile.balance += record["amount"]
        await asyncio.gather(
            ctx.profile.save(self.bot),
            self.bot.db.execute(query, ctx.author.id),
        )
        return await ctx.approve(
            f"Your rakeback has been claimed for `${record['amount']:,.2f}`"
        )

    @command(aliases=("transfer", "send", "pay"))
    async def tip(
        self,
        ctx: Context,
        member: Member,
        amount: Annotated[float, Amount],
    ) -> Message:
        """Tip another user some money."""

        if member == ctx.author:
            return await ctx.warn("You can't tip yourself")

        profile = await EconomyUser.fetch(self.bot, member.id)
        ctx.profile.balance -= amount
        profile.balance += amount
        await asyncio.gather(
            ctx.profile.save(self.bot),
            profile.save(self.bot),
        )
        return await ctx.approve(f"You've tipped {member.mention} `${amount:,.2f}`")

    @command(aliases=("flip", "cf"))
    async def coinflip(
        self,
        ctx: Context,
        bet: Annotated[float, Amount],
        side: Literal["heads", "tails"],
    ) -> Message:
        """Flip a coin and double your bet."""

        embed = Embed(description=f"Flipping the coin for `${bet:,.2f}`.. :coin:")
        message = await ctx.reply(embed=embed, mention_author=True)

        landing = random.choice(["heads", "tails"])
        if landing == side:
            ctx.profile.balance += bet
        else:
            ctx.profile.balance -= bet

        ctx.profile.wagered += bet
        await asyncio.gather(
            ctx.profile.save(self.bot),
            self.save_bet(ctx, bet, 2, bet if landing == side else 0),
        )

        await asyncio.sleep(1.3)
        embed.color = Color.green() if landing == side else Color.red()
        embed.description = f"The coin landed on **{landing}** you {'won' if landing == side else 'lost'}! You now have `${ctx.profile.balance:,.2f}` in your balance"
        return await message.edit(embed=embed)

    @command()
    async def roulette(
        self,
        ctx: Context,
        bet: Annotated[float, Amount],
        color: Literal["red", "black", "green"],
    ) -> Message:
        """Bet on a color in roulette.

        Red: 1-10, 19-28 (2x)
        Black: 11-18, 29-36 (2x)
        Green: 0 (35x)"""

        embed = Embed(description=f"Spinning the roulette for `${bet:,.2f}`..")
        message = await ctx.reply(embed=embed, mention_author=True)

        colors = {
            "red": [(1, 10), (19, 28)],
            "black": [(11, 18), (29, 36)],
            "green": [(0, 0)],
        }
        landing_color = None
        landing = randint(0, 36)

        for color_name, ranges in colors.items():
            for start, end in ranges:
                if start <= landing <= end:
                    landing_color = color_name
                    break

            if landing_color:
                break

        if landing_color == "green":
            payout = bet * 35 if landing_color == color else 0
        else:
            payout = bet * 2 if landing_color == color else 0

        ctx.profile.wagered += bet
        if landing_color == color:
            ctx.profile.balance += payout
        else:
            ctx.profile.balance -= bet

        await asyncio.gather(
            ctx.profile.save(self.bot),
            self.save_bet(ctx, bet, 2, payout),
        )

        await asyncio.sleep(1.3)
        embed.color = Color.green() if landing_color == color else Color.red()
        embed.description = f"The ball landed on **{landing_color}** `{landing}` you {'won' if landing_color == color else 'lost'}! You now have `${ctx.profile.balance:,.2f}` in your balance"
        return await message.edit(embed=embed)

    @command()
    async def slots(self, ctx: Context, bet: Annotated[float, Amount]) -> Message:
        """Spin the slots for a chance to win big."""

        emojis = (
            "🍎", "🍊", "🍐", "🍋", "🍉", "🍇", "🍓", "🍒", "🍌", "🍍", "🥥", "🍑", "🥭"
        )
        a, b, c, d = random.choices(emojis, k=4)
        
        payout = 0
        multiplier = 0

        # Check for 4-of-a-kind
        if a == b == c == d:
            multiplier = 100
        # Check for 3-of-a-kind in any combination
        elif (a == b == c) or (a == c == d) or (a == b == d) or (b == c == d):
            multiplier = 10
        # Check for 2 pairs (any matching pair across the set of 4)
        elif (a == b and c == d) or (a == c and b == d) or (a == d and b == c):
            multiplier = 10
        # Check for 1 pair
        elif a == b or a == c or a == d or b == c or b == d or c == d:
            multiplier = 1
        else:
            multiplier = -1  # Lost

        if multiplier > 0:
            payout = bet * multiplier
        elif multiplier == -1:
            payout = -bet

        ctx.profile.balance += payout
        ctx.profile.wagered += bet
        await asyncio.gather(
            ctx.profile.save(self.bot),
            self.save_bet(ctx, bet, 10, payout),
        )

        embed = Embed(
            description=(
                f"{' '.join(f'`{emoji}`' for emoji in [a, b, c, d])} (`{multiplier}x`)\n"
                f"You {'won' if multiplier > 0 else 'lost'} `${(payout):,.2f}`\n"
                f"You now have `${ctx.profile.balance:,.2f}` in your balance"
            )
        )
        embed.color = Color.green() if multiplier > 0 else Color.red()
        return await ctx.send(embed=embed, mention_author=bool(payout))

    @command()
    async def limbo(
        self,
        ctx: Context,
        bet: Annotated[float, Amount],
        multiplier: Range[float, 1.01, 1000000.00] = 2,
    ) -> Message:
        """Land on or above your target to win."""

        value = random.random()
        result = round((((1 / value) * 0.90) if value < 0.25 else (1 / value)), 3)

        outcome = "won" if result >= multiplier else "lost"
        payout = bet * (multiplier - 1) if result >= multiplier else 0
        if result >= multiplier:
            ctx.profile.balance += payout
        else:
            ctx.profile.balance -= bet

        ctx.profile.wagered += bet
        await asyncio.gather(
            ctx.profile.save(self.bot),
            self.save_bet(ctx, bet, multiplier, payout),
        )
        return await ctx.respond(
            f"Landed on `{result}x` of *`{multiplier}x`* and {outcome} `${(payout or bet):,.2f}`",
            f"You now have `${ctx.profile.balance:,.2f}` in your balance and have wagered `${ctx.profile.wagered:,.2f}`",
            color=Color.green() if payout else Color.red(),
            mention_author=bool(payout),
        )

    @command(aliases=("gamble", "bet"))
    async def dice(
        self,
        ctx: Context,
        bet: Annotated[float, Amount],
        chance: Range[float, 1, 98] = 49,
    ) -> Message:
        """Roll a dice for a multiplier of your bet."""

        multiplier = round(99 / chance, 4)
        roll = randint(1, 100)

        embed = Embed()

        original_balance = ctx.profile.balance
        ctx.profile.wagered += bet
        payout = 0
        streak = 0
        if roll <= chance:
            payout = bet * (multiplier - 1)
            ctx.profile.balance += payout
            embed.color = Color.green()
            embed.title = f"You won {payout:,.2f}! {round(multiplier, 2)}x"
        else:
            embed.color = Color.red()
            embed.title = f"You lost {bet:,.2f}! {round(multiplier, 2)}x"
            ctx.profile.balance -= bet
            query = """
            SELECT payout 
            FROM economy.bet 
            WHERE user_id = $1 AND game = $2
            ORDER BY created_at DESC 
            LIMIT 20
            """
            bets = await self.bot.db.fetch(
                query,
                ctx.author.id,
                ctx.command.qualified_name.title(),
            )
            for _bet in bets:
                if _bet["payout"] == 0:
                    streak += 1
                else:
                    break

        await asyncio.gather(
            ctx.profile.save(self.bot),
            self.save_bet(ctx, bet, multiplier, payout),
        )
        embed.description = "\n".join(
            [
                f"🎲 Rolled a *`{roll}`* out of `100` from `${original_balance:,.2f}`",
                f"💸 You now have `${ctx.profile.balance:,.2f}` in your balance",
                f"{'📈' if ctx.profile.balance > 10 else '📉'} *Wagered a total of `${(ctx.profile.wagered):,.2f}`*",
            ]
        )
        if streak >= 5:
            embed.set_footer(text=f"Streak of {streak} losses")

        return await ctx.reply(embed=embed, mention_author=True)


async def setup(bot: Juno) -> None:
    await bot.add_cog(Gamble(bot))
