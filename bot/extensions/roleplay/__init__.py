import json
from pathlib import Path
from random import choice
from typing import Optional, TypedDict, cast

from discord import Embed, Member, Message
from discord.ext.commands import Cog, command
from humanize import ordinal
from yarl import URL

from bot.core import Context, Juno
from bot.shared.formatter import human_join

BASE_URL = URL.build(
    scheme="https",
    host="api.otakugifs.xyz",
)


class Action(TypedDict):
    description: str
    message: str


with open(Path(__file__).parent / "actions.json") as file:
    ACTIONS: dict[str, Action] = json.load(file)


class Roleplay(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    async def roleplay(self, ctx: Context, target: Member, action: str) -> Message:
        url = BASE_URL / "gif"
        response = await self.bot.session.get(url, params={"reaction": action})
        data = await response.json()
        if not data.get("url"):
            return await ctx.warn("Something went wrong while fetching the image")

        amount = 0
        if ctx.author != target:
            amount = await self.bot.redis.incr(
                f"roleplay.{action}:{ctx.author.id}:{target.id}"
            )

        embed = Embed(
            description=f"{ctx.author.mention} **{ACTIONS[action]['message']}** {target.mention if ctx.author != target else 'themselves'}"
            + (
                f" for the **{ordinal(amount)}** time!"
                if amount
                else f".. {choice(['sus', 'wtf', 'lol?'])}"
            ),
        )
        embed.set_image(url=data["url"])

        return await ctx.send(embed=embed)

    @command(name="roleplay", aliases=("rpstats", "rp"))
    async def roleplay_stats(self, ctx: Context, target: Member) -> Message:
        """Check roleplay stats with someone."""

        keys = await self.bot.redis.keys(f"roleplay.*:{ctx.author.id}:{target.id}")
        actions = []
        for key in keys:
            action = str(key).split(".")[1].split(":")[0]
            amount = int(cast(str, await self.bot.redis.get(key)))

            plural = ("es" if action.endswith("s") else "s") if int(amount) > 1 else ""
            actions.append(f"{amount} {action}{plural}")

        if not actions:
            return await ctx.warn("You haven't roleplayed with this user yet")

        human_actions = human_join(
            sorted(actions, key=lambda x: int(x.split()[0]), reverse=True),
            final="and",
        )
        return await ctx.reply(f"You've given {target} {human_actions}")

    @command(help=ACTIONS["airkiss"]["description"])
    async def airkiss(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["angrystare"]["description"])
    async def angrystare(
        self, ctx: Context, target: Optional[Member] = None
    ) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["bite"]["description"])
    async def bite(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["bleh"]["description"])
    async def bleh(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["blush"]["description"])
    async def blush(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["brofist"]["description"])
    async def brofist(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["celebrate"]["description"])
    async def celebrate(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["cheers"]["description"])
    async def cheers(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["clap"]["description"])
    async def clap(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["confused"]["description"])
    async def confused(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["cool"]["description"])
    async def cool(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["cry"]["description"])
    async def cry(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["cuddle"]["description"])
    async def cuddle(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["dance"]["description"])
    async def dance(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["drool"]["description"])
    async def drool(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["evillaugh"]["description"])
    async def evillaugh(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["facepalm"]["description"])
    async def facepalm(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["handhold"]["description"])
    async def handhold(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["happy"]["description"])
    async def happy(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["headbang"]["description"])
    async def headbang(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["hug"]["description"])
    async def hug(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["kiss"]["description"])
    async def kiss(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["laugh"]["description"])
    async def laugh(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["lick"]["description"])
    async def lick(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["love"]["description"])
    async def love(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["mad"]["description"])
    async def mad(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["nervous"]["description"])
    async def nervous(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["no"]["description"])
    async def no(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["nom"]["description"])
    async def nom(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["nosebleed"]["description"])
    async def nosebleed(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["nuzzle"]["description"])
    async def nuzzle(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["nyah"]["description"])
    async def nyah(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["pat"]["description"])
    async def pat(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["peek"]["description"])
    async def peek(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["pinch"]["description"])
    async def pinch(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["poke"]["description"])
    async def poke(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["pout"]["description"])
    async def pout(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["punch"]["description"])
    async def punch(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["roll"]["description"])
    async def roll(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["sad"]["description"])
    async def sad(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["scared"]["description"])
    async def scared(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["shout"]["description"])
    async def shout(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["shrug"]["description"])
    async def shrug(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["shy"]["description"])
    async def shy(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["sigh"]["description"])
    async def sigh(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["sip"]["description"])
    async def sip(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["slap"]["description"])
    async def slap(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["sleep"]["description"])
    async def sleep(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["slowclap"]["description"])
    async def slowclap(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["smack"]["description"])
    async def smack(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["smile"]["description"])
    async def smile(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["smug"]["description"])
    async def smug(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["sneeze"]["description"])
    async def sneeze(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["sorry"]["description"])
    async def sorry(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["stare"]["description"])
    async def stare(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["surprised"]["description"])
    async def surprised(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["sweat"]["description"])
    async def sweat(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["thumbsup"]["description"])
    async def thumbsup(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["tickle"]["description"])
    async def tickle(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["tired"]["description"])
    async def tired(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["wave"]["description"])
    async def wave(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["wink"]["description"])
    async def wink(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["woah"]["description"])
    async def woah(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["yawn"]["description"])
    async def yawn(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)

    @command(help=ACTIONS["yay"]["description"])
    async def yay(self, ctx: Context, target: Optional[Member] = None) -> Message:
        return await self.roleplay(ctx, target or ctx.author, ctx.command.name)


async def setup(bot: Juno) -> None:
    await bot.add_cog(Roleplay(bot))
