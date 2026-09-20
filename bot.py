import os
import json
import uuid
import asyncio
import aiohttp
from datetime import datetime, timezone

import discord
from discord.ext import commands


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")

LTC_ADDRESS = "LL8EdfmaSujikXjyM8Z9vieREQ57nV8YvJ"

AUTOBUY_PANEL_CHANNEL_ID = 1547960767821779094
BUY_TICKET_CATEGORY_ID = 1549730305361973310
STAFF_ROLE_ID = 1546208356610605286
LOG_CHANNEL_ID = 0

REQUIRED_CONFIRMATIONS = 1
PAYMENT_CHECK_INTERVAL = 10

DATA_DIR = "/app/data"
PRODUCTS_FILE = os.path.join(DATA_DIR, "products.json")
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")

LTC_API = "https://litecoinspace.org/api"

# ============================================================
# DISCORD INTENTS
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Supports both $ and !
bot = commands.Bot(
    command_prefix=["$", "!"],
    intents=intents,
    help_command=None
)


# ============================================================
# DATA
# ============================================================

os.makedirs(DATA_DIR, exist_ok=True)


def load_json(path, default):
    try:
        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


products = load_json(PRODUCTS_FILE, {})
orders = load_json(ORDERS_FILE, {})


# ============================================================
# HELPERS
# ============================================================

def money(value):
    return f"${float(value):.2f}"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def get_product(product_id):
    return products.get(str(product_id))


def get_order(order_id):
    return orders.get(str(order_id))


def save_data():
    save_json(PRODUCTS_FILE, products)
    save_json(ORDERS_FILE, orders)


def user_is_staff(member):
    if member.guild_permissions.administrator:
        return True

    role = member.guild.get_role(STAFF_ROLE_ID)

    if role and role in member.roles:
        return True

    return False


async def send_log(guild, message):
    if not LOG_CHANNEL_ID:
        return

    channel = guild.get_channel(LOG_CHANNEL_ID)

    if channel:
        try:
            await channel.send(message)
        except Exception:
            pass


# ============================================================
# LTC API
# ============================================================

async def ltc_get(path):
    url = LTC_API + path

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:

                if response.status != 200:
                    return None

                return await response.json()

    except Exception:
        return None


async def get_ltc_price():
    """
    Returns current LTC/USD price.
    Uses LitecoinSpace mempool endpoint if available.
    """

    # Try several common public endpoints.
    endpoints = [
        "https://api.coinbase.com/v2/prices/LTC-USD/spot",
        "https://api.kraken.com/0/public/Ticker?pair=LTCUSD"
    ]

    for url in endpoints:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:

                    if response.status != 200:
                        continue

                    data = await response.json()

                    # Coinbase
                    if "data" in data and "amount" in data["data"]:
                        return float(data["data"]["amount"])

                    # Kraken
                    result = data.get("result")

                    if result:
                        pair = next(iter(result.values()))
                        return float(pair["c"][0])

        except Exception:
            continue

    return None


async def usd_to_ltc(usd_amount):
    price = await get_ltc_price()

    if not price or price <= 0:
        return None, None

    ltc_amount = float(usd_amount) / price

    return ltc_amount, price


# ============================================================
# PAYMENT CHECKING
# ============================================================

async def get_address_transactions():
    data = await ltc_get(
        f"/address/{LTC_ADDRESS}/txs"
    )

    if not data:
        return []

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        if "txs" in data:
            return data["txs"]

        if "transactions" in data:
            return data["transactions"]

    return []


def extract_tx_id(tx):
    if not isinstance(tx, dict):
        return None

    return (
        tx.get("txid")
        or tx.get("tx_hash")
        or tx.get("hash")
        or tx.get("id")
    )


def extract_confirmations(tx):
    if not isinstance(tx, dict):
        return 0

    value = (
        tx.get("confirmations")
        or tx.get("confirmation")
        or 0
    )

    try:
        return int(value)
    except Exception:
        return 0


