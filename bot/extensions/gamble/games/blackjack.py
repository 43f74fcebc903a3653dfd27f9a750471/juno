import random
from typing import Annotated, List, Optional
from discord.ext.commands import BucketType, command, max_concurrency
from discord.ui import Button, button
from discord import ButtonStyle, Embed, Interaction, Message
from ..meta import Cog, Context, Amount
from bot.shared import RestrictedView

SUITS = {
    "Spades": "\u2664",
    "Hearts": "\u2661",
    "Clubs": "\u2667",
    "Diamonds": "\u2662",
}
CARDS = {
    "A": 11,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "10": 10,
    "J": 10,
    "Q": 10,
    "K": 10,
}


class Card:
    def __init__(self, name: str, suit: str, value: int) -> None:
        self.name = name
        self.suit = suit
        self.value = value

    def __str__(self) -> str:
        return f"{self.name}{self.suit}"

    def __repr__(self) -> str:
        return str(self)


class Deck:
    cards: list[Card]
    member: List[Card]
    dealer: List[Card]

    def __init__(self):
        self.cards = [
            Card(name, suit, value)
            for name, value in CARDS.items()
            for suit in SUITS.values()
        ]
        random.shuffle(self.cards)
        self.member = [self.cards.pop(), self.cards.pop()]
        self.dealer = [self.cards.pop(), self.cards.pop()]

    @staticmethod
    def score(cards: List[Card], hidden: bool = False) -> int:
        if hidden:
            return cards[0].value

        score = sum(card.value for card in cards)
        aces = sum(1 for card in cards if card.name == "A")
        while score > 21 and aces:
            score -= 10
            aces -= 1

        return score

    def is_winner(self):
        member_score = self.score(self.member)
        if member_score > 21:
            return False, "BUST"

        dealer_score = self.score(self.dealer)
        if member_score == 21 and len(self.member) == 2:
            if dealer_score == 21 and len(self.dealer) == 2:
                return None, "Both got Blackjack!"

            return True, "Blackjack!"

        while dealer_score < 16 or dealer_score < member_score:
            self.dealer.append(self.cards.pop())
            dealer_score = self.score(self.dealer)

        if dealer_score > 21:
            return True, "The dealer busted!"
        elif dealer_score == 21:
            return False, "The dealer got Blackjack!"
        elif member_score > dealer_score:
            return True, f"Your {member_score} beats dealer's {dealer_score}!"
        elif member_score == dealer_score:
            return None, f"Both have {member_score} - It's a tie!"
        else:
            return False, f"Dealer's {dealer_score} beats your {member_score}!"


class Blackjack(RestrictedView):
    ctx: Context
    bet: float
    initial_bet: float
    deck: Deck

    def __init__(self, ctx: Context, bet: float) -> None:
        super().__init__(ctx=ctx)
        self.bet = bet
        self.initial_bet = bet
        self.deck = Deck()
        self.double_down.label = f"Double Down (${bet * 2:,.2f})"
        if bet * 2 <= ctx.profile.balance:
            self.double_down.disabled = False

    @property
    def hidden(self) -> bool:
        return not self.children[0].disabled  # type: ignore

    @property
    def embed(self) -> Embed:
        winner, reason = self.deck.is_winner() if not self.hidden else (None, None)
        hidden = self.hidden
        embed = Embed(title=f"Blackjack for ${self.bet:,.2f}")
        if hidden:
            ...
        elif winner is None:
            embed.description = f"{reason}\n-# You kept your money!"
        elif winner:
            embed.description = f"{reason}\n-# You won ${self.bet:,.2f}, you now have ${self.ctx.profile.balance:,.2f}!"
        else:
            embed.description = f"{reason}\n-# You lost ${self.bet:,.2f}, you now have ${self.ctx.profile.balance:,.2f}!"

        if embed.description:
            self.clear_items()
            if self.ctx.profile.balance >= self.initial_bet:
                self.add_item(
                    Button(
                        label=f"Play Again (${self.initial_bet:,.2f})",
                        style=ButtonStyle.green,
                        custom_id="new_game",
                    )
                )
                self.children[0].callback = self.new_game

        embed.add_field(
            name=f"Your Hand ({Deck.score(self.deck.member)})",
            value=" ".join(f"`{card}`" for card in self.deck.member),
            inline=False,
        )
        if hidden:
            embed.add_field(
                name=f"Dealer's Hand ({Deck.score(self.deck.dealer, hidden=True)}+)",
                value=f"`{self.deck.dealer[0]}` `??`",
                inline=False,
            )
        else:
            embed.add_field(
                name=f"Dealer's Hand ({Deck.score(self.deck.dealer)})",
                value=" ".join(f"`{card}`" for card in self.deck.dealer),
                inline=False,
            )

        return embed

    @button(label="Hit", style=ButtonStyle.blurple)
    async def hit(self, interaction: Interaction, _: Button):
        self.deck.member.append(self.deck.cards.pop())
        if self.deck.score(self.deck.member) >= 21:
            winner = self.deck.is_winner()
            if winner is True:
                self.ctx.profile.balance += self.bet * 2
            elif winner is None:
                self.ctx.profile.balance += self.bet

            await self.ctx.profile.save(self.ctx.bot)
            for child in self.children:
                child.disabled = True  # type: ignore

        self.children[2].disabled = True  # type: ignore
        await interaction.response.edit_message(embed=self.embed, view=self)

    @button(label="Stand", style=ButtonStyle.blurple)
    async def stand(self, interaction: Interaction, _: Button):
        for child in self.children:
            child.disabled = True  # type: ignore

        winner = self.deck.is_winner()
        if winner is True:
            self.ctx.profile.balance += self.bet * 2
        elif winner is None:
            self.ctx.profile.balance += self.bet

        await self.ctx.profile.save(self.ctx.bot)
        await interaction.response.edit_message(embed=self.embed, view=self)

    @button(label="Double Down", style=ButtonStyle.blurple)
    async def double_down(self, interaction: Interaction, _: Button):
        self.bet *= 2
        self.deck.member.append(self.deck.cards.pop())

        if self.deck.score(self.deck.member) >= 21:
            winner = self.deck.is_winner()
            if winner is True:
                self.ctx.profile.balance += self.bet * 2
                await self.ctx.profile.save(self.ctx.bot)

            for child in self.children:
                child.disabled = True  # type: ignore

            await interaction.response.edit_message(embed=self.embed, view=self)
            return

        await self.stand.callback(interaction)

    async def new_game(self, interaction: Interaction):
        view = Blackjack(self.ctx, self.initial_bet)
        return await view.start(interaction)

    async def start(
        self, interaction: Optional[Interaction] = None
    ) -> Optional[Message]:
        self.ctx.profile.balance -= self.initial_bet
        await self.ctx.profile.save(self.ctx.bot)

        if not interaction:
            return await self.ctx.send(embed=self.embed, view=self)

        return await interaction.response.edit_message(embed=self.embed, view=self)


class BlackjackCog(Cog):
    @command()
    @max_concurrency(1, per=BucketType.user)
    async def blackjack(
        self, ctx: Context, bet: Annotated[float, Amount]
    ) -> Optional[Message]:
        """Play a game of blackjack."""

        view = Blackjack(ctx, bet)
        return await view.start()
