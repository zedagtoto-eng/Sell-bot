import os
import json
import time
import asyncio
import secrets
from decimal import Decimal, ROUND_DOWN

import aiohttp
import discord
from discord.ext import commands, tasks


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")

# YOUR LTC RECEIVING ADDRESS
LTC_ADDRESS = os.getenv(
    "LTC_ADDRESS",
    "LL8EdfmaSujikXjyM8Z9vieREQ57nV8YvJ"
)

# Discord IDs
AUTOBUY_PANEL_CHANNEL_ID = int(
    os.getenv("AUTOBUY_PANEL_CHANNEL_ID", "0")
)

BUY_TICKET_CATEGORY_ID = int(
    os.getenv("BUY_TICKET_CATEGORY_ID", "0")
)

STAFF_ROLE_ID = int(
    os.getenv("STAFF_ROLE_ID", "0")
)

LOG_CHANNEL_ID = int(
    os.getenv("LOG_CHANNEL_ID", "0")
)

# Payment settings
REQUIRED_CONFIRMATIONS = int(
    os.getenv("REQUIRED_CONFIRMATIONS", "1")
)

PAYMENT_CHECK_SECONDS = 10

ORDER_EXPIRY_SECONDS = 30 * 60

# Litecoin Space API
LTC_API = "https://litecoinspace.org/api"

# Railway persistent storage
DATA_DIR = "/app/data"

PRODUCTS_FILE = f"{DATA_DIR}/products.json"
ORDERS_FILE = f"{DATA_DIR}/orders.json"


# ============================================================
# DISCORD
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="$",
    intents=intents,
    help_command=None
)


# ============================================================
# STORAGE
# ============================================================

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_json(path, default):
    ensure_data_dir()

    if not os.path.exists(path):
        save_json(path, default)
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        print(f"JSON LOAD ERROR {path}: {e}")
        return default


def save_json(path, data):
    ensure_data_dir()

    temp = path + ".tmp"

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=4,
            ensure_ascii=False
        )

    os.replace(temp, path)


products = load_json(
    PRODUCTS_FILE,
    {
        "example_product": {
            "name": "Example Product",
            "emoji": "📦",
            "price_usd": "1.00",
            "min_quantity": 1,
            "max_quantity": 100,
            "stock": []
        }
    }
)

orders = load_json(
    ORDERS_FILE,
    {}
)


# ============================================================
# DECIMAL HELPERS
# ============================================================

def ltc8(value):
    return Decimal(str(value)).quantize(
        Decimal("0.00000001"),
        rounding=ROUND_DOWN
    )


def usd8(value):
    return Decimal(str(value)).quantize(
        Decimal("0.00000001"),
        rounding=ROUND_DOWN
    )


# ============================================================
# HTTP
# ============================================================

async def api_get(endpoint):
    url = LTC_API + endpoint

    timeout = aiohttp.ClientTimeout(
        total=15
    )

    try:
        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.get(url) as response:

                if response.status != 200:
                    text = await response.text()

                    print(
                        f"LTC API ERROR "
                        f"{response.status}: "
                        f"{text[:300]}"
                    )

                    return None

                return await response.json()

    except Exception as e:

        print(
            "LTC API CONNECTION ERROR:",
            e
        )

        return None


# ============================================================
# LTC ADDRESS VALIDATION
# ============================================================

async def validate_ltc_address():

    if LTC_ADDRESS == "PUT_YOUR_LTC_ADDRESS_HERE":
        print(
            "WARNING: LTC_ADDRESS has not been configured."
        )
        return False

    data = await api_get(
        f"/v1/validate-address/{LTC_ADDRESS}"
    )

    if not data:
        print(
            "Could not validate LTC address."
        )
        return False

    valid = data.get(
        "isvalid",
        False
    )

    if not valid:
        print(
            "WARNING: LTC_ADDRESS appears invalid."
        )
        return False

    print(
        "LTC address validated successfully."
    )

    return True


# ============================================================
# LTC PRICE
# ============================================================

async def get_ltc_usd_price():

    data = await api_get(
        "/v1/prices"
    )

    if not data:
        return None

    price = data.get(
        "USD"
    )

    if price is None:
        return None

    try:
        return Decimal(
            str(price)
        )

    except Exception:
        return None


# ============================================================
# LTC TRANSACTION HELPERS
# ============================================================

def tx_received_amount(tx):
    """
    Returns total LTC sent TO LTC_ADDRESS.

    Litecoin Space returns output values in litoshis.
    1 LTC = 100,000,000 litoshis.
    """

    total_litoshis = 0

    for output in tx.get(
        "vout",
        []
    ):

        if not isinstance(
            output,
            dict
        ):
            continue

        script = output.get(
            "scriptpubkey",
            {}
        )

        if not isinstance(
            script,
            dict
        ):
            continue

        destination = script.get(
            "scriptpubkey_address"
        )

        if destination != LTC_ADDRESS:
            continue

        value = output.get(
            "value",
            0
        )

        try:
            total_litoshis += int(
                value
            )

        except Exception:
            continue

    return ltc8(
        Decimal(total_litoshis)
        / Decimal(100_000_000)
    )