def extract_received_amount(tx):
    """
    Attempts to calculate the amount sent to our LTC address.
    """

    total = 0.0

    if not isinstance(tx, dict):
        return 0.0

    # Some APIs provide outputs.
    outputs = (
        tx.get("vout")
        or tx.get("outputs")
        or []
    )

    for output in outputs:
        try:
            addresses = []

            script_pub_key = output.get("scriptPubKey", {})

            if isinstance(script_pub_key, dict):
                addresses = (
                    script_pub_key.get("addresses")
                    or []
                )

            if output.get("address"):
                addresses.append(output["address"])

            if LTC_ADDRESS not in addresses:
                continue

            value = output.get("value", 0)

            # LitecoinSpace normally returns LTC amounts
            # in BTC/LTC decimal format.
            total += float(value)

        except Exception:
            continue

    # Some APIs have a direct amount field.
    if total <= 0:
        for key in [
            "amount",
            "value",
            "received",
            "total_received"
        ]:
            try:
                value = tx.get(key)

                if value is not None:
                    total = float(value)
                    break
            except Exception:
                pass

    return total


async def find_payment(order):
    """
    Looks for a transaction paying the order amount.

    Returns:
        {
            "txid": "...",
            "amount": 1.234,
            "confirmations": 1
        }

    or None.
    """

    transactions = await get_address_transactions()

    if not transactions:
        return None

    expected = float(order["ltc_amount"])

    # Small tolerance for floating-point calculations.
    tolerance = max(expected * 0.00001, 0.00000001)

    for tx in transactions:
        txid = extract_tx_id(tx)

        if not txid:
            continue

        amount = extract_received_amount(tx)

        if amount <= 0:
            continue

        if abs(amount - expected) <= tolerance:
            return {
                "txid": txid,
                "amount": amount,
                "confirmations": extract_confirmations(tx)
            }

        # Overpayment counts as payment too.
        if amount > expected:
            return {
                "txid": txid,
                "amount": amount,
                "confirmations": extract_confirmations(tx)
            }

    return None


# ============================================================
# PAYMENT MONITOR
# ============================================================

async def monitor_order(order_id):
    await asyncio.sleep(3)

    while True:

        order = get_order(order_id)

        if not order:
            return

        if order.get("status") in [
            "paid",
            "delivered",
            "cancelled"
        ]:
            return

        channel_id = order.get("channel_id")
        channel = bot.get_channel(channel_id)

        if not channel:
            return

        payment = await find_payment(order)

        if payment:

            txid = payment["txid"]
            received = payment["amount"]
            confirmations = payment["confirmations"]

            expected = float(order["ltc_amount"])

            # Don't process the same transaction repeatedly.
            if order.get("payment_txid") != txid:
                order["payment_txid"] = txid
                order["received_ltc"] = received
                save_data()

            # Underpayment
            if received + 0.00000001 < expected:

                try:
                    await channel.send(
                        embed=discord.Embed(
                            title="⚠️ Underpayment Detected",
                            description=(
                                f"**Required:** `{expected:.8f} LTC`\n"
                                f"**Received:** `{received:.8f} LTC`\n\n"
                                "Please send the remaining amount."
                            ),
                            color=discord.Color.orange()
                        )
                    )
                except Exception:
                    pass

                await asyncio.sleep(PAYMENT_CHECK_INTERVAL)
                continue

            # Payment exists but needs confirmations.
            if confirmations < REQUIRED_CONFIRMATIONS:

                try:
                    embed = discord.Embed(
                        title="⏳ Payment Detected",
                        description=(
                            f"**Received:** `{received:.8f} LTC`\n"
                            f"**Confirmations:** `{confirmations}/{REQUIRED_CONFIRMATIONS}`\n\n"
                            "Your payment is waiting for confirmation."
                        ),
                        color=discord.Color.orange()
                    )

                    embed.add_field(
                        name="Transaction",
                        value=f"`{txid}`",
                        inline=False
                    )

                    await channel.send(embed=embed)

                except Exception:
                    pass

                await asyncio.sleep(PAYMENT_CHECK_INTERVAL)
                continue

            # Payment confirmed.
            order["status"] = "paid"
            order["paid_at"] = utc_now()
            order["confirmations"] = confirmations
            save_data()

            try:
                embed = discord.Embed(
                    title="✅ Payment Confirmed",
                    description=(
                        f"Payment received successfully.\n\n"
                        f"**Received:** `{received:.8f} LTC`\n"
                        f"**Confirmations:** `{confirmations}`\n\n"
                        "Preparing your delivery..."
                    ),
                    color=discord.Color.green()
                )

                embed.add_field(
                    name="Transaction",
                    value=f"`{txid}`",
                    inline=False
                )

                await channel.send(embed=embed)

            except Exception:
                pass

            await deliver_order(order_id)

            return

        await asyncio.sleep(PAYMENT_CHECK_INTERVAL)


