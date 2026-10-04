import discord
from discord.ext import commands
import aiohttp
import json
import os
from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")

PREFIX = "."

# Your Discord User ID
OWNER_ID = 494442502632243200

# Channel where reviews will be posted
REVIEWS_CHANNEL_ID = 1556289345261019136

# Saved Litecoin address file
DATA_FILE = "address.json"

# Litecoin blockchain API
LTC_API = "https://litecoinspace.org/api"

# CoinGecko API
PRICE_API = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=litecoin&vs_currencies=usd"
)


# ============================================================
# BOT SETUP
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None
)


# ============================================================
# ADDRESS STORAGE
# ============================================================

def load_address():

    if not os.path.exists(DATA_FILE):
        return ""

    try:

        with open(DATA_FILE, "r") as file:
            data = json.load(file)

        return data.get("address", "")

    except Exception as e:

        print(f"Address load error: {e}")
        return ""


def save_address(address):

    try:

        with open(DATA_FILE, "w") as file:

            json.dump(
                {
                    "address": address
                },
                file,
                indent=4
            )

        return True

    except Exception as e:

        print(f"Address save error: {e}")
        return False


# ============================================================
# API REQUEST
# ============================================================

async def api_get(url):

    timeout = aiohttp.ClientTimeout(total=20)

    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.get(
                url,
                headers={
                    "User-Agent": "Discord-LTC-Bot/1.0"
                }
            ) as response:

                if response.status != 200:

                    print(
                        f"API returned status {response.status}"
                    )

                    return None

                return await response.json()

    except Exception as e:

        print(f"API error: {e}")
        return None


# ============================================================
# LTC PRICE
# ============================================================

async def get_ltc_price():

    data = await api_get(PRICE_API)

    if not data:
        return None

    try:

        return float(
            data["litecoin"]["usd"]
        )

    except Exception as e:

        print(f"Price error: {e}")
        return None


# ============================================================
# FORMATTING
# ============================================================

def satoshis_to_ltc(satoshis):

    return satoshis / 100_000_000


def format_ltc(satoshis):

    return (
        f"{satoshis_to_ltc(satoshis):.8f} LTC"
    )


def format_usd(satoshis, ltc_price):

    usd = (
        satoshis_to_ltc(satoshis)
        * ltc_price
    )

    return f"${usd:,.2f}"


def shorten_tx(txid):

    if not txid:
        return "Unknown"

    if len(txid) <= 16:
        return txid

    return (
        f"{txid[:8]}..."
        f"{txid[-8:]}"
    )


# ============================================================
# BOT READY
# ============================================================

@bot.event
async def on_ready():

    print("================================")
    print(f"Logged in as: {bot.user}")
    print(f"Bot ID: {bot.user.id}")
    print(f"Prefix: {PREFIX}")
    print("================================")

    # Register persistent button
    bot.add_view(ReviewView())


# ============================================================
# .SETADDY
# ============================================================

@bot.command()
async def setaddy(ctx, *, address=None):

    # Owner only
    if ctx.author.id != OWNER_ID:

        return await ctx.send(
            "❌ You don't have permission "
            "to change the payment address."
        )

    if not address:

        return await ctx.send(
            "❌ Usage:\n"
            "`.setaddy <litecoin address>`"
        )

    address = address.strip()

    # Basic Litecoin address check
    valid_prefixes = (
        "L",
        "M",
        "ltc1",
        "Q"
    )

    if not address.startswith(valid_prefixes):

        return await ctx.send(
            "❌ That doesn't look like a Litecoin address."
        )

    # Save
    if not save_address(address):

        return await ctx.send(
            "❌ Failed to save the address."
        )

    embed = discord.Embed(
        title="✅ Payment Address Updated",
        description=(
            "The Litecoin payment address "
            "has been successfully updated."
        ),
        color=discord.Color.green()
    )

    embed.add_field(
        name="💰 Litecoin Address",
        value=f"```{address}```",
        inline=False
    )

    embed.set_footer(
        text=f"Updated by {ctx.author}"
    )

    await ctx.send(
        embed=embed
    )


# ============================================================
# .ADDY
# ============================================================

@bot.command()
async def addy(ctx):

    address = load_address()

    if not address:

        return await ctx.send(
            "❌ No Litecoin address has been set.\n\n"
            "Use `.setaddy <address>` first."
        )

    embed = discord.Embed(
        title="💰 Litecoin Payment Address",
        description=(
            "Send your Litecoin payment "
            "to the address below."
        ),
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="LTC Address",
        value=f"```{address}```",
        inline=False
    )

    embed.set_footer(
        text="Double-check the address before sending."
    )

    await ctx.send(
        embed=embed
    )