def tx_confirmed(tx):

    status = tx.get(
        "status",
        {}
    )

    if not isinstance(
        status,
        dict
    ):
        return False

    return bool(
        status.get(
            "confirmed",
            False
        )
    )


def tx_confirmations(
    tx,
    tip_height
):

    status = tx.get(
        "status",
        {}
    )

    if not isinstance(
        status,
        dict
    ):
        return 0

    if not status.get(
        "confirmed",
        False
    ):
        return 0

    block_height = status.get(
        "block_height"
    )

    if block_height is None:
        return 0

    try:

        confirmations = (
            int(tip_height)
            - int(block_height)
            + 1
        )

        return max(
            0,
            confirmations
        )

    except Exception:
        return 0


# ============================================================
# GET PENDING TRANSACTIONS
# ============================================================

async def get_pending_transactions():

    data = await api_get(
        f"/address/{LTC_ADDRESS}/txs/mempool"
    )

    if not isinstance(
        data,
        list
    ):
        return []

    return data


# ============================================================
# GET ADDRESS TRANSACTIONS
# ============================================================

async def get_address_transactions():

    data = await api_get(
        f"/address/{LTC_ADDRESS}/txs"
    )

    if not isinstance(
        data,
        list
    ):
        return []

    return data


# ============================================================
# GET TX
# ============================================================

async def get_transaction(
    txid
):

    data = await api_get(
        f"/tx/{txid}"
    )

    if not isinstance(
        data,
        dict
    ):
        return None

    return data


# ============================================================
# BLOCK TIP
# ============================================================

async def get_tip_height():

    data = await api_get(
        "/blocks/tip/height"
    )

    if data is None:
        return None

    try:
        return int(data)

    except Exception:
        return None


# ============================================================
# CHANNEL HELPERS
# ============================================================

async def get_channel(channel_id):

    if not channel_id:
        return None

    channel = bot.get_channel(
        int(channel_id)
    )

    if channel:
        return channel

    try:
        return await bot.fetch_channel(
            int(channel_id)
        )

    except Exception:
        return None


async def send_log(message):

    channel = await get_channel(
        LOG_CHANNEL_ID
    )

    if channel:

        try:
            await channel.send(
                message
            )

        except Exception:
            pass


# ============================================================
# PAYMENT INVOICE EMBED
# ============================================================

def invoice_embed(
    order
):

    embed = discord.Embed(
        title="💸 Payment Invoice",
        description=(
            "Please complete the payment using "
            "the details below.\n\n"
            "Once the payment is confirmed, "
            "your product will be delivered automatically."
        ),
        color=discord.Color.blue()
    )

    embed.add_field(
        name="🌐 LTC Wallet Address",
        value=f"`{LTC_ADDRESS}`",
        inline=False
    )

    embed.add_field(
        name="💰 Amount to Pay (LTC)",
        value=f"`{order['ltc_required']} LTC`",
        inline=False
    )

    embed.add_field(
        name="💵 Equivalent in USD",
        value=f"${order['total_usd']}",
        inline=False
    )

    embed.add_field(
        name="⚠️ Important",
        value=(
            "Send the exact amount shown above.\n\n"
            "Payments are irreversible.\n\n"
            "Your payment will first appear as "
            "**TRANSACTION DETECTED** while pending."
        ),
        inline=False
    )

    return embed


# ============================================================
# DETECTED EMBED
# ============================================================

def detected_embed(
    order
):

    embed = discord.Embed(
        title="🔵 TRANSACTION DETECTED",
        description=(
            "Your LTC transaction has been detected "
            "on the network.\n\n"
            "The transaction is currently pending. "
            "Your items will **not** be delivered "
            "until the required confirmation is reached."
        ),
        color=discord.Color.blue()
    )

    embed.add_field(
        name="🔗 Transaction",
        value=f"`{order['txid']}`",
        inline=False
    )

    embed.add_field(
        name="💰 Amount Received",
        value=f"`{order['received_ltc']} LTC`",
        inline=True
    )

    embed.add_field(
        name="⏳ Confirmations",
        value=(
            f"`{order.get('confirmations', 0)}`"
            f" / `{REQUIRED_CONFIRMATIONS}`"
        ),
        inline=True
    )

    return embed


# ============================================================
# CONFIRMED EMBED
# ============================================================