# ============================================================
# DELIVERY
# ============================================================

async def deliver_order(order_id):
    order = get_order(order_id)

    if not order:
        return

    # Duplicate delivery protection.
    if order.get("delivery_sent"):
        return

    product_id = str(order["product_id"])
    product = products.get(product_id)

    channel = bot.get_channel(order["channel_id"])

    if not product:
        if channel:
            await channel.send(
                "❌ This product no longer exists. Please contact staff."
            )

        return

    stock = product.get("stock", [])

    quantity = int(order["quantity"])

    if len(stock) < quantity:
        if channel:
            await channel.send(
                embed=discord.Embed(
                    title="❌ Insufficient Stock",
                    description=(
                        "Payment was confirmed, but there is not "
                        "enough stock available for automatic delivery.\n\n"
                        "Please contact staff."
                    ),
                    color=discord.Color.red()
                )
            )

        return

    # Remove stock.
    delivered_items = stock[:quantity]
    product["stock"] = stock[quantity:]

    # Mark delivery before sending to prevent duplicate attempts.
    order["delivery_sent"] = True
    order["delivered_items"] = delivered_items
    order["delivered_at"] = utc_now()
    order["status"] = "delivered"

    save_data()

    if not channel:
        return

    delivery_text = "\n".join(
        f"`{item}`" for item in delivered_items
    )

    embed = discord.Embed(
        title="📦 Order Delivered",
        description=(
            "Your order has been delivered successfully.\n\n"
            f"**Product:** {product.get('name', product_id)}\n"
            f"**Quantity:** `{quantity}`\n\n"
            f"**Your Items:**\n{delivery_text}"
        ),
        color=discord.Color.green()
    )

    embed.set_footer(
        text=f"Order ID: {order_id}"
    )

    try:
        await channel.send(
            content=f"<@{order['user_id']}>",
            embed=embed
        )
    except Exception:
        pass

    guild = channel.guild

    await send_log(
        guild,
        (
            f"📦 **AutoBuy Delivery**\n"
            f"User: <@{order['user_id']}>\n"
            f"Product: `{product_id}`\n"
            f"Quantity: `{quantity}`\n"
            f"Order: `{order_id}`"
        )
    )


# ============================================================
# PRODUCT SELECT
# ============================================================

