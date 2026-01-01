from contextlib import suppress
from typing import List
from tabulate import tabulate
from discord import Embed, HTTPException, Message
from discord.utils import as_chunks, format_dt, utcnow
from discord.ext.commands import Cog, BucketType, group, command, cooldown, check
from discord.ext.tasks import loop
from bot.core import Juno, Context
from bot.shared import codeblock
from bot.shared.formatter import human_number, plural, short_timespan, shorten
from .models import Address, Transaction
from yarl import URL

from bot.shared.paginator import Paginator


def symbol(value: float) -> str:
    return "+" if value > 0 else "-"


async def can_dm(ctx: Context) -> bool:
    try:
        await ctx.author.send()
    except HTTPException as exc:
        if exc.code == 50007:
            raise ValueError("You need to enable DMs to use this command")

    return True


class Crypto(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot
        self.check_wallets.start()
        self.check_transactions.start()

    def cog_unload(self) -> None:
        self.check_wallets.cancel()
        self.check_transactions.cancel()

    @loop(seconds=60)
    async def check_wallets(self) -> None:
        """Notify users when a wallet address receives a transaction."""

        addresses = await self.bot.redis.smembers("addr_sub")
        scheduled_deletion: List[str] = []
        for key in addresses:
            user_id, currency, address_id = key.split(":")
            user = self.bot.get_user(int(user_id))
            if not user:
                scheduled_deletion.append(key)
                continue

            address = await Address.fetch(currency, address_id)
            if not address:
                # scheduled_deletion.append(key)
                continue

            for transaction in address.unconfirmed_transactions:
                if await self.bot.redis.sismember(
                    "addr_txs",
                    f"{user_id}:{currency}:{transaction.id}",
                ):
                    continue

                await self.bot.redis.sadd(
                    "addr_txs",
                    f"{user_id}:{currency}:{transaction.id}",
                )
                embed = Embed(
                    url=transaction.url,
                    title=f"{address.currency} Transaction {'Sent' if transaction.sent else 'Received'}",
                    description=f"Your address [`{shorten(address.id, 12)}`]({address.url}) {'sent' if transaction.sent else 'received'} {transaction.amount:.8g} {transaction.currency} (`${transaction.usd_amount:,.2f} USD`)",
                )
                embed.set_footer(text=transaction.id)
                try:
                    await user.send(embed=embed)
                except HTTPException:
                    scheduled_deletion.append(key)
                    break

        if scheduled_deletion:
            await self.bot.redis.srem("addr_sub", *scheduled_deletion)

    @loop(seconds=60)
    async def check_transactions(self) -> None:
        """Notify users when a transaction is confirmed."""

        transactions = await self.bot.redis.smembers("tx_sub")
        scheduled_deletion: List[str] = []
        for key in transactions:
            user_id, currency, tx_id = key.split(":")
            user = self.bot.get_user(int(user_id))
            if not user:
                scheduled_deletion.append(key)
                continue

            transaction = await Transaction.fetch(currency, tx_id)
            if not transaction:
                scheduled_deletion.append(key)
                continue

            elif transaction.confirmed:
                scheduled_deletion.append(key)
                embed = Embed(
                    url=transaction.url,
                    title=f"{transaction.currency} Transaction Confirmed",
                    description=f"Your transaction of {transaction.amount:.8g} {transaction.currency} has been confirmed",
                )
                embed.set_footer(text=transaction.id)

                with suppress(HTTPException):
                    await user.send(embed=embed)

        if scheduled_deletion:
            await self.bot.redis.srem("tx_sub", *scheduled_deletion)

    @group(invoke_without_command=True, aliases=("cryptocurrency",))
    @cooldown(1, 5, BucketType.channel)
    async def crypto(self, ctx: Context, coin: str) -> Message:
        """View the price of a cryptocurrency."""

        async with ctx.typing():
            response = await self.bot.session.post(
                URL.build(
                    scheme="https",
                    host="min-api.cryptocompare.com",
                    path="/data/pricemultifull",
                ),
                params={
                    "fsyms": coin.upper(),
                    "tsyms": "USD",
                },
            )
            if not response.ok:
                return await ctx.warn("No response was received from the API")

            data = await response.json()
            if coin.upper() not in data.get("RAW", {}):
                return await ctx.warn("The coin provided doesn't exist")

            coin_data = data["RAW"][coin.upper()]["USD"]
            coin_data["IMAGEURL"] = (
                f"https://www.cryptocompare.com{coin_data['IMAGEURL']}"
            )

        embed = Embed()
        embed.set_author(
            name=f"{coin.upper()} Information",
            icon_url=coin_data["IMAGEURL"],
        )

        embed.add_field(name="Price", value=f"${round(coin_data['PRICE'], 2):,} USD")
        embed.add_field(
            name="Hourly Change",
            value=(
                symbol(coin_data["CHANGEHOUR"])
                + f"${abs(round(coin_data['CHANGEHOUR'], 2)):,} USD"
                + f" ({round(coin_data['CHANGEPCTHOUR'], 2):,}%)"
            ),
        )
        embed.add_field(
            name="Daily Change",
            value=(
                symbol(coin_data["CHANGE24HOUR"])
                + f"${abs(round(coin_data['CHANGE24HOUR'], 2)):,} USD"
                + f" ({round(coin_data['CHANGEPCT24HOUR'], 2):,}%)"
            ),
        )
        embed.add_field(
            name="Daily Highest",
            value=f"${round(coin_data['HIGH24HOUR'], 2):,} USD",
        )
        embed.add_field(
            name="Daily Lowest",
            value=f"${round(coin_data['LOW24HOUR'], 2):,} USD",
        )
        embed.add_field(
            name="Market Cap",
            value=f"${human_number(coin_data['MKTCAP'])} USD",
        )

        return await ctx.send(embed=embed)

    @crypto.command(name="rates", aliases=("rate", "r"))
    @cooldown(1, 5, BucketType.channel)
    async def crypto_rates(self, ctx: Context) -> Message:
        """View the current rates of the top cryptocurrencies."""

        async with ctx.typing():
            response = await self.bot.session.post(
                URL.build(
                    scheme="https",
                    host="graphql.coincap.io",
                ),
                json={
                    "variables": {"direction": "ASC", "first": 50, "sort": "rank"},
                    "query": """
                    query (
                        $after: String,
                        $before: String,
                        $direction: SortDirection,
                        $first: Int,
                        $last: Int,
                        $sort: AssetSortInput
                    ) {
                        assets(
                            after: $after,
                            before: $before,
                            direction: $direction,
                            first: $first,
                            last: $last,
                            sort: $sort
                        ) {
                            pageInfo {
                                startCursor
                                endCursor
                                hasNextPage
                                hasPreviousPage
                                __typename
                            }
                            edges {
                                cursor
                                node {
                                    changePercent24Hr
                                    name
                                    id
                                    logo
                                    marketCapUsd
                                    priceUsd
                                    rank
                                    supply
                                    symbol
                                    volumeUsd24Hr
                                    vwapUsd24Hr
                                    __typename
                                }
                                __typename
                            }
                            __typename
                        }
                    }
                """,
                },
            )
            if not response.ok:
                return await ctx.warn("No response was received from the API")

            data = await response.json()

        tables: List[str] = []
        assets = [asset["node"] for asset in data["data"]["assets"]["edges"]]
        for chunk in as_chunks(assets, 6):
            table = tabulate(
                [
                    [
                        f"{asset['rank']}",
                        shorten(asset["name"]),
                        f"${round(float(asset['priceUsd']), 2):,}",
                        f"${human_number(asset['marketCapUsd'])}",
                        f"${human_number(asset['volumeUsd24Hr'])}",
                        f"{round(float(asset['changePercent24Hr']), 2):,}%",
                    ]
                    for asset in chunk
                ],
                headers=[
                    "Rank",
                    "Name",
                    "Price",
                    "Market Cap",
                    "Volume(24H)",
                    "Change(24H)",
                ],
            )
            tables.append(
                f"`MARKET CAP:` *`${human_number(sum(float(asset['marketCapUsd']) for asset in chunk))}`*"
                f" • `EXCHANGE VOLUME:` *`${human_number(sum(float(asset['volumeUsd24Hr']) for asset in chunk))}`*"
                f"\n>>> {codeblock(table, 'prolog')}"
            )

        paginator = Paginator(ctx, tables)
        return await paginator.start()

    @crypto.group(
        name="wallet",
        aliases=("address", "addr", "w"),
        invoke_without_command=True,
    )
    @cooldown(1, 5, BucketType.channel)
    async def crypto_wallet(self, ctx: Context, address: Address) -> Message:
        """View the details of a wallet address."""

        embed = Embed(url=address.url, title=shorten(address.id, 32))
        embed.add_field(
            name="Balance",
            value=f"{address.balance:.8g} {address.currency}\n-# ${address.usd_balance:,.2f} USD",
        )
        embed.add_field(
            name="Received",
            value=f"{address.received:.8g} {address.currency}\n-# ${address.usd_received:,.2f} USD",
        )
        embed.add_field(
            name="Sent",
            value=f"{address.sent:.8g} {address.currency}\n-# ${address.usd_sent:,.2f} USD",
        )
        if address.unconfirmed_transactions + address.transactions:
            embed.add_field(
                name="Transaction"
                + (
                    "s"
                    if len((address.unconfirmed_transactions + address.transactions))
                    > 1
                    else ""
                ),
                value="\n".join(
                    [
                        f"{format_dt(transaction.confirmed_at or transaction.received_at or utcnow(), 'R')} [`{transaction.short_id}`]({transaction.url}) *`{'+' if not transaction.sent else '-'}${transaction.usd_amount:,.2f} USD`*"
                        for transaction in (
                            address.unconfirmed_transactions + address.transactions
                        )[:6]
                    ]
                )
                + (
                    f"\n> +{address.total_transactions - 6} more transactions"
                    if address.total_transactions > 6
                    else ""
                ),
                inline=False,
            )

        return await ctx.send(embed=embed)

    @crypto_wallet.command(
        name="subscribe",
        aliases=("notify", "watch", "alert", "sub"),
    )
    @check(can_dm)
    async def crypto_wallet_subscribe(self, ctx: Context, address: Address) -> Message:
        """Receive a notification when a wallet address has a new transaction."""

        await self.bot.redis.sadd(
            "addr_sub",
            f"{ctx.author.id}:{address.currency}:{address.id}",
        )
        return await ctx.approve(
            f"You'll now be notified when [`{shorten(address.id, 12)}`]({address.url}) receives a transaction"
        )

    @crypto_wallet.command(
        name="cancel",
        aliases=("unsubscribe", "unsub", "remove", "rm"),
    )
    async def crypto_wallet_cancel(self, ctx: Context, address: Address) -> Message:
        """Cancel a wallet address subscription."""

        status = await self.bot.redis.srem(
            "addr_sub",
            f"{ctx.author.id}:{address.currency}:{address.id}",
        )
        if not status:
            return await ctx.warn("You're not subscribed to this address")

        return await ctx.approve(
            f"You'll no longer be notified when [`{shorten(address.id, 12)}`]({address.url}) receives a transaction"
        )

    @crypto_wallet.command(name="clear")
    async def crypto_wallet_clear(self, ctx: Context) -> Message:
        """Remove all subscribed wallet addresses."""

        keys = [
            key
            for key in await self.bot.redis.smembers("addr_sub")
            if key.startswith(f"{ctx.author.id}:")
        ]
        if not keys:
            return await ctx.warn("You're not subscribed to any addresses")

        await self.bot.redis.srem("addr_sub", *keys)
        return await ctx.approve("No longer notifying you of any transactions")

    @crypto_wallet.command(name="list")
    async def crypto_wallet_list(self, ctx: Context) -> Message:
        """View all addresses you're subscribed to."""

        addresses = await self.bot.redis.smembers("addr_sub")
        addresses = [
            f"{currency} [`{shorten(address_id, 12)}`](https://egirl.software/{currency}:{address_id})"
            for address in addresses
            if int(address.split(":")[0]) == ctx.author.id
            and (currency := address.split(":")[1])
            and (address_id := address.split(":")[2])
        ]
        if not addresses:
            return await ctx.warn("You're not subscribed to any addresses")

        embed = Embed(title="Wallet Addresses")
        paginator = Paginator(ctx, addresses, embed)
        return await paginator.start()

    @crypto.group(
        name="transaction",
        aliases=("txid", "tx"),
        invoke_without_command=True,
    )
    @cooldown(1, 5, BucketType.channel)
    async def crypto_transaction(
        self,
        ctx: Context,
        transaction: Transaction,
    ) -> Message:
        """View the details of a transaction."""

        embed = Embed(
            url=transaction.url,
            title=shorten(transaction.id, 32),
            description="\n> ".join(
                [
                    f"**{transaction.currency}** transaction with {plural(transaction.confirmations, '`'):confirmation}",
                    f"{format_dt(transaction.created_at)} {format_dt(transaction.created_at, 'R')}"
                    + (
                        f" (*confirmed after {timespan}*)"
                        if transaction.confirmed
                        and transaction.confirmed_at
                        and (
                            timespan := short_timespan(
                                transaction.confirmed_at - transaction.created_at,
                                max_units=1,
                            )
                        )
                        else ""
                    ),
                ]
            ),
        )
        embed.add_field(
            name="Amount",
            value=f"{transaction.amount:.8g} {transaction.currency} (${transaction.usd_amount:,.2f} USD)",
        )
        embed.add_field(
            name="Fee",
            value=f"{transaction.pretty_fee} (${transaction.usd_fee:,.2f} USD)",
        )
        embed.add_field(
            name="Size",
            value=f"{transaction.size} b"
            + (f" ({transaction.virtual_size} vB)" if transaction.virtual_size else ""),
        )
        embed.add_field(
            name="Sender",
            value=f"[`{shorten(transaction.inputs[0].id, 42)}`]({transaction.url}) *`-${transaction.usd_amount:,.2f} USD`*",
            inline=False,
        )
        embed.add_field(
            name="Recipient" + ("s" if len(transaction.outputs) > 1 else ""),
            value="\n".join(
                [
                    f"[`{shorten(output.id, 42)}`]({transaction.url}) *`${output.usd_amount:,.2f} USD`*"
                    for output in transaction.outputs[:3]
                ]
            )
            + (
                f"\n> +{len(transaction.outputs) - 3} more transactions"
                if len(transaction.outputs) > 3
                else ""
            ),
            inline=False,
        )

        return await ctx.send(embed=embed)

    @crypto_transaction.command(
        name="subscribe",
        aliases=("notify", "watch", "alert", "sub"),
    )
    @check(can_dm)
    async def crypto_transaction_subscribe(
        self,
        ctx: Context,
        transaction: Transaction,
    ) -> Message:
        """Receive a notification when a transaction is confirmed."""

        if transaction.confirmed:
            return await ctx.warn("The transaction has already been confirmed")

        await self.bot.redis.sadd(
            "tx_sub",
            f"{ctx.author.id}:{transaction.currency}:{transaction.id}",
        )
        return await ctx.approve(
            f"You'll now be notified when [`{shorten(transaction.id, 12)}`]({transaction.url}) has been confirmed"
        )

    @crypto_transaction.command(
        name="cancel",
        aliases=("unsubscribe", "unsub", "remove", "rm"),
    )
    async def crypto_transaction_cancel(
        self,
        ctx: Context,
        transaction: Transaction,
    ) -> Message:
        """Cancel a transaction subscription."""

        status = await self.bot.redis.srem(
            "tx_sub",
            f"{ctx.author.id}:{transaction.currency}:{transaction.id}",
        )
        if not status:
            return await ctx.warn("You're not subscribed to this transaction")

        return await ctx.approve(
            f"You'll no longer be notified when [`{shorten(transaction.id, 12)}`]({transaction.url}) has been confirmed"
        )

    @crypto_transaction.command(name="clear")
    async def crypto_transaction_clear(self, ctx: Context) -> Message:
        """Remove all subscribed transactions."""

        keys = [
            key
            for key in await self.bot.redis.smembers("tx_sub")
            if key.startswith(f"{ctx.author.id}:")
        ]
        if not keys:
            return await ctx.warn("You're not subscribed to any transactions")

        await self.bot.redis.srem("tx_sub", *keys)
        return await ctx.approve("No longer notifying you of any transactions")

    @crypto_transaction.command(name="list")
    async def crypto_transaction_list(self, ctx: Context) -> Message:
        """View all transactions you're subscribed to."""

        transactions = await self.bot.redis.smembers("tx_sub")
        transactions = [
            f"{currency} [`{shorten(tx_id, 12)}`](https://egirl.software/{currency}:{tx_id})"
            for transaction in transactions
            if int(transaction.split(":")[0]) == ctx.author.id
            and (currency := transaction.split(":")[1])
            and (tx_id := transaction.split(":")[2])
        ]
        if not transactions:
            return await ctx.warn("You're not subscribed to any transactions")

        embed = Embed(title="Transactions")
        paginator = Paginator(ctx, transactions, embed)
        return await paginator.start()

    @crypto.command(
        name="subscribe",
        aliases=("notify", "watch", "alert", "sub"),
        hidden=True,
    )
    @check(can_dm)
    async def crypto_subscribe(
        self,
        ctx: Context,
        transaction: Transaction,
    ) -> Message:
        """Receive a notification when a transaction is confirmed."""

        return await ctx.invoke(
            self.crypto_transaction_subscribe,
            transaction=transaction,
        )

    @crypto.command(
        name="cancel",
        aliases=("unsubscribe", "unsub", "remove", "rm"),
        hidden=True,
    )
    async def crypto_cancel(
        self,
        ctx: Context,
        transaction: Transaction,
    ) -> Message:
        """Cancel a transaction subscription."""

        return await ctx.invoke(
            self.crypto_transaction_cancel,
            transaction=transaction,
        )

    @command(aliases=("btc",))
    @cooldown(1, 3, BucketType.channel)
    async def bitcoin(self, ctx: Context) -> Message:
        """View the current rates of Bitcoin."""

        return await ctx.invoke(self.crypto, "btc")

    @group(aliases=("eth",), invoke_without_command=True)
    @cooldown(1, 3, BucketType.channel)
    async def ethereum(self, ctx: Context) -> Message:
        """View the current rates of Ethereum."""

        return await ctx.invoke(self.crypto, "eth")

    @ethereum.command(name="gas", aliases=("gwei",))
    @cooldown(1, 15, BucketType.channel)
    async def ethereum_gas(self, ctx: Context) -> Message:
        """View the current gas price of Ethereum."""

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="api.owlracle.info",
                    path="/v3/eth/gas",
                ),
                params={"apikey": "5cb7b35142204038b9ccf4dc1b77d08f"},
            )
            if not response.ok:
                return await ctx.warn("No response was received from the API")

            data = await response.json()

        embed = Embed(title="Ethereum Gas Price")
        for speed, value in {"Slow": 0, "Standard": 1, "Fast": 2}.items():
            embed.add_field(
                name=speed,
                value="\n".join(
                    [
                        f"{data['speeds'][value]['maxFeePerGas']:,.2f} GWEI",
                        f"**FEE:** ${data['speeds'][value]['estimatedFee']:,.2f} USD",
                    ]
                ),
                inline=True,
            )

        return await ctx.send(embed=embed)

    @command(aliases=("ltc",))
    @cooldown(1, 3, BucketType.channel)
    async def litecoin(self, ctx: Context) -> Message:
        """View the current rates of Litecoin."""

        return await ctx.invoke(self.crypto, "ltc")

    @command(aliases=("xrp",))
    @cooldown(1, 3, BucketType.channel)
    async def ripple(self, ctx: Context) -> Message:
        """View the current rates of Ripple."""

        return await ctx.invoke(self.crypto, "xrp")

    @command(aliases=("xmr",))
    @cooldown(1, 3, BucketType.channel)
    async def monero(self, ctx: Context) -> Message:
        """View the current rates of Monero."""

        return await ctx.invoke(self.crypto, "xmr")

    @command(aliases=("sol",))
    @cooldown(1, 3, BucketType.channel)
    async def solana(self, ctx: Context) -> Message:
        """View the current rates of Solana."""

        return await ctx.invoke(self.crypto, "sol")


async def setup(bot: Juno) -> None:
    return await bot.add_cog(Crypto(bot))