# ============================================================
# .BAL
# ============================================================

@bot.command()
async def bal(ctx):

    address = load_address()

    if not address:

        return await ctx.send(
            "❌ No Litecoin address has been set.\n\n"
            "Use `.setaddy <address>` first."
        )

    loading = await ctx.send(
        "🔎 **Checking Litecoin wallet...**"
    )

    # --------------------------------------------------------
    # ADDRESS DATA
    # --------------------------------------------------------

    address_data = await api_get(
        f"{LTC_API}/address/{address}"
    )

    if not address_data:

        return await loading.edit(
            content=(
                "❌ Failed to retrieve "
                "Litecoin wallet information."
            )
        )

    # --------------------------------------------------------
    # TRANSACTIONS
    # --------------------------------------------------------

    transactions = await api_get(
        f"{LTC_API}/address/{address}/txs"
    )

    if transactions is None:
        transactions = []

    # --------------------------------------------------------
    # LTC PRICE
    # --------------------------------------------------------

    ltc_price = await get_ltc_price()

    if ltc_price is None:

        return await loading.edit(
            content=(
                "❌ Failed to retrieve the "
                "current LTC/USD price."
            )
        )

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    chain = address_data.get(
        "chain_stats",
        {}
    )

    mempool = address_data.get(
        "mempool_stats",
        {}
    )

    # Confirmed received
    confirmed_received = chain.get(
        "funded_txo_sum",
        0
    )

    # Confirmed spent
    confirmed_spent = chain.get(
        "spent_txo_sum",
        0
    )

    # Pending received
    pending_received = mempool.get(
        "funded_txo_sum",
        0
    )

    # Pending spent
    pending_spent = mempool.get(
        "spent_txo_sum",
        0
    )

    # Balance
    confirmed_balance = (
        confirmed_received
        - confirmed_spent
    )

    pending_balance = (
        pending_received
        - pending_spent
    )

    balance = (
        confirmed_balance
        + pending_balance
    )

    # Total received
    total_received = (
        confirmed_received
        + pending_received
    )

    # Transaction count
    tx_count = (
        chain.get("tx_count", 0)
        + mempool.get("tx_count", 0)
    )

    # --------------------------------------------------------
    # WALLET EMBED
    # --------------------------------------------------------

    embed = discord.Embed(
        title="💰 Litecoin Wallet",
        description=f"```{address}```",
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc)
    )

    # Balance
    embed.add_field(
        name="💵 Balance",
        value=(
            f"**{format_usd(balance, ltc_price)}**\n"
            f"`{format_ltc(balance)}`"
        ),
        inline=False
    )

    # Total received
    embed.add_field(
        name="📥 Total Received",
        value=(
            f"**{format_usd(total_received, ltc_price)}**\n"
            f"`{format_ltc(total_received)}`"
        ),
        inline=True
    )

    # Current LTC price
    embed.add_field(
        name="📈 LTC Price",
        value=(
            f"**${ltc_price:,.2f}**"
        ),
        inline=True
    )

    # Transaction count
    embed.add_field(
        name="🧾 Transactions",
        value=f"**{tx_count}**",
        inline=True
    )

    # --------------------------------------------------------
    # RECENT 5 TRANSACTIONS
    # --------------------------------------------------------

    recent_transactions = ""

    for index, tx in enumerate(
        transactions[:5],
        start=1
    ):

        txid = tx.get(
            "txid",
            ""
        )

        received = 0
        sent = 0

        # ----------------------------------------------
        # OUTPUTS
        # ----------------------------------------------

        for vout in tx.get(
            "vout",
            []
        ):

            script = vout.get(
                "scriptpubkey",
                {}
            )

            output_address = script.get(
                "address"
            )

            if output_address == address:

                received += vout.get(
                    "value",
                    0
                )

        # ----------------------------------------------
        # INPUTS
        # ----------------------------------------------

        for vin in tx.get(
            "vin",
            []
        ):

            prevout = vin.get(
                "prevout"
            )

            if not prevout:
                continue

            script = prevout.get(
                "scriptpubkey",
                {}
            )

            input_address = script.get(
                "address"
            )

            if input_address == address:

                sent += prevout.get(
                    "value",
                    0
                )

        # ----------------------------------------------
        # NET TRANSACTION
        # ----------------------------------------------

        net = received - sent

        if net > 0:

            emoji = "🟢"
            sign = "+"

        elif net < 0:

            emoji = "🔴"
            sign = "-"

        else:

            emoji = "⚪"
            sign = ""

        absolute_value = abs(net)

        usd_value = (
            satoshis_to_ltc(
                absolute_value
            )
            * ltc_price
        )

        # ----------------------------------------------
        # CONFIRMATION
        # ----------------------------------------------

        status = tx.get(
            "status",
            {}
        )

        if status.get("confirmed"):

            confirmation = "✅ Confirmed"

        else:

            confirmation = "⏳ Pending"

        # ----------------------------------------------
        # ADD TO EMBED
        # ----------------------------------------------

        recent_transactions += (
            f"{emoji} **{sign}"
            f"{format_ltc(absolute_value)}** "
            f"(${usd_value:,.2f})\n"
            f"`{shorten_tx(txid)}` • "
            f"{confirmation}\n\n"
        )

    if not recent_transactions:

        recent_transactions = (
            "No transactions found."
        )

    embed.add_field(
        name="🧾 Recent 5 Transactions",
        value=recent_transactions[:1024],
        inline=False
    )

    embed.set_footer(
        text=(
            "Live Litecoin blockchain data • "
            "LTC/USD via CoinGecko"
        )
    )

    await loading.edit(
        content="",
        embed=embed
    )