def confirmed_embed(
    order
):

    embed = discord.Embed(
        title="🟢 PAYMENT CONFIRMED",
        description=(
            "Your LTC payment has been confirmed.\n\n"
            "🎁 Your order is now being delivered."
        ),
        color=discord.Color.green()
    )

    embed.add_field(
        name="🔗 Transaction",
        value=f"`{order['txid']}`",
        inline=False
    )

    embed.add_field(
        name="💰 Paid",
        value=f"`{order['received_ltc']} LTC`",
        inline=True
    )

    embed.add_field(
        name="✅ Confirmations",
        value=f"`{order['confirmations']}`",
        inline=True
    )

    return embed


# ============================================================
# UNDERPAYMENT EMBED
# ============================================================

def underpayment_embed(
    order
):

    required = Decimal(
        str(order["ltc_required"])
    )

    received = Decimal(
        str(order["received_ltc"])
    )

    remaining = ltc8(
        required - received
    )

    embed = discord.Embed(
        title="⚠️ INCORRECT PAYMENT",
        description=(
            "A transaction was detected, but the "
            "amount received is below the required amount.\n\n"
            "**Your order has NOT been delivered.**"
        ),
        color=discord.Color.orange()
    )

    embed.add_field(
        name="Required",
        value=f"`{required} LTC`",
        inline=True
    )

    embed.add_field(
        name="Received",
        value=f"`{received} LTC`",
        inline=True
    )

    embed.add_field(
        name="Remaining",
        value=f"`{remaining} LTC`",
        inline=False
    )

    embed.add_field(
        name="Transaction",
        value=f"`{order['txid']}`",
        inline=False
    )

    return embed


# ============================================================
# OVERPAYMENT EMBED
# ============================================================

def overpayment_embed(
    order
):

    embed = discord.Embed(
        title="⚠️ OVERPAYMENT",
        description=(
            "A payment greater than the invoice "
            "amount was detected.\n\n"
            "The order has **NOT** been automatically "
            "delivered.\n\n"
            "Please contact staff."
        ),
        color=discord.Color.orange()
    )

    embed.add_field(
        name="Required",
        value=f"`{order['ltc_required']} LTC`",
        inline=True
    )

    embed.add_field(
        name="Received",
        value=f"`{order['received_ltc']} LTC`",
        inline=True
    )

    embed.add_field(
        name="Transaction",
        value=f"`{order['txid']}`",
        inline=False
    )

    return embed


# ============================================================
# PAYMENT BUTTONS
# ============================================================