class ProductSelect(discord.ui.Select):

    def __init__(self):
        options = []

        for product_id, product in products.items():

            stock = len(product.get("stock", []))

            if stock <= 0:
                continue

            options.append(
                discord.SelectOption(
                    label=product.get("name", product_id)[:100],
                    description=(
                        f"{money(product.get('price_usd', 0))} each • "
                        f"{stock} in stock"
                    )[:100],
                    value=str(product_id)
                )
            )

        if not options:
            options = [
                discord.SelectOption(
                    label="No products available",
                    value="none"
                )
            ]

        super().__init__(
            placeholder="Choose a product",
            options=options[:25],
            custom_id="autobuy_product_select"
        )

    async def callback(self, interaction: discord.Interaction):

        if self.values[0] == "none":
            await interaction.response.send_message(
                "❌ There are currently no products in stock.",
                ephemeral=True
            )
            return

        product_id = self.values[0]

        product = get_product(product_id)

        if not product:
            await interaction.response.send_message(
                "❌ Product not found.",
                ephemeral=True
            )
            return

        stock_count = len(product.get("stock", []))

        if stock_count <= 0:
            await interaction.response.send_message(
                "❌ This product is out of stock.",
                ephemeral=True
            )
            return

        min_quantity = max(
            1,
            int(product.get("min_quantity", 1))
        )

        max_quantity = min(
            int(product.get("max_quantity", stock_count)),
            stock_count
        )

        if max_quantity < min_quantity:
            max_quantity = min_quantity

        view = QuantityView(
            product_id,
            min_quantity,
            max_quantity
        )

        await interaction.response.send_message(
            embed=discord.Embed(
                title="🛒 Choose Quantity",
                description=(
                    f"**Product:** {product.get('name', product_id)}\n"
                    f"**Price:** {money(product.get('price_usd', 0))} each\n"
                    f"**Stock:** `{stock_count}`"
                ),
                color=discord.Color.blurple()
            ),
            view=view,
            ephemeral=True
        )


class ProductView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ProductSelect())


# ============================================================
# QUANTITY SELECT
# ============================================================

class QuantitySelect(discord.ui.Select):

    def __init__(self, product_id, minimum, maximum):

        self.product_id = product_id

        options = []

        for quantity in range(
            minimum,
            min(maximum, 25) + 1
        ):
            options.append(
                discord.SelectOption(
                    label=f"{quantity} item{'s' if quantity != 1 else ''}",
                    value=str(quantity)
                )
            )

        super().__init__(
            placeholder="Choose quantity",
            options=options,
            custom_id=f"autobuy_quantity_{product_id}"
        )

    async def callback(self, interaction: discord.Interaction):

        quantity = int(self.values[0])

        product = get_product(self.product_id)

        if not product:
            await interaction.response.send_message(
                "❌ Product no longer exists.",
                ephemeral=True
            )
            return

        stock = len(product.get("stock", []))

        if quantity > stock:
            await interaction.response.send_message(
                f"❌ Only `{stock}` items are in stock.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        category = guild.get_channel(BUY_TICKET_CATEGORY_ID)

        if not category:
            await interaction.response.send_message(
                "❌ AutoBuy ticket category was not found.",
                ephemeral=True
            )
            return

        # Prevent users from creating unlimited tickets.
        existing = None

        for channel in guild.text_channels:
            if not channel.name.startswith("autobuy-"):
                continue

            order = next(
                (
                    o for o in orders.values()
                    if o.get("channel_id") == channel.id
                    and o.get("user_id") == interaction.user.id
                    and o.get("status") not in [
                        "delivered",
                        "cancelled"
                    ]
                ),
                None
            )

            if order:
                existing = channel
                break

        if existing:
            await interaction.response.send_message(
                f"❌ You already have an AutoBuy ticket: {existing.mention}",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        price_usd = float(product.get("price_usd", 0))
        usd_total = price_usd * quantity

        ltc_amount, ltc_price = await usd_to_ltc(usd_total)

        if not ltc_amount:
            await interaction.followup.send(
                "❌ Unable to get the current LTC price. Please try again later.",
                ephemeral=True
            )
            return

        order_id = uuid.uuid4().hex[:10].upper()

        channel_name = (
            f"autobuy-{str(interaction.user.id)[-4:]}"
        )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False
            ),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )
        }

        staff_role = guild.get_role(STAFF_ROLE_ID)

        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            reason=f"AutoBuy order {order_id}"
        )

        order = {
            "order_id": order_id,
            "user_id": interaction.user.id,
            "guild_id": guild.id,
            "channel_id": channel.id,
            "product_id": self.product_id,
            "quantity": quantity,
            "price_usd_each": price_usd,
            "usd_total": usd_total,
            "ltc_amount": ltc_amount,
            "ltc_price": ltc_price,
            "ltc_address": LTC_ADDRESS,
            "status": "pending",
            "created_at": utc_now(),
            "payment_txid": None,
            "delivery_sent": False
        }

        orders[order_id] = order
        save_data()

        embed = discord.Embed(
            title="💳 AutoBuy Invoice",
            description=(
                "Send the **exact LTC amount** below to the address provided.\n\n"
                "The bot will automatically detect your payment."
            ),
            color=discord.Color.blurple()
        )

        embed.add_field(
            name="Product",
            value=product.get("name", self.product_id),
            inline=True
        )

        embed.add_field(
            name="Quantity",
            value=str(quantity),
            inline=True
        )

        embed.add_field(
            name="USD Total",
            value=money(usd_total),
            inline=True
        )

        embed.add_field(
            name="LTC Amount",
            value=f"`{ltc_amount:.8f} LTC`",
            inline=False
        )

        embed.add_field(
            name="LTC Address",
            value=f"`{LTC_ADDRESS}`",
            inline=False
        )

        embed.add_field(
            name="Order ID",
            value=f"`{order_id}`",
            inline=True
        )

        embed.add_field(
            name="Required Confirmations",
            value=str(REQUIRED_CONFIRMATIONS),
            inline=True
        )

        embed.set_footer(
            text="Do not close the ticket until your payment is detected."
        )

        await channel.send(
            content=interaction.user.mention,
            embed=embed,
            view=InvoiceButtons(order_id)
        )

        await interaction.followup.send(
            f"✅ Your AutoBuy ticket has been created: {channel.mention}",
            ephemeral=True
        )

        asyncio.create_task(
            monitor_order(order_id)
        )


