import random
from typing import Annotated, Literal
from discord.ext.commands import BucketType, command, max_concurrency
from discord.ui import Button, button
from discord import ButtonStyle, Embed, Interaction, Message
from ..meta import Cog, Context, Amount
from bot.shared import RestrictedView


class Road(RestrictedView):
    bet: float
    difficulty: Literal["easy", "medium", "hard"]
    position: int = 0
    progress: list = []

    def __init__(
        self,
        ctx: Context,
        bet: float,
        difficulty: Literal["easy", "medium", "hard"],
    ) -> None:
        super().__init__(ctx=ctx)
        self.bet = bet
        self.difficulty = difficulty
        self.position = 0
        self.progress = [1]

    @property
    def embed(self) -> Embed:
        embed = Embed(title="Mission Uncrossable")
        embed.description = ""
        embed.set_footer(
            text=" ∙ ".join(
                [
                    f"${self.bet:,.2f}",
                    f"{self.difficulty.title()} Risk",
                ]
            )
        )

        before_multipliers = self.progress[:2]
        after_multipliers = self.progress[2:5]
        progress_str = ""
        for i, m in enumerate(before_multipliers):
            if i < self.position // 25:
                progress_str += f"*`{m}x`* "
            else:
                progress_str += f"`{m}x` "

        progress_str += " **`🐓`** "
        for i, m in enumerate(after_multipliers):
            if i < (self.position - self.position // 25 * 25) // 5:
                progress_str += f"*`{m}x`* "
            else:
                progress_str += f"`{m}x` "

        embed.add_field(name="Progress", value=progress_str)

        # display the chickens current position on the road
        return embed

    @property
    def hit_chance(self) -> int:
        if self.difficulty == "easy":
            return 1  # 1 hit chance per 25 lanes
        elif self.difficulty == "medium":
            return 3  # 3 hit chances per 25 lanes
        elif self.difficulty == "hard":
            return 5  # 5 hit chances per 25 lanes
        return 1

    @button(label="Cross", style=ButtonStyle.blurple)
    async def cross(self, interaction: Interaction, button: Button):
        """Attempt to cross the road without getting hit."""

        hit_chance_threshold = self.hit_chance * ((self.position // 25) + 1)
        collision = random.randint(1, 100) <= hit_chance_threshold

        if collision:
            self.progress = []
            self.position = 0
            await interaction.response.edit_message(embed=self.embed, view=None)
            await interaction.followup.send("You got hit! You lost the bet.")
        else:
            self.position += 1
            self.progress.append(self.progress[-1] * 2)
            self.bet *= 2

            await interaction.response.edit_message(embed=self.embed, view=self)
            await interaction.followup.send(
                f"Lane {self.position} crossed! Current bet: ${self.bet:,.2f}",
                ephemeral=True,
            )

    @button(label="Cash out", style=ButtonStyle.green)
    async def cashout(self, interaction: Interaction, button: Button):
        """Cash out your current bet."""

        await interaction.response.edit_message(embed=self.embed, view=None)
        await interaction.followup.send(
            f"Congratulations! You successfully crossed the road. You won ${self.bet:,.2f}."
        )


class MissionUncrossable(Cog):
    @command(aliases=("chicken", "crossroad"))
    @max_concurrency(1, per=BucketType.user)
    async def uncrossable(
        self,
        ctx: Context,
        bet: Annotated[float, Amount],
        difficulty: Literal["easy", "medium", "hard"] = "medium",
    ) -> Message:
        """Cross the road without getting hit.

        Chance of collision per game
        >>> **Easy:** 1 for every 25 lanes
        **Medium:** 3 for every 25 lanes
        **Hard:** 5 for every 25 lanes
        """

        view = Road(ctx, bet, difficulty)
        return await ctx.send(embed=view.embed, view=view)