class PaymentView(
    discord.ui.View
):

    def __init__(
        self,
        order_id
    ):

        super().__init__(
            timeout=None
        )

        self.order_id = order_id

    @discord.ui.button(
        label="📜 Paste Details",
        style=discord.ButtonStyle.primary,
        custom_id="autobuy_paste"
    )
    async def paste_details(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        order = orders.get(
            self.order_id
        )

        if not order:
            return await interaction.response.send_message(
                "❌ Order not found.",
                ephemeral=True
            )

        await interaction.response.send_message(
            (
                "**LTC Payment Details**\n\n"
                "🌐 Address:\n"
                f"`{LTC_ADDRESS}`\n\n"
                "💰 Amount:\n"
                f"`{order['ltc_required']} LTC`"
            ),
            ephemeral=True
        )

    @discord.ui.button(
        label="📷 Show QR Code",
        style=discord.ButtonStyle.success,
        custom_id="autobuy_qr"
    )
    async def show_qr(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        order = orders.get(
            self.order_id
        )

        if not order:
            return await interaction.response.send_message(
                "❌ Order not found.",
                ephemeral=True
            )

        uri = (
            f"litecoin:{LTC_ADDRESS}"
            f"?amount={order['ltc_required']}"
        )

        await interaction.response.send_message(
            (
                "📷 **LTC Payment URI**\n\n"
                f"`{uri}`\n\n"
                "Your Litecoin wallet can use this "
                "URI to generate the payment QR."
            ),
            ephemeral=True
        )


# ============================================================
# DELIVER STOCK
# ============================================================

async def deliver_order(
    order
):

    channel = await get_channel(
        order["channel_id"]
    )

    if not channel:
        return False

    product = products.get(
        order["product_id"]
    )

    if not product:
        await channel.send(
            "❌ Product no longer exists. Contact staff."
        )
        return False

    quantity = int(
        order["quantity"]
    )

    stock = product.get(
        "stock",
        []
    )

    if len(stock) < quantity:

        await channel.send(
            (
                "⚠️ **PAYMENT CONFIRMED**\n\n"
                "There is currently not enough stock "
                "to fulfill your order.\n\n"
                "Please contact staff."
            )
        )

        await send_log(
            f"⚠️ **OUT OF STOCK**\n"
            f"Order: `{order['id']}`\n"
            f"User: <@{order['user_id']}>\n"
            f"Product: `{product['name']}`\n"
            f"Quantity: `{quantity}`"
        )

        return False

    delivered = stock[:quantity]

    product["stock"] = stock[
        quantity:
    ]

    save_json(
        PRODUCTS_FILE,
        products
    )

    order["status"] = "delivered"
    order["delivered"] = True

    orders[order["id"]] = order

    save_json(
        ORDERS_FILE,
        orders
    )

    items = "\n".join(
        f"`{item}`"
        for item in delivered
    )

    embed = discord.Embed(
        title="🎁 ORDER DELIVERED",
        description=(
            f"📦 **Product:** "
            f"{product['name']}\n"
            f"🔢 **Quantity:** "
            f"{quantity}\n\n"
            f"**Your items:**\n"
            f"{items}"
        ),
        color=discord.Color.green()
    )

    await channel.send(
        content=f"<@{order['user_id']}>",
        embed=embed
    )

    await send_log(
        f"🎁 **ORDER DELIVERED**\n"
        f"Order: `{order['id']}`\n"
        f"User: <@{order['user_id']}>\n"
        f"Product: `{product['name']}`\n"
        f"Quantity: `{quantity}`\n"
        f"TX: `{order.get('txid', 'unknown')}`"
    )

    return True


# ============================================================
# FIND PAYMENT
# ============================================================

async def find_payment(
    order
):

    # --------------------------------------------------------
    # FIRST: MEMPOOL
    # --------------------------------------------------------

    pending = await get_pending_transactions()

    for tx in pending:

        txid = tx.get(
            "txid"
        )

        if not txid:
            continue

        if txid in order.get(
            "seen_txids",
            []
        ):
            continue

        amount = tx_received_amount(
            tx
        )

        if amount <= 0:
            continue

        return {
            "txid": txid,
            "amount": amount,
            "confirmed": False,
            "confirmations": 0
        }

    # --------------------------------------------------------
    # SECOND: CONFIRMED / ADDRESS HISTORY
    # --------------------------------------------------------

    transactions = await get_address_transactions()

    for tx in transactions:

        txid = tx.get(
            "txid"
        )

        if not txid:
            continue

        if txid in order.get(
            "seen_txids",
            []
        ):
            # Still check an already-detected TX later
            # through get_transaction().
            if txid != order.get(
                "txid"
            ):
                continue

        amount = tx_received_amount(
            tx
        )

        if amount <= 0:
            continue

        if tx_confirmed(tx):

            tip = await get_tip_height()

            confirmations = 0

            if tip is not None:
                confirmations = tx_confirmations(
                    tx,
                    tip
                )

            return {
                "txid": txid,
                "amount": amount,
                "confirmed": True,
                "confirmations": confirmations
            }

    return None


# ============================================================
# MONITOR EXISTING TRANSACTION
# ============================================================

async def update_existing_payment(
    order
):

    txid = order.get(
        "txid"
    )

    if not txid:
        return None

    tx = await get_transaction(
        txid
    )

    if not tx:
        return None

    amount = tx_received_amount(
        tx
    )

    if amount <= 0:
        return None

    tip = await get_tip_height()

    confirmations = 0

    if tip is not None:
        confirmations = tx_confirmations(
            tx,
            tip
        )

    return {
        "txid": txid,
        "amount": amount,
        "confirmed": tx_confirmed(tx),
        "confirmations": confirmations
    }


# ============================================================
# EDIT PAYMENT MESSAGE
# ============================================================

async def edit_payment_message(
    order,
    embed,
    view=None
):

    channel = await get_channel(
        order["channel_id"]
    )

    if not channel:
        return

    message_id = order.get(
        "invoice_message_id"
    )

    if not message_id:
        return

    try:

        message = await channel.fetch_message(
            int(message_id)
        )

        await message.edit(
            embed=embed,
            view=view
        )

    except Exception as e:

        print(
            "MESSAGE EDIT ERROR:",
            e
        )


# ============================================================
# PAYMENT MONITOR
# ============================================================

@tasks.loop(
    seconds=PAYMENT_CHECK_SECONDS
)
async def payment_monitor():

    if not orders:
        return

    for order_id, order in list(
        orders.items()
    ):

        try:

            status = order.get(
                "status"
            )

            if status in (
                "delivered",
                "overpaid",
                "expired",
                "cancelled"
            ):
                continue

            # ------------------------------------------------
            # EXPIRATION
            # ------------------------------------------------

            created_at = float(
                order.get(
                    "created_at",
                    time.time()
                )
            )

            if (
                time.time() - created_at
                > ORDER_EXPIRY_SECONDS
            ):

                order["status"] = "expired"

                orders[order_id] = order

                save_json(
                    ORDERS_FILE,
                    orders
                )

                continue

            # ------------------------------------------------
            # IF WE ALREADY KNOW TX, CHECK IT DIRECTLY
            # ------------------------------------------------

            if order.get("txid"):

                payment = await update_existing_payment(
                    order
                )

            else:

                payment = await find_payment(
                    order
                )

            if not payment:
                continue

            txid = payment["txid"]

            amount = ltc8(
                payment["amount"]
            )

            confirmations = int(
                payment.get(
                    "confirmations",
                    0
                )
            )

            required = ltc8(
                order["ltc_required"]
            )

            # Save TX
            order["txid"] = txid
            order["received_ltc"] = str(
                amount
            )
            order["confirmations"] = confirmations

            seen = order.setdefault(
                "seen_txids",
                []
            )

            if txid not in seen:
                seen.append(txid)

            # ------------------------------------------------
            # UNDERPAYMENT
            # ------------------------------------------------

            if amount < required:

                order["status"] = "underpaid"

                orders[order_id] = order

                save_json(
                    ORDERS_FILE,
                    orders
                )

                await edit_payment_message(
                    order,
                    underpayment_embed(order),
                    PaymentView(order_id)
                )

                continue

            # ------------------------------------------------
            # OVERPAYMENT
            # ------------------------------------------------

            if amount > required:

                order["status"] = "overpaid"

                orders[order_id] = order

                save_json(
                    ORDERS_FILE,
                    orders
                )

                await edit_payment_message(
                    order,
                    overpayment_embed(order)
                )

                await send_log(
                    f"⚠️ **OVERPAYMENT**\n"
                    f"Order: `{order_id}`\n"
                    f"User: <@{order['user_id']}>\n"
                    f"Required: `{required} LTC`\n"
                    f"Received: `{amount} LTC`\n"
                    f"TX: `{txid}`"
                )

                continue

            # ------------------------------------------------
            # EXACT PAYMENT
            # ------------------------------------------------

            if not payment["confirmed"]:

                if status != "detected":

                    order["status"] = "detected"

                    orders[order_id] = order

                    save_json(
                        ORDERS_FILE,
                        orders
                    )

                    await edit_payment_message(
                        order,
                        detected_embed(order)
                    )

                    await send_log(
                        f"🔵 **TRANSACTION DETECTED**\n"
                        f"Order: `{order_id}`\n"
                        f"User: <@{order['user_id']}>\n"
                        f"Amount: `{amount} LTC`\n"
                        f"TX: `{txid}`"
                    )

                continue

            # ------------------------------------------------
            # CONFIRMED BUT NOT ENOUGH CONFIRMATIONS
            # ------------------------------------------------

            if confirmations < REQUIRED_CONFIRMATIONS:

                order["status"] = "detected"

                orders[order_id] = order

                save_json(
                    ORDERS_FILE,
                    orders
                )

                await edit_payment_message(
                    order,
                    detected_embed(order)
                )

                continue

            # ------------------------------------------------
            # FULLY CONFIRMED
            # ------------------------------------------------

            if order.get(
                "status"
            ) != "confirmed":

                order["status"] = "confirmed"

                orders[order_id] = order

                save_json(
                    ORDERS_FILE,
                    orders
                )

                await edit_payment_message(
                    order,
                    confirmed_embed(order)
                )

                await send_log(
                    f"🟢 **PAYMENT CONFIRMED**\n"
                    f"Order: `{order_id}`\n"
                    f"User: <@{order['user_id']}>\n"
                    f"Amount: `{amount} LTC`\n"
                    f"Confirmations: `{confirmations}`\n"
                    f"TX: `{txid}`"
                )

                await asyncio.sleep(2)

                await deliver_order(
                    order
                )

        except Exception as e:

            print(
                f"PAYMENT MONITOR ERROR "
                f"{order_id}:",
                e
            )


# ============================================================
# PRODUCT SELECT
# ============================================================

class ProductSelect(
    discord.ui.Select
):

    def __init__(self):

        options = []

        for product_id, product in products.items():

            stock_count = len(
                product.get(
                    "stock",
                    []
                )
            )

            options.append(
                discord.SelectOption(
                    label=product["name"][:100],
                    value=product_id,
                    emoji=product.get(
                        "emoji",
                        "📦"
                    ),
                    description=(
                        f"${product['price_usd']} each "
                        f"• Stock: {stock_count}"
                    )[:100]
                )
            )

        if not options:

            options.append(
                discord.SelectOption(
                    label="No products available",
                    value="none"
                )
            )

        super().__init__(
            placeholder="🛍️ Select a product...",
            options=options[:25]
        )

    async def callback(
        self,
        interaction: discord.Interaction
    ):

        product_id = self.values[0]

        if product_id == "none":

            return await interaction.response.send_message(
                "❌ No products are currently available.",
                ephemeral=True
            )

        product = products.get(
            product_id
        )

        if not product:

            return await interaction.response.send_message(
                "❌ Product not found.",
                ephemeral=True
            )

        stock_count = len(
            product.get(
                "stock",
                []
            )
        )

        embed = discord.Embed(
            title="✅ Product Ready",
            color=discord.Color.from_rgb(
                217,
                232,
                74
            )
        )

        embed.add_field(
            name="📦 Product",
            value=product["name"],
            inline=False
        )

        embed.add_field(
            name="💵 Price",
            value=f"${product['price_usd']}",
            inline=True
        )

        embed.add_field(
            name="📦 Stock",
            value=str(stock_count),
            inline=True
        )

        embed.add_field(
            name="🔢 Quantity",
            value=(
                f"{product['min_quantity']} - "
                f"{product['max_quantity']}"
            ),
            inline=True
        )

        await interaction.response.send_message(
            embed=embed,
            view=ProductReadyView(
                product_id
            ),
            ephemeral=True
        )


class ProductSelectView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=300
        )

        self.add_item(
            ProductSelect()
        )


# ============================================================
# PRODUCT READY
# ============================================================

class ProductReadyView(
    discord.ui.View
):

    def __init__(
        self,
        product_id
    ):

        super().__init__(
            timeout=300
        )

        self.product_id = product_id

    @discord.ui.button(
        label="🛒 Fill Purchase Details",
        style=discord.ButtonStyle.success
    )
    async def purchase(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.send_modal(
            QuantityModal(
                self.product_id
            )
        )

    @discord.ui.button(
        label="🔄 Change Product",
        style=discord.ButtonStyle.primary
    )
    async def change(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.send_message(
            "🛍️ Select another product:",
            view=ProductSelectView(),
            ephemeral=True
        )


# ============================================================
# QUANTITY MODAL
# ============================================================

class QuantityModal(
    discord.ui.Modal,
    title="🛒 Purchase Details"
):

    quantity = discord.ui.TextInput(
        label="Quantity",
        placeholder="Enter quantity",
        required=True,
        max_length=10
    )

    def __init__(
        self,
        product_id
    ):

        super().__init__()

        self.product_id = product_id

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        product = products.get(
            self.product_id
        )

        if not product:

            return await interaction.response.send_message(
                "❌ Product unavailable.",
                ephemeral=True
            )

        try:

            quantity = int(
                self.quantity.value.strip()
            )

        except ValueError:

            return await interaction.response.send_message(
                "❌ Quantity must be a whole number.",
                ephemeral=True
            )

        minimum = int(
            product["min_quantity"]
        )

        maximum = int(
            product["max_quantity"]
        )

        stock = len(
            product.get(
                "stock",
                []
            )
        )

        if quantity < minimum:

            return await interaction.response.send_message(
                f"❌ Minimum quantity is **{minimum}**.",
                ephemeral=True
            )

        if quantity > maximum:

            return await interaction.response.send_message(
                f"❌ Maximum quantity is **{maximum}**.",
                ephemeral=True
            )

        if quantity > stock:

            return await interaction.response.send_message(
                (
                    "❌ Not enough stock.\n\n"
                    f"Available: **{stock}**\n"
                    f"Requested: **{quantity}**"
                ),
                ephemeral=True
            )

        unit_price = Decimal(
            str(product["price_usd"])
        )

        total_usd = usd8(
            unit_price
            * Decimal(quantity)
        )

        embed = discord.Embed(
            title="🛒 Purchase Summary",
            color=discord.Color.from_rgb(
                217,
                232,
                74
            )
        )

        embed.add_field(
            name="📦 Product",
            value=product["name"],
            inline=False
        )

        embed.add_field(
            name="🔢 Quantity",
            value=str(quantity),
            inline=True
        )

        embed.add_field(
            name="💵 Unit Price",
            value=f"${unit_price}",
            inline=True
        )

        embed.add_field(
            name="🧮 Total",
            value=f"${total_usd}",
            inline=True
        )

        await interaction.response.send_message(
            embed=embed,
            view=PurchaseSummaryView(
                self.product_id,
                quantity,
                total_usd
            ),
            ephemeral=True
        )


# ============================================================
# PURCHASE SUMMARY
# ============================================================

class PurchaseSummaryView(
    discord.ui.View
):

    def __init__(
        self,
        product_id,
        quantity,
        total_usd
    ):

        super().__init__(
            timeout=600
        )

        self.product_id = product_id
        self.quantity = quantity
        self.total_usd = total_usd

    @discord.ui.button(
        label="🔢 Change Quantity",
        style=discord.ButtonStyle.primary
    )
    async def change_quantity(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.send_modal(
            QuantityModal(
                self.product_id
            )
        )

    @discord.ui.button(
        label="💸 Continue to Payment",
        style=discord.ButtonStyle.success
    )
    async def pay(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.defer(
            ephemeral=True
        )

        # ----------------------------------------------------
        # GET LTC PRICE
        # ----------------------------------------------------

        ltc_price = await get_ltc_usd_price()

        if not ltc_price:

            return await interaction.followup.send(
                (
                    "❌ Couldn't retrieve the current "
                    "LTC price. Try again shortly."
                ),
                ephemeral=True
            )

        # ----------------------------------------------------
        # CALCULATE LTC
        # ----------------------------------------------------

        required_ltc = ltc8(
            Decimal(str(self.total_usd))
            / ltc_price
        )

        if required_ltc <= 0:

            return await interaction.followup.send(
                "❌ Invalid LTC amount.",
                ephemeral=True
            )

        guild = interaction.guild

        if not guild:

            return await interaction.followup.send(
                "❌ This can only be used in a server.",
                ephemeral=True
            )

        # ----------------------------------------------------
        # CATEGORY
        # ----------------------------------------------------

        category = None

        if BUY_TICKET_CATEGORY_ID:

            category = guild.get_channel(
                BUY_TICKET_CATEGORY_ID
            )

        # ----------------------------------------------------
        # PERMISSIONS
        # ----------------------------------------------------

        overwrites = {
            guild.default_role:
                discord.PermissionOverwrite(
                    view_channel=False
                ),

            interaction.user:
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                )
        }

        if STAFF_ROLE_ID:

            staff_role = guild.get_role(
                STAFF_ROLE_ID
            )

            if staff_role:

                overwrites[staff_role] = (
                    discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True
                    )
                )

        # ----------------------------------------------------
        # ORDER ID
        # ----------------------------------------------------

        order_id = (
            secrets.token_hex(6)
            .upper()
        )

        # ----------------------------------------------------
        # CREATE CHANNEL
        # ----------------------------------------------------

        channel = await guild.create_text_channel(
            f"buy-{order_id.lower()}",
            category=category,
            overwrites=overwrites,
            reason="AutoBuy order"
        )

        # ----------------------------------------------------
        # ORDER
        # ----------------------------------------------------

        order = {
            "id": order_id,
            "user_id": interaction.user.id,
            "channel_id": channel.id,

            "product_id": self.product_id,
            "quantity": self.quantity,

            "total_usd": str(
                self.total_usd
            ),

            "ltc_price_usd": str(
                ltc_price
            ),

            "ltc_required": str(
                required_ltc
            ),

            "status": "waiting",

            "created_at": time.time(),

            "txid": None,
            "received_ltc": None,
            "confirmations": 0,

            "seen_txids": [],

            "delivered": False
        }

        orders[order_id] = order

        save_json(
            ORDERS_FILE,
            orders
        )

        # ----------------------------------------------------
        # INVOICE
        # ----------------------------------------------------

        message = await channel.send(
            content=(
                f"<@{interaction.user.id}>"
            ),
            embed=invoice_embed(
                order
            ),
            view=PaymentView(
                order_id
            )
        )

        order["invoice_message_id"] = (
            message.id
        )

        orders[order_id] = order

        save_json(
            ORDERS_FILE,
            orders
        )

        # ----------------------------------------------------
        # ORDER INFORMATION
        # ----------------------------------------------------

        product = products[
            self.product_id
        ]

        await channel.send(
            (
                "🛒 **AutoBuy Order Created**\n\n"
                f"📦 Product: **{product['name']}**\n"
                f"🔢 Quantity: **{self.quantity}**\n"
                f"💵 Total: **${self.total_usd}**\n"
                f"💰 LTC Required: **{required_ltc} LTC**\n\n"
                "Waiting for payment..."
            )
        )

        await send_log(
            (
                f"🛒 **NEW AUTOBUY ORDER**\n"
                f"Order: `{order_id}`\n"
                f"User: <@{interaction.user.id}>\n"
                f"Product: `{product['name']}`\n"
                f"Quantity: `{self.quantity}`\n"
                f"USD: `${self.total_usd}`\n"
                f"LTC: `{required_ltc}`"
            )
        )

        await interaction.followup.send(
            (
                "✅ **Order created!**\n\n"
                f"Go to {channel.mention}"
            ),
            ephemeral=True
        )


# ============================================================
# PUBLIC AUTOBUY PANEL
# ============================================================

class AutoBuyPanelView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="🛒 Open Auto Buy",
        style=discord.ButtonStyle.success,
        custom_id="autobuy_open"
    )
    async def open_shop(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.send_message(
            "🛍️ **Select what you want to buy:**",
            view=ProductSelectView(),
            ephemeral=True
        )


# ============================================================
# SEND PANEL
# ============================================================

@bot.command(
    name="autobuy"
)
@commands.has_permissions(
    administrator=True
)
async def autobuy(
    ctx
):

    embed = discord.Embed(
        title="🛒 AUTO BUY",
        description=(
            "Purchase your products automatically "
            "using Litecoin.\n\n"
            "Select your product, choose your quantity, "
            "complete the LTC payment, and receive "
            "your items automatically after confirmation."
        ),
        color=discord.Color.from_rgb(
            217,
            232,
            74
        )
    )

    embed.add_field(
        name="⚡ Automatic",
        value="Automatic payment detection",
        inline=True
    )

    embed.add_field(
        name="💰 LTC",
        value="Litecoin payments",
        inline=True
    )

    embed.add_field(
        name="🎁 Delivery",
        value="Automatic stock delivery",
        inline=True
    )

    await ctx.send(
        embed=embed,
        view=AutoBuyPanelView()
    )


# ============================================================
# ADD PRODUCT
# ============================================================

@bot.command(
    name="addproduct"
)
@commands.has_permissions(
    administrator=True
)
async def addproduct(
    ctx,
    product_id: str,
    price_usd: str,
    min_quantity: int,
    max_quantity: int,
    *,
    name: str
):

    if product_id in products:

        return await ctx.send(
            "❌ Product already exists."
        )

    try:
        Decimal(price_usd)

    except Exception:

        return await ctx.send(
            "❌ Invalid price."
        )

    products[product_id] = {
        "name": name,
        "emoji": "📦",
        "price_usd": price_usd,
        "min_quantity": min_quantity,
        "max_quantity": max_quantity,
        "stock": []
    }

    save_json(
        PRODUCTS_FILE,
        products
    )

    await ctx.send(
        (
            f"✅ Product created:\n"
            f"**{name}**\n"
            f"ID: `{product_id}`"
        )
    )


# ============================================================
# ADD STOCK
# ============================================================

@bot.command(
    name="stock"
)
@commands.has_permissions(
    administrator=True
)
async def stock(
    ctx,
    product_id: str,
    *,
    items: str
):

    product = products.get(
        product_id
    )

    if not product:

        return await ctx.send(
            "❌ Product not found."
        )

    new_items = [
        line.strip()
        for line in items.splitlines()
        if line.strip()
    ]

    if not new_items:

        return await ctx.send(
            "❌ No stock supplied."
        )

    product.setdefault(
        "stock",
        []
    ).extend(
        new_items
    )

    save_json(
        PRODUCTS_FILE,
        products
    )

    await ctx.send(
        (
            f"✅ Added **{len(new_items)}** "
            f"items to **{product['name']}**.\n\n"
            f"Current stock: "
            f"**{len(product['stock'])}**"
        )
    )


# ============================================================
# STOCK COUNT
# ============================================================

@bot.command(
    name="stockcount"
)
@commands.has_permissions(
    administrator=True
)
async def stockcount(
    ctx,
    product_id: str
):

    product = products.get(
        product_id
    )

    if not product:

        return await ctx.send(
            "❌ Product not found."
        )

    await ctx.send(
        (
            f"📦 **{product['name']}**\n"
            f"Stock: **"
            f"{len(product.get('stock', []))}"
            f"**"
        )
    )


# ============================================================
# ORDER INFO
# ============================================================

@bot.command(
    name="order"
)
@commands.has_permissions(
    administrator=True
)
async def order_info(
    ctx,
    order_id: str
):

    order = orders.get(
        order_id.upper()
    )

    if not order:

        return await ctx.send(
            "❌ Order not found."
        )

    await ctx.send(
        (
            f"🧾 **Order `{order_id.upper()}`**\n\n"
            f"User: <@{order['user_id']}>\n"
            f"Product: `{order['product_id']}`\n"
            f"Quantity: `{order['quantity']}`\n"
            f"USD: `${order['total_usd']}`\n"
            f"LTC: `{order['ltc_required']}`\n"
            f"Status: `{order['status']}`\n"
            f"TX: `{order.get('txid')}`\n"
            f"Received: `{order.get('received_ltc')}`\n"
            f"Confirmations: "
            f"`{order.get('confirmations', 0)}`"
        )
    )


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():

    print(
        f"Logged in as "
        f"{bot.user} ({bot.user.id})"
    )

    valid = await validate_ltc_address()

    if not valid:
        print(
            "WARNING: LTC payment detection "
            "may not work until LTC_ADDRESS is fixed."
        )

    if not payment_monitor.is_running():

        payment_monitor.start()

        print(
            "LTC payment monitor started."
        )


# ============================================================
# COMMAND ERROR
# ============================================================

@autobuy.error
async def autobuy_error(
    ctx,
    error
):

    if isinstance(
        error,
        commands.MissingPermissions
    ):

        await ctx.send(
            "❌ Administrator permission required."
        )


# ============================================================
# START
# ============================================================

if not TOKEN:

    raise RuntimeError(
        "DISCORD_TOKEN is missing."
    )


bot.run(TOKEN)