class QuantityView(discord.ui.View):

    def __init__(self, product_id, minimum, maximum):
        super().__init__(timeout=120)
        self.add_item(
            QuantitySelect(
                product_id,
                minimum,
                maximum
            )
        )


# ============================================================
# INVOICE BUTTONS
# ============================================================

class InvoiceButtons(discord.ui.View):

    def __init__(self, order_id):
        super().__init__(timeout=None)

        self.order_id = order_id

    @discord.ui.button(
        label="Paste Details",
        emoji="📋",
        style=discord.ButtonStyle.secondary,
        custom_id="autobuy_paste_details"
    )
    async def paste_details(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        order = get_order(self.order_id)

        if not order:
            await interaction.response.send_message(
                "❌ Order not found.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="💳 Payment Details",
            description=(
                f"**LTC Address**\n"
                f"`{order['ltc_address']}`\n\n"
                f"**Amount**\n"
                f"`{float(order['ltc_amount']):.8f} LTC`\n\n"
                f"**USD Total**\n"
                f"`{money(order['usd_total'])}`"
            ),
            color=discord.Color.blurple()
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    @discord.ui.button(
        label="Show QR Code",
        emoji="🔳",
        style=discord.ButtonStyle.primary,
        custom_id="autobuy_qr_code"
    )
    async def qr_code(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        order = get_order(self.order_id)

        if not order:
            await interaction.response.send_message(
                "❌ Order not found.",
                ephemeral=True
            )
            return

        uri = (
            f"litecoin:{order['ltc_address']}"
            f"?amount={float(order['ltc_amount']):.8f}"
        )

        await interaction.response.send_message(
            embed=discord.Embed(
                title="🔳 Litecoin Payment",
                description=(
                    "Use the Litecoin URI below with a compatible wallet:\n\n"
                    f"`{uri}`"
                ),
                color=discord.Color.blurple()
            ),
            ephemeral=True
        )


# ============================================================
# TICKET CONTROLS
# ============================================================

class TicketControls(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="autobuy_close_ticket"
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not user_is_staff(interaction.user):
            await interaction.response.send_message(
                "❌ You do not have permission to close this ticket.",
                ephemeral=True
            )
            return

        channel = interaction.channel

        await interaction.response.send_message(
            "🔒 Closing this AutoBuy ticket in 3 seconds..."
        )

        await asyncio.sleep(3)

        try:
            await channel.delete(
                reason=f"AutoBuy ticket closed by {interaction.user}"
            )
        except Exception:
            pass


# ============================================================
# AUTOBUY PANEL
# ============================================================

class AutoBuyPanelView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open Auto Buy",
        emoji="🎟️",
        style=discord.ButtonStyle.primary,
        custom_id="autobuy_open"
    )
    async def open_autobuy(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not products:
            await interaction.response.send_message(
                "❌ There are currently no products available.",
                ephemeral=True
            )
            return

        in_stock = any(
            len(p.get("stock", [])) > 0
            for p in products.values()
        )

        if not in_stock:
            await interaction.response.send_message(
                "❌ Everything is currently out of stock.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🛒 AutoBuy",
            description=(
                "Select a product below to start your order.\n\n"
                "Payments are handled automatically through Litecoin."
            ),
            color=discord.Color.blurple()
        )

        await interaction.response.send_message(
            embed=embed,
            view=ProductView(),
            ephemeral=True
        )


# ============================================================
# $autobuy
# ============================================================

@bot.command(name="autobuy")
@commands.has_permissions(administrator=True)
async def autobuy_command(ctx):

    embed = discord.Embed(
        title="🛒 AutoBuy",
        description=(
            "Purchase products automatically using Litecoin.\n\n"
            "Click the button below to browse the available products."
        ),
        color=discord.Color.blurple()
    )

    embed.set_footer(
        text="AutoBuy • Litecoin Payments"
    )

    await ctx.send(
        embed=embed,
        view=AutoBuyPanelView()
    )


# ============================================================
# $add
# ============================================================

@bot.command(name="add")
@commands.has_permissions(administrator=True)
async def add_product(
    ctx,
    product_id: str,
    price_usd: float,
    min_quantity: int,
    max_quantity: int,
    *,
    name: str
):

    product_id = str(product_id).lower()

    if price_usd <= 0:
        await ctx.send(
            "❌ Price must be greater than `0`."
        )
        return

    if min_quantity < 1:
        await ctx.send(
            "❌ Minimum quantity must be at least `1`."
        )
        return

    if max_quantity < min_quantity:
        await ctx.send(
            "❌ Maximum quantity cannot be lower than minimum quantity."
        )
        return

    products[product_id] = {
        "name": name,
        "price_usd": price_usd,
        "min_quantity": min_quantity,
        "max_quantity": max_quantity,
        "stock": products.get(product_id, {}).get("stock", [])
    }

    save_data()

    await ctx.send(
        embed=discord.Embed(
            title="✅ Product Added",
            description=(
                f"**Product ID:** `{product_id}`\n"
                f"**Name:** {name}\n"
                f"**Price:** `{money(price_usd)}`\n"
                f"**Minimum:** `{min_quantity}`\n"
                f"**Maximum:** `{max_quantity}`\n"
                f"**Current Stock:** `{len(products[product_id]['stock'])}`"
            ),
            color=discord.Color.green()
        )
    )


# ============================================================
# $stock
# ============================================================

@bot.command(name="stock")
@commands.has_permissions(administrator=True)
async def add_stock(
    ctx,
    product_id: str,
    *,
    items: str
):

    product_id = str(product_id).lower()

    product = get_product(product_id)

    if not product:
        await ctx.send(
            f"❌ Product `{product_id}` does not exist."
        )
        return

    new_items = [
        line.strip()
        for line in items.splitlines()
        if line.strip()
    ]

    if not new_items:
        await ctx.send(
            "❌ No stock items were provided."
        )
        return

    product.setdefault("stock", [])
    product["stock"].extend(new_items)

    save_data()

    await ctx.send(
        embed=discord.Embed(
            title="📦 Stock Added",
            description=(
                f"**Product:** {product.get('name', product_id)}\n"
                f"**Added:** `{len(new_items)}`\n"
                f"**Total Stock:** `{len(product['stock'])}`"
            ),
            color=discord.Color.green()
        )
    )


# ============================================================
# $stockcount
# ============================================================

@bot.command(name="stockcount")
@commands.has_permissions(administrator=True)
async def stock_count(
    ctx,
    product_id: str
):

    product_id = str(product_id).lower()

    product = get_product(product_id)

    if not product:
        await ctx.send(
            f"❌ Product `{product_id}` does not exist."
        )
        return

    await ctx.send(
        f"📦 **{product.get('name', product_id)}** has "
        f"`{len(product.get('stock', []))}` items in stock."
    )


# ============================================================
# $clearstock
# ============================================================

@bot.command(name="clearstock")
@commands.has_permissions(administrator=True)
async def clear_stock(
    ctx,
    product_id: str
):

    product_id = str(product_id).lower()

    product = get_product(product_id)

    if not product:
        await ctx.send(
            f"❌ Product `{product_id}` does not exist."
        )
        return

    old_count = len(product.get("stock", []))

    product["stock"] = []

    save_data()

    await ctx.send(
        embed=discord.Embed(
            title="🗑️ Stock Cleared",
            description=(
                f"**Product:** {product.get('name', product_id)}\n"
                f"**Items Removed:** `{old_count}`\n\n"
                "The product itself was kept."
            ),
            color=discord.Color.orange()
        )
    )


# ============================================================
# $remove
# ============================================================

@bot.command(name="remove")
@commands.has_permissions(administrator=True)
async def remove_product(
    ctx,
    product_id: str
):

    product_id = str(product_id).lower()

    product = get_product(product_id)

    if not product:
        await ctx.send(
            f"❌ Product `{product_id}` does not exist."
        )
        return

    product_name = product.get(
        "name",
        product_id
    )

    stock_count = len(
        product.get("stock", [])
    )

    del products[product_id]

    save_data()

    await ctx.send(
        embed=discord.Embed(
            title="🗑️ Product Removed",
            description=(
                f"**Product:** {product_name}\n"
                f"**ID:** `{product_id}`\n"
                f"**Stock Removed:** `{stock_count}`\n\n"
                "The product has been completely deleted."
            ),
            color=discord.Color.red()
        )
    )


# ============================================================
# $products
# ============================================================

@bot.command(name="products")
@commands.has_permissions(administrator=True)
async def products_command(ctx):

    if not products:
        await ctx.send(
            "❌ No products have been added."
        )
        return

    embed = discord.Embed(
        title="📦 Products",
        color=discord.Color.blurple()
    )

    for product_id, product in products.items():

        stock_count = len(
            product.get("stock", [])
        )

        embed.add_field(
            name=product.get("name", product_id),
            value=(
                f"**ID:** `{product_id}`\n"
                f"**Price:** `{money(product.get('price_usd', 0))}`\n"
                f"**Stock:** `{stock_count}`\n"
                f"**Quantity:** "
                f"`{product.get('min_quantity', 1)}"
                f"-{product.get('max_quantity', 1)}`"
            ),
            inline=False
        )

    await ctx.send(embed=embed)


# ============================================================
# !delete / $delete
# DELETE ALL AUTOBUY TICKETS
# ============================================================

@bot.command(name="delete")
@commands.has_permissions(administrator=True)
async def delete_all_autobuy_tickets(ctx):

    guild = ctx.guild

    if guild is None:
        await ctx.send(
            "❌ This command can only be used inside a server."
        )
        return

    target_channels = []

    # --------------------------------------------------------
    # Find channels by AutoBuy name/category.
    # --------------------------------------------------------

    for channel in guild.text_channels:

        if channel.name.startswith("autobuy-"):

            if (
                channel.category_id == BUY_TICKET_CATEGORY_ID
                or channel.category_id is None
            ):
                target_channels.append(channel)

    # --------------------------------------------------------
    # Also find channels referenced by orders.
    # --------------------------------------------------------

    tracked_channel_ids = {
        order.get("channel_id")
        for order in orders.values()
        if order.get("guild_id") == guild.id
        and order.get("channel_id")
    }

    for channel_id in tracked_channel_ids:

        channel = guild.get_channel(channel_id)

        if channel and channel not in target_channels:
            target_channels.append(channel)

    # --------------------------------------------------------
    # Remove duplicates.
    # --------------------------------------------------------

    unique_channels = {}

    for channel in target_channels:
        unique_channels[channel.id] = channel

    target_channels = list(
        unique_channels.values()
    )

    if not target_channels:
        await ctx.send(
            "❌ There are no AutoBuy tickets to delete."
        )
        return

    # Send confirmation BEFORE deletion.
    try:
        await ctx.send(
            f"🗑️ Deleting **{len(target_channels)}** AutoBuy ticket(s)..."
        )
    except Exception:
        pass

    # --------------------------------------------------------
    # Mark matching orders as cancelled.
    # --------------------------------------------------------

    target_ids = {
        channel.id
        for channel in target_channels
    }

    for order in orders.values():

        if (
            order.get("guild_id") == guild.id
            and order.get("channel_id") in target_ids
        ):
            order["status"] = "cancelled"
            order["cancelled_at"] = utc_now()
            order["cancelled_by"] = ctx.author.id

    save_data()

    # --------------------------------------------------------
    # Delete channels.
    # --------------------------------------------------------

    deleted = 0
    failed = 0

    for channel in target_channels:

        try:
            await channel.delete(
                reason=f"All AutoBuy tickets deleted by {ctx.author}"
            )

            deleted += 1

        except discord.Forbidden:
            failed += 1

        except discord.HTTPException:
            failed += 1

        await asyncio.sleep(0.5)

    # If the command channel itself was deleted,
    # sending this won't work, so catch the exception.
    try:

        await ctx.send(
            embed=discord.Embed(
                title="🗑️ AutoBuy Tickets Deleted",
                description=(
                    f"**Deleted:** `{deleted}`\n"
                    f"**Failed:** `{failed}`"
                ),
                color=discord.Color.green()
            )
        )

    except Exception:
        pass


# ============================================================
# $ltctest
# ============================================================

@bot.command(name="ltctest")
@commands.has_permissions(administrator=True)
async def ltc_test(ctx):

    await ctx.send(
        "⏳ Checking Litecoin API..."
    )

    price = await get_ltc_price()

    if not price:
        await ctx.send(
            "❌ Unable to retrieve the current LTC/USD price."
        )
        return

    embed = discord.Embed(
        title="🟢 Litecoin Test",
        description=(
            f"**LTC/USD:** `${price:,.2f}`\n"
            f"**Address:** `{LTC_ADDRESS}`\n"
            f"**API:** Online"
        ),
        color=discord.Color.green()
    )

    await ctx.send(embed=embed)


# ============================================================
# COMMAND ERROR HANDLER
# ============================================================

@bot.event
async def on_command_error(ctx, error):

    if isinstance(
        error,
        commands.CommandNotFound
    ):
        return

    if isinstance(
        error,
        commands.MissingPermissions
    ):
        await ctx.send(
            "❌ You need administrator permissions to use this command."
        )
        return

    if isinstance(
        error,
        commands.MissingRequiredArgument
    ):
        await ctx.send(
            f"❌ Missing argument: `{error.param.name}`"
        )
        return

    if isinstance(
        error,
        commands.BadArgument
    ):
        await ctx.send(
            "❌ Invalid argument. Check the command format."
        )
        return

    print(
        f"Command error: {repr(error)}"
    )


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():

    print(
        f"✅ Logged in as {bot.user} "
        f"(ID: {bot.user.id})"
    )

    print(
        f"📦 Loaded {len(products)} products"
    )

    print(
        f"🧾 Loaded {len(orders)} orders"
    )

    # Register persistent views.
    bot.add_view(
        AutoBuyPanelView()
    )

    @bot.command(name="clean")
@commands.has_permissions(manage_channels=True)
async def clean(ctx):
    channel = ctx.channel

    await channel.delete(reason=f"Cleaned by {ctx.author}")

# ============================================================
# RUN
# ============================================================

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN environment variable is missing."
    )

bot.run(TOKEN)