# ============================================================
# REVIEW MODAL
# ============================================================

class ReviewModal(
    discord.ui.Modal,
    title="Leave a Review"
):

    review = discord.ui.TextInput(
        label="Your Review",
        placeholder=(
            "Tell us about your experience..."
        ),
        style=discord.TextStyle.paragraph,
        required=True,
        min_length=1,
        max_length=1000
    )

    rating = discord.ui.TextInput(
        label="Rating",
        placeholder="Example: 5/5",
        required=True,
        min_length=1,
        max_length=10
    )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        if not interaction.guild:

            return await interaction.response.send_message(
                "❌ Reviews can only be submitted in a server.",
                ephemeral=True
            )

        channel = interaction.guild.get_channel(
            REVIEWS_CHANNEL_ID
        )

        if channel is None:

            return await interaction.response.send_message(
                "❌ Reviews channel could not be found.",
                ephemeral=True
            )

        embed = discord.Embed(
            title="⭐ New Customer Review",
            description=self.review.value,
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.set_author(
            name=str(interaction.user),
            icon_url=interaction.user.display_avatar.url
        )

        embed.add_field(
            name="⭐ Rating",
            value=self.rating.value,
            inline=True
        )

        embed.add_field(
            name="👤 Customer",
            value=interaction.user.mention,
            inline=True
        )

        embed.set_footer(
            text="Customer Review System"
        )

        await channel.send(
            embed=embed
        )

        await interaction.response.send_message(
            "✅ Your review has been submitted!",
            ephemeral=True
        )


# ============================================================
# REVIEW VIEW
# ============================================================

class ReviewView(discord.ui.View):

    def __init__(self):

        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="Leave a Review",
        emoji="⭐",
        style=discord.ButtonStyle.primary,
        custom_id="customer_review_button"
    )
    async def review_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.send_modal(
            ReviewModal()
        )


# ============================================================
# .DONE
# ============================================================

@bot.command()
async def done(ctx):

    embed = discord.Embed(
        title="✅ Order Completed!",
        description=(
            "Thank you for your purchase!\n\n"
            "We hope you enjoyed your experience "
            "with us.\n\n"
            "⭐ **Want to leave a review?**\n"
            "Click the button below and tell us "
            "what you think!"
        ),
        color=discord.Color.green()
    )

    embed.add_field(
        name="⭐ Customer Review",
        value=(
            "Your feedback helps us improve "
            "and helps future customers."
        ),
        inline=False
    )

    embed.set_footer(
        text="Thank you for supporting us!"
    )

    await ctx.send(
        embed=embed,
        view=ReviewView()
    )


# ============================================================
# ERROR HANDLER
# ============================================================

@bot.event
async def on_command_error(
    ctx,
    error
):

    if isinstance(
        error,
        commands.CommandNotFound
    ):
        return

    if isinstance(
        error,
        commands.MissingRequiredArgument
    ):

        return await ctx.send(
            "❌ Missing information."
        )

    print(
        f"Command error: {error}"
    )


# ============================================================
# START BOT
# ============================================================

if not TOKEN:

    raise ValueError(
        "DISCORD_TOKEN environment variable is missing!"
    )

bot.run(TOKEN)
