import re
from discord.ext.commands import Cog as BaseCog, Converter
from bot.core import Juno, Context as OriginalContext
from .schema import EconomyUser

class Context(OriginalContext):
    profile: EconomyUser


class Amount(Converter):
    async def convert(self, ctx: Context, argument: str) -> float:
        if not ctx.profile.balance or ctx.profile.balance <= 0:
            raise ValueError("You don't have any money for that")

        if argument.lower() in ("all", "max"):
            return ctx.profile.balance

        elif argument.lower() in ("half", "/2", "/"):
            return ctx.profile.balance / 2

        elif percentage := re.match(r"(\d+)%", argument):
            return ctx.profile.balance * (int(percentage.group(1)) / 100)

        amount = self.humanize_number(argument)
        if amount > ctx.profile.balance:
            raise ValueError("You don't have that much money")

        elif amount <= 0:
            raise ValueError("You can't use a negative amount")

        return amount

    def humanize_number(self, argument: str) -> float:
        argument = argument.lower()
        for char in (",", "$"):
            argument = argument.replace(char, "").replace(f"{char} ", "")

        p = {
            "k": 1_000,
            "m": 1_000_000,
            "b": 1_000_000_000,
            "t": 1_000_000_000_000,
            "q": 1_000_000_000_000_000,
            "s": 1_000_000_000_000_000_000,
        }
        if argument[-1] in p:
            return float(argument[:-1]) * p[argument[-1]]

        return float(argument)
    
class Cog(BaseCog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    async def cog_check(self, ctx: Context):
        ctx.profile = await EconomyUser.fetch(self.bot, ctx.author.id)
        return True

    async def save_bet(
        self,
        ctx: Context,
        amount: float,
        multiplier: float,
        payout: float,
    ) -> None:
        query = """
        INSERT INTO economy.bet (
            user_id,
            amount,
            multiplier,
            payout,
            game
        ) VALUES ($1, $2, $3, $4, $5)
        """
        rakeback_query = """
        INSERT INTO economy.rakeback (user_id, amount)
        VALUES ($1, $2) ON CONFLICT (user_id)
        DO UPDATE SET amount = economy.rakeback.amount + EXCLUDED.amount
        """
        if payout > 0:
            rakeback = amount * 0.15 / 50
            await self.bot.db.execute(rakeback_query, ctx.author.id, rakeback)

        await self.bot.db.execute(
            query,
            ctx.author.id,
            amount,
            multiplier,
            payout,
            ctx.command.qualified_name.title(),
        )