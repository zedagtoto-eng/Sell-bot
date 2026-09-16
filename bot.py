import os
import json
import asyncio
import random
import string
from decimal import Decimal, ROUND_HALF_UP

import aiohttp
import discord
from discord.ext import commands


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")

# ============================================================
# PUT YOUR LTC ADDRESS HERE
# ============================================================

LTC_ADDRESS = "LL8EdfmaSujikXjyM8Z9vieREQ57nV8YvJ"

# ============================================================
# DISCORD IDS
# ============================================================

AUTOBUY_PANEL_CHANNEL_ID = 1547960767821779094
BUY_TICKET_CATEGORY_ID = 1549730305361973310
STAFF_ROLE_ID = 1546208356610605286
LOG_CHANNEL_ID = 0

# How many confirmations are required
REQUIRED_CONFIRMATIONS = 1

# How often payments are checked
PAYMENT_CHECK_INTERVAL = 10

# ============================================================
# RAILWAY STORAGE
# ============================================================

DATA_DIR = "/app/data"

PRODUCTS_FILE = os.path.join(
    DATA_DIR,
    "products.json"
)

ORDERS_FILE = os.path.join(
    DATA_DIR,
    "orders.json"
)

# ============================================================
# COLORS
# ============================================================

EMBED_COLOR = discord.Color.from_rgb(
    217,
    232,
    74
)

# ============================================================
# BOT
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
# DATA FUNCTIONS
# ============================================================

def ensure_data():

    os.makedirs(
        DATA_DIR,
        exist_ok=True
    )

    if not os.path.exists(PRODUCTS_FILE):

        with open(
            PRODUCTS_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                {},
                f,
                indent=4
            )

    if not os.path.exists(ORDERS_FILE):

        with open(
            ORDERS_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                {},
                f,
                indent=4
            )


def load_products():

    ensure_data()

    try:

        with open(
            PRODUCTS_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return {}


def save_products(data):

    ensure_data()

    temp_file = PRODUCTS_FILE + ".tmp"

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            indent=4
        )

    os.replace(
        temp_file,
        PRODUCTS_FILE
    )


def load_orders():

    ensure_data()

    try:

        with open(
            ORDERS_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return {}


def save_orders(data):

    ensure_data()

    temp_file = ORDERS_FILE + ".tmp"

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            indent=4
        )

    os.replace(
        temp_file,
        ORDERS_FILE
    )


# ============================================================
# HELPERS
# ============================================================

def money(value):

    return f"${float(value):.2f}"


def random_order_id():

    return (
        "AB-"
        + "".join(
            random.choices(
                string.ascii_uppercase
                + string.digits,
                k=8
            )
        )
    )


def get_staff_role(guild):

    if not STAFF_ROLE_ID:
        return None

    return guild.get_role(
        STAFF_ROLE_ID
    )


def is_staff(member):

    if member.guild.owner_id == member.id:
        return True

    role = get_staff_role(
        member.guild
    )

    if not role:
        return False

    return role in member.roles


def ticket_overwrites(
    guild,
    user
):

    overwrites = {

        guild.default_role:
            discord.PermissionOverwrite(
                view_channel=False
            ),

        user:
            discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )
    }

    staff = get_staff_role(
        guild
    )

    if staff:

        overwrites[staff] = (
            discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )
        )

    return overwrites


async def send_log(message):

    if not LOG_CHANNEL_ID:
        return

    channel = bot.get_channel(
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
# LITECOIN API
# ============================================================

LTC_API = "https://litecoinspace.org/api"


async def api_get(path):

    url = LTC_API + path

    timeout = aiohttp.ClientTimeout(
        total=15
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.get(
            url
        ) as response:

            if response.status != 200:
                return None

            return await response.json()


async def get_ltc_price():

    data = await api_get(
        "/v1/prices"
    )

    if not data:
        return None

    if isinstance(data, dict):

        for key in (
            "USD",
            "usd",
            "price"
        ):

            if key in data:

                try:

                    return float(
                        data[key]
                    )

                except Exception:
                    pass

    return None


async def get_mempool_transactions():

    if not LTC_ADDRESS:
        return []

    data = await api_get(
        f"/address/{LTC_ADDRESS}/txs/mempool"
    )

    if isinstance(data, list):
        return data

    return []


async def get_confirmed_transactions():

    if not LTC_ADDRESS:
        return []

    data = await api_get(
        f"/address/{LTC_ADDRESS}/txs"
    )

    if isinstance(data, list):
        return data

    return []


async def get_transaction(txid):

    data = await api_get(
        f"/tx/{txid}"
    )

    if isinstance(data, dict):
        return data

    return None


async def get_tip_height():

    data = await api_get(
        "/blocks/tip/height"
    )

    try:

        return int(data)

    except Exception:

        return None


def amount_sent_to_address(tx):

    if not tx:
        return Decimal("0")

    total_litoshi = 0

    for output in tx.get(
        "vout",
        []
    ):

        address = output.get(
            "scriptpubkey_address"
        )

        if address == LTC_ADDRESS:

            value = output.get(
                "value",
                0
            )

            try:

                total_litoshi += int(
                    value
                )

            except Exception:
                pass

    return (
        Decimal(total_litoshi)
        / Decimal(100_000_000)
    )


def tx_is_confirmed(tx):

    status = tx.get(
        "status",
        {}
    )

    return bool(
        status.get(
            "confirmed",
            False
        )
    )


async def transaction_confirmations(tx):

    if not tx_is_confirmed(tx):
        return 0

    status = tx.get(
        "status",
        {}
    )

    block_height = status.get(
        "block_height"
    )

    if not block_height:
        return 0

    tip = await get_tip_height()

    if tip is None:
        return 0

    return max(
        0,
        tip - int(block_height) + 1
    )


# ============================================================
# PRODUCT FUNCTIONS
# ============================================================

def get_product(
    product_id
):

    products = load_products()

    return products.get(
        product_id
    )


def stock_count(
    product_id
):

    product = get_product(
        product_id
    )

    if not product:
        return 0

    return len(
        product.get(
            "stock",
            []
        )
    )


# ============================================================
# INVOICE EMBED
# ============================================================

def build_invoice_embed(
    order,
    ltc_price
):

    required_ltc = Decimal(
        str(
            order["required_ltc"]
        )
    )

    usd_total = Decimal(
        str(
            order["usd_total"]
        )
    )

    embed = discord.Embed(
        title="💸 Payment Invoice",
        color=EMBED_COLOR
    )

    embed.add_field(
        name="🪙 Litecoin Address",
        value=(
            f"```{LTC_ADDRESS}```"
        ),
        inline=False
    )

    embed.add_field(
        name="💰 Amount Required",
        value=(
            f"**{required_ltc:.8f} LTC**"
        ),
        inline=True
    )

    embed.add_field(
        name="💵 USD Equivalent",
        value=(
            f"**{money(usd_total)}**"
        ),
        inline=True
    )

    embed.add_field(
        name="📦 Product",
        value=order[
            "product_name"
        ],
        inline=True
    )

    embed.add_field(
        name="🔢 Quantity",
        value=str(
            order["quantity"]
        ),
        inline=True
    )

    embed.add_field(
        name="🆔 Order",
        value=f"`{order['id']}`",
        inline=True
    )

    embed.add_field(
        name="⚠️ Important",
        value=(
            "Send the **exact LTC amount** "
            "shown above.\n"
            "Do not send another cryptocurrency.\n"
            "Automatic delivery happens after "
            f"**{REQUIRED_CONFIRMATIONS} "
            "confirmation(s)**."
        ),
        inline=False
    )

    embed.set_footer(
        text="AutoBuy • Payment Monitoring"
    )

    return embed


# ============================================================
# INVOICE BUTTONS
# ============================================================

class InvoiceButtons(
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
        label="Paste Details",
        emoji="📜",
        style=discord.ButtonStyle.secondary,
        custom_id="autobuy_paste_details"
    )
    async def paste_details(
        self,
        interaction,
        button
    ):

        orders = load_orders()

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
                "```text\n"
                f"LTC Address:\n"
                f"{LTC_ADDRESS}\n\n"
                f"Amount:\n"
                f"{order['required_ltc']:.8f} LTC\n"
                "```"
            ),
            ephemeral=True
        )

    @discord.ui.button(
        label="Show QR Code",
        emoji="📷",
        style=discord.ButtonStyle.primary,
        custom_id="autobuy_qr_code"
    )
    async def show_qr(
        self,
        interaction,
        button
    ):

        orders = load_orders()

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
            f"?amount="
            f"{order['required_ltc']:.8f}"
        )

        await interaction.response.send_message(
            (
                "📷 **Litecoin Payment URI**\n"
                f"```{uri}```"
            ),
            ephemeral=True
        )


# ============================================================
# TICKET CONTROLS
# ============================================================

class TicketControls(
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
        label="Close Ticket",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="autobuy_close_ticket"
    )
    async def close_ticket(
        self,
        interaction,
        button
    ):

        orders = load_orders()

        order = orders.get(
            self.order_id
        )

        if not order:
            return await interaction.response.send_message(
                "❌ Order not found.",
                ephemeral=True
            )

        if (
            interaction.user.id
            != order["user_id"]
            and not is_staff(
                interaction.user
            )
        ):

            return await interaction.response.send_message(
                "❌ You cannot close this ticket.",
                ephemeral=True
            )

        order[
            "status"
        ] = "cancelled"

        orders[
            self.order_id
        ] = order

        save_orders(
            orders
        )

        await interaction.response.send_message(
            "🔒 Closing ticket..."
        )

        await asyncio.sleep(
            2
        )

        try:

            await interaction.channel.delete(
                reason="AutoBuy ticket closed"
            )

        except Exception:
            pass


# ============================================================
# PRODUCT SELECT
# ============================================================

class ProductSelect(
    discord.ui.Select
):

    def __init__(self):

        products = load_products()

        options = []

        for product_id, product in list(
            products.items()
        )[:25]:

            stock = len(
                product.get(
                    "stock",
                    []
                )
            )

            if stock <= 0:
                continue

            options.append(
                discord.SelectOption(
                    label=product.get(
                        "name",
                        product_id
                    )[:100],
                    value=product_id,
                    description=(
                        f"${product.get('price_usd', 0):.2f}"
                        f" • {stock} stock"
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
            placeholder="🛒 Choose a product",
            options=options,
            custom_id="autobuy_product"
        )

    async def callback(
        self,
        interaction
    ):

        product_id = self.values[0]

        if product_id == "none":

            return await interaction.response.send_message(
                "❌ No products are in stock.",
                ephemeral=True
            )

        product = get_product(
            product_id
        )

        if not product:

            return await interaction.response.send_message(
                "❌ Product no longer exists.",
                ephemeral=True
            )

        stock = len(
            product.get(
                "stock",
                []
            )
        )

        if stock <= 0:

            return await interaction.response.send_message(
                "❌ This product is out of stock.",
                ephemeral=True
            )

        await interaction.response.send_message(
            (
                f"📦 **{product['name']}**\n"
                f"💵 Price: "
                f"**${product['price_usd']:.2f}** each\n"
                f"📊 Stock: **{stock}**\n\n"
                "Choose your quantity:"
            ),
            view=QuantityView(
                product_id
            ),
            ephemeral=True
        )


class ProductView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=120
        )

        self.add_item(
            ProductSelect()
        )


# ============================================================
# QUANTITY SELECT
# ============================================================

class QuantitySelect(
    discord.ui.Select
):

    def __init__(
        self,
        product_id
    ):

        self.product_id = product_id

        product = get_product(
            product_id
        )

        stock = len(
            product.get(
                "stock",
                []
            )
        )

        max_quantity = min(
            stock,
            int(
                product.get(
                    "max_quantity",
                    10
                )
            )
        )

        min_quantity = max(
            1,
            int(
                product.get(
                    "min_quantity",
                    1
                )
            )
        )

        if max_quantity < min_quantity:
            max_quantity = min_quantity

        options = []

        for quantity in range(
            min_quantity,
            max_quantity + 1
        ):

            options.append(
                discord.SelectOption(
                    label=f"{quantity}x",
                    value=str(
                        quantity
                    ),
                    description=(
                        f"Buy {quantity} item(s)"
                    )
                )
            )

        super().__init__(
            placeholder="🔢 Choose quantity",
            options=options[:25],
            custom_id=(
                f"autobuy_quantity_{product_id}"
            )
        )

    async def callback(
        self,
        interaction
    ):

        quantity = int(
            self.values[0]
        )

        await create_order(
            interaction,
            self.product_id,
            quantity
        )


class QuantityView(
    discord.ui.View
):

    def __init__(
        self,
        product_id
    ):

        super().__init__(
            timeout=120
        )

        self.add_item(
            QuantitySelect(
                product_id
            )
        )


# ============================================================
# CREATE TICKET
# ============================================================

async def create_order(
    interaction,
    product_id,
    quantity
):

    product = get_product(
        product_id
    )

    if not product:

        return await interaction.response.send_message(
            "❌ Product not found.",
            ephemeral=True
        )

    available = len(
        product.get(
            "stock",
            []
        )
    )

    if quantity > available:

        return await interaction.response.send_message(
            f"❌ Only **{available}** stock available.",
            ephemeral=True
        )

    usd_total = (
        Decimal(
            str(
                product["price_usd"]
            )
        )
        * Decimal(quantity)
    ).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )

    ltc_price = await get_ltc_price()

    if not ltc_price:

        return await interaction.response.send_message(
            "❌ Couldn't get the current LTC price.",
            ephemeral=True
        )

    required_ltc = (
        usd_total
        / Decimal(
            str(
                ltc_price
            )
        )
    ).quantize(
        Decimal("0.00000001"),
        rounding=ROUND_HALF_UP
    )

    guild = interaction.guild
    user = interaction.user

    category = None

    if BUY_TICKET_CATEGORY_ID:

        category = guild.get_channel(
            BUY_TICKET_CATEGORY_ID
        )

    username = "".join(
        character.lower()
        if character.isalnum()
        else "-"
        for character in user.name
    )

    username = username[:20]

    channel_name = (
        f"autobuy-{username}"
    )

    overwrites = ticket_overwrites(
        guild,
        user
    )

    try:

        channel = await guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites,
            reason="AutoBuy ticket"
        )

    except discord.Forbidden:

        return await interaction.response.send_message(
            "❌ I don't have permission to create tickets.",
            ephemeral=True
        )

    order_id = random_order_id()

    order = {

        "id": order_id,

        "user_id":
            user.id,

        "guild_id":
            guild.id,

        "channel_id":
            channel.id,

        "product_id":
            product_id,

        "product_name":
            product["name"],

        "quantity":
            quantity,

        "unit_price_usd":
            float(
                product["price_usd"]
            ),

        "usd_total":
            float(
                usd_total
            ),

        "required_ltc":
            float(
                required_ltc
            ),

        "status":
            "waiting",

        "created_at":
            discord.utils.utcnow().isoformat(),

        "detected_tx":
            None,

        "received_ltc":
            0,

        "confirmations":
            0,

        "delivered":
            False
    }

    orders = load_orders()

    orders[
        order_id
    ] = order

    save_orders(
        orders
    )

    await interaction.response.send_message(
        (
            "🎟️ **AutoBuy Ticket Created!**\n"
            f"{channel.mention}"
        ),
        ephemeral=True
    )

    embed = discord.Embed(
        title="🛒 AutoBuy Ticket",
        description=(
            f"Welcome {user.mention}!\n\n"
            "Your order has been created."
        ),
        color=EMBED_COLOR
    )

    embed.add_field(
        name="📦 Product",
        value=product["name"],
        inline=True
    )

    embed.add_field(
        name="🔢 Quantity",
        value=str(quantity),
        inline=True
    )

    embed.add_field(
        name="💵 Total",
        value=money(usd_total),
        inline=True
    )

    embed.add_field(
        name="📊 Stock",
        value=str(available),
        inline=True
    )

    embed.add_field(
        name="🆔 Order",
        value=f"`{order_id}`",
        inline=True
    )

    embed.set_footer(
        text="AutoBuy • Ticket System"
    )

    await channel.send(
        embed=embed
    )

    invoice = build_invoice_embed(
        order,
        ltc_price
    )

    await channel.send(
        embed=invoice,
        view=InvoiceButtons(
            order_id
        )
    )

    await channel.send(
        (
            "🔎 **Payment monitoring started.**\n"
            "Send the exact amount shown above."
        )
    )

    await channel.send(
        view=TicketControls(
            order_id
        )
    )

    await send_log(
        (
            "🛒 **New AutoBuy Order**\n"
            f"User: {user.mention}\n"
            f"Product: {product['name']}\n"
            f"Quantity: {quantity}\n"
            f"Total: ${usd_total:.2f}\n"
            f"Order: `{order_id}`"
        )
    )


# ============================================================
# PUBLIC AUTOBUY PANEL
# ============================================================

class AutoBuyPanel(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="Open Auto Buy",
        emoji="🎟️",
        style=discord.ButtonStyle.success,
        custom_id="autobuy_open_ticket"
    )
    async def open_autobuy(
        self,
        interaction,
        button
    ):

        products = load_products()

        available = [
            product
            for product in products.values()
            if len(
                product.get(
                    "stock",
                    []
                )
            ) > 0
        ]

        if not available:

            return await interaction.response.send_message(
                "❌ AutoBuy is currently out of stock.",
                ephemeral=True
            )

        await interaction.response.send_message(
            (
                "🛒 **AUTO BUY**\n\n"
                "Select the product you want to purchase."
            ),
            view=ProductView(),
            ephemeral=True
        )


# ============================================================
# $AUTOBUY
# ============================================================

@bot.command(
    name="autobuy"
)
@commands.has_permissions(
    administrator=True
)
async def autobuy_command(
    ctx
):

    embed = discord.Embed(
        title="🛒 AUTO BUY",
        description=(
            "**Automatic Stock Delivery**\n\n"
            "Click **🎟️ Open Auto Buy** below "
            "to create your private purchase ticket.\n\n"
            "📦 Select a product\n"
            "🔢 Select quantity\n"
            "💸 Pay with LTC\n"
            "🔵 Payment detected\n"
            "🟢 Payment confirmed\n"
            "📦 Stock delivered automatically"
        ),
        color=EMBED_COLOR
    )

    embed.add_field(
        name="📊 Stock System",
        value=(
            "Products can hold up to **10+ stock items** "
            "and stock is checked before every order."
        ),
        inline=False
    )

    embed.set_footer(
        text="AutoBuy • Private Ticket System"
    )

    await ctx.send(
        embed=embed,
        view=AutoBuyPanel()
    )


# ============================================================
# $ADDPRODUCT
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
    price_usd: float,
    min_quantity: int,
    max_quantity: int,
    *,
    name: str
):

    if price_usd <= 0:

        return await ctx.send(
            "❌ Price must be greater than $0."
        )

    if min_quantity < 1:

        return await ctx.send(
            "❌ Minimum quantity must be at least 1."
        )

    if max_quantity < min_quantity:

        return await ctx.send(
            "❌ Maximum quantity cannot be below minimum."
        )

    products = load_products()

    if product_id in products:

        return await ctx.send(
            "❌ Product already exists."
        )

    products[
        product_id
    ] = {

        "name":
            name,

        "price_usd":
            price_usd,

        "min_quantity":
            min_quantity,

        "max_quantity":
            max_quantity,

        "stock":
            []
    }

    save_products(
        products
    )

    await ctx.send(
        (
            "✅ **Product Created**\n\n"
            f"📦 Product: **{name}**\n"
            f"🆔 ID: `{product_id}`\n"
            f"💵 Price: **${price_usd:.2f}**\n"
            f"🔢 Quantity: "
            f"**{min_quantity}-{max_quantity}**\n"
            "📊 Stock: **0**"
        )
    )


# ============================================================
# $STOCK
# ============================================================

@bot.command(
    name="stock"
)
@commands.has_permissions(
    administrator=True
)
async def stock_command(
    ctx,
    product_id: str,
    *,
    items: str
):

    product = get_product(
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
            "❌ No stock items supplied."
        )

    products = load_products()

    products[
        product_id
    ][
        "stock"
    ].extend(
        new_items
    )

    save_products(
        products
    )

    total = len(
        products[
            product_id
        ][
            "stock"
        ]
    )

    await ctx.send(
        (
            f"✅ Added **{len(new_items)}** "
            f"stock item(s).\n"
            f"📦 Product: **{product['name']}**\n"
            f"📊 Total Stock: **{total}**"
        )
    )


# ============================================================
# $STOCKCOUNT
# ============================================================

@bot.command(
    name="stockcount"
)
@commands.has_permissions(
    administrator=True
)
async def stockcount_command(
    ctx,
    product_id: str
):

    product = get_product(
        product_id
    )

    if not product:

        return await ctx.send(
            "❌ Product not found."
        )

    count = len(
        product.get(
            "stock",
            []
        )
    )

    await ctx.send(
        (
            f"📦 **{product['name']}**\n"
            f"📊 Stock: **{count}**"
        )
    )


# ============================================================
# $PRODUCTS
# ============================================================

@bot.command(
    name="products"
)
async def products_command(
    ctx
):

    products = load_products()

    if not products:

        return await ctx.send(
            "❌ No products configured."
        )

    embed = discord.Embed(
        title="📦 AutoBuy Products",
        color=EMBED_COLOR
    )

    for product_id, product in products.items():

        count = len(
            product.get(
                "stock",
                []
            )
        )

        embed.add_field(
            name=product["name"],
            value=(
                f"ID: `{product_id}`\n"
                f"Price: **${product['price_usd']:.2f}**\n"
                f"Stock: **{count}**"
            ),
            inline=False
        )

    await ctx.send(
        embed=embed
    )


# ============================================================
# UPDATE TICKET
# ============================================================

async def update_ticket(
    order,
    title,
    description
):

    channel = bot.get_channel(
        int(
            order["channel_id"]
        )
    )

    if not channel:
        return

    embed = discord.Embed(
        title=title,
        description=description,
        color=EMBED_COLOR
    )

    embed.set_footer(
        text=f"Order {order['id']}"
    )

    try:

        await channel.send(
            embed=embed
        )

    except Exception:
        pass


# ============================================================
# DELIVERY
# ============================================================

async def deliver_order(
    order
):

    if order.get(
        "delivered"
    ):
        return False

    products = load_products()

    product = products.get(
        order["product_id"]
    )

    if not product:
        return False

    stock = product.get(
        "stock",
        []
    )

    quantity = int(
        order["quantity"]
    )

    if len(stock) < quantity:

        await update_ticket(
            order,
            "❌ DELIVERY FAILED",
            (
                "Payment was confirmed, but there "
                "isn't enough stock available.\n\n"
                "Staff review is required."
            )
        )

        return False

    items = stock[
        :quantity
    ]

    product[
        "stock"
    ] = stock[
        quantity:
    ]

    products[
        order["product_id"]
    ] = product

    save_products(
        products
    )

    orders = load_orders()

    if order["id"] not in orders:
        return False

    # Prevent duplicate delivery
    orders[
        order["id"]
    ][
        "delivered"
    ] = True

    orders[
        order["id"]
    ][
        "status"
    ] = "delivered"

    save_orders(
        orders
    )

    channel = bot.get_channel(
        int(
            order["channel_id"]
        )
    )

    if channel:

        embed = discord.Embed(
            title="📦 DELIVERY",
            description=(
                "Your payment has been confirmed "
                "and your order has been delivered."
            ),
            color=EMBED_COLOR
        )

        await channel.send(
            embed=embed
        )

        for number, item in enumerate(
            items,
            start=1
        ):

            await channel.send(
                (
                    f"**Item {number}**\n"
                    f"```text\n"
                    f"{item}\n"
                    f"```"
                )
            )

        await channel.send(
            (
                "🟢 **ORDER COMPLETE**\n"
                "You can close this ticket."
            ),
            view=TicketControls(
                order["id"]
            )
        )

    await send_log(
        (
            "📦 **AutoBuy Delivered**\n"
            f"Order: `{order['id']}`\n"
            f"User: <@{order['user_id']}>\n"
            f"Product: {order['product_name']}\n"
            f"Quantity: {order['quantity']}"
        )
    )

    return True


# ============================================================
# TRANSACTION PROCESSING
# ============================================================

async def inspect_transaction(
    order,
    tx
):

    if not tx:
        return False

    received = amount_sent_to_address(
        tx
    )

    if received <= 0:
        return False

    required = Decimal(
        str(
            order["required_ltc"]
        )
    )

    txid = tx.get(
        "txid"
    )

    order[
        "detected_tx"
    ] = txid

    order[
        "received_ltc"
    ] = float(
        received
    )

    # --------------------------------------------------------
    # OVERPAYMENT
    # --------------------------------------------------------

    if received > required:

        order[
            "status"
        ] = "overpaid"

        await update_ticket(
            order,
            "🟠 PAYMENT OVERPAID",
            (
                f"Required: **{required:.8f} LTC**\n"
                f"Received: **{received:.8f} LTC**\n\n"
                "Automatic delivery has been paused.\n"
                "Staff review is required."
            )
        )

        return True

    # --------------------------------------------------------
    # UNDERPAYMENT
    # --------------------------------------------------------

    if received < required:

        remaining = (
            required - received
        )

        order[
            "status"
        ] = "underpaid"

        await update_ticket(
            order,
            "🟡 PAYMENT UNDERPAID",
            (
                f"Required: **{required:.8f} LTC**\n"
                f"Received: **{received:.8f} LTC**\n"
                f"Remaining: **{remaining:.8f} LTC**\n\n"
                "No stock has been delivered."
            )
        )

        return True

    # --------------------------------------------------------
    # PENDING TRANSACTION
    # --------------------------------------------------------

    if not tx_is_confirmed(tx):

        order[
            "status"
        ] = "detected"

        await update_ticket(
            order,
            "🔵 TRANSACTION DETECTED",
            (
                "A matching Litecoin transaction "
                "was detected.\n\n"
                f"**TX:** `{txid}`\n"
                f"**Amount:** {received:.8f} LTC\n"
                "**Confirmations:** 0\n\n"
                "Waiting for blockchain confirmation."
            )
        )

        return True

    # --------------------------------------------------------
    # CONFIRMATIONS
    # --------------------------------------------------------

    confirmations = (
        await transaction_confirmations(
            tx
        )
    )

    order[
        "confirmations"
    ] = confirmations

    if confirmations < REQUIRED_CONFIRMATIONS:

        order[
            "status"
        ] = "detected"

        await update_ticket(
            order,
            "🔵 TRANSACTION DETECTED",
            (
                f"**TX:** `{txid}`\n"
                f"**Amount:** {received:.8f} LTC\n"
                f"**Confirmations:** "
                f"{confirmations}/"
                f"{REQUIRED_CONFIRMATIONS}\n\n"
                "Waiting for more confirmations."
            )
        )

        return True

    # --------------------------------------------------------
    # CONFIRMED
    # --------------------------------------------------------

    order[
        "status"
    ] = "confirmed"

    await update_ticket(
        order,
        "🟢 PAYMENT CONFIRMED",
        (
            f"**TX:** `{txid}`\n"
            f"**Amount:** {received:.8f} LTC\n"
            f"**Confirmations:** {confirmations}\n\n"
            "Payment confirmed.\n"
            "Delivering your stock..."
        )
    )

    await deliver_order(
        order
    )

    return True


# ============================================================
# CHECK ORDER
# ============================================================

async def check_order(
    order
):

    if order.get(
        "delivered"
    ):
        return

    if order.get(
        "status"
    ) in (
        "cancelled",
        "expired",
        "delivered",
        "overpaid"
    ):
        return

    # Check previously detected transaction
    detected_tx = order.get(
        "detected_tx"
    )

    if detected_tx:

        tx = await get_transaction(
            detected_tx
        )

        if tx:

            await inspect_transaction(
                order,
                tx
            )

            return

    # Check mempool
    mempool = (
        await get_mempool_transactions()
    )

    for tx in mempool:

        received = (
            amount_sent_to_address(
                tx
            )
        )

        if received <= 0:
            continue

        await inspect_transaction(
            order,
            tx
        )

        return

    # Check confirmed history
    confirmed = (
        await get_confirmed_transactions()
    )

    for tx in confirmed:

        received = (
            amount_sent_to_address(
                tx
            )
        )

        if received <= 0:
            continue

        required = Decimal(
            str(
                order["required_ltc"]
            )
        )

        if received == required:

            await inspect_transaction(
                order,
                tx
            )

            return


# ============================================================
# PAYMENT LOOP
# ============================================================

async def payment_loop():

    await bot.wait_until_ready()

    while not bot.is_closed():

        try:

            orders = load_orders()

            changed = False

            for order_id, order in list(
                orders.items()
            ):

                if order.get(
                    "delivered"
                ):
                    continue

                if order.get(
                    "status"
                ) in (
                    "cancelled",
                    "expired",
                    "delivered"
                ):
                    continue

                try:

                    await check_order(
                        order
                    )

                    orders[
                        order_id
                    ] = order

                    changed = True

                except Exception as error:

                    print(
                        f"[PAYMENT ERROR] "
                        f"{order_id}: {error}"
                    )

            if changed:

                save_orders(
                    orders
                )

        except Exception as error:

            print(
                f"[PAYMENT LOOP ERROR] "
                f"{error}"
            )

        await asyncio.sleep(
            PAYMENT_CHECK_INTERVAL
        )


# ============================================================
# LTC TEST
# ============================================================

@bot.command(
    name="ltctest"
)
@commands.has_permissions(
    administrator=True
)
async def ltctest(
    ctx
):

    if not LTC_ADDRESS:

        return await ctx.send(
            "❌ Put your LTC address in the code first."
        )

    message = await ctx.send(
        "🔎 Checking Litecoin API..."
    )

    try:

        price = await get_ltc_price()

        mempool = (
            await get_mempool_transactions()
        )

        confirmed = (
            await get_confirmed_transactions()
        )

        embed = discord.Embed(
            title="🔎 Litecoin Test",
            color=EMBED_COLOR
        )

        embed.add_field(
            name="🪙 LTC Address",
            value=f"`{LTC_ADDRESS}`",
            inline=False
        )

        embed.add_field(
            name="💵 LTC Price",
            value=(
                f"${price:.2f}"
                if price
                else "Unavailable"
            ),
            inline=True
        )

        embed.add_field(
            name="🔵 Pending",
            value=str(
                len(mempool)
            ),
            inline=True
        )

        embed.add_field(
            name="🟢 Confirmed",
            value=str(
                len(confirmed)
            ),
            inline=True
        )

        await message.edit(
            content=None,
            embed=embed
        )

    except Exception as error:

        await message.edit(
            content=(
                "❌ Litecoin test failed:\n"
                f"```{error}```"
            )
        )


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():

    ensure_data()

    print(
        "===================================="
    )

    print(
        f"✅ Logged in as {bot.user}"
    )

    print(
        "🛒 AutoBuy Ticket System ONLINE"
    )

    print(
        f"💰 LTC Address: {LTC_ADDRESS}"
    )

    print(
        "📦 Stock System: ENABLED"
    )

    print(
        "🎟️ Ticket System: ENABLED"
    )

    print(
        "===================================="
    )

    if not hasattr(
        bot,
        "payment_task"
    ):

        bot.payment_task = (
            asyncio.create_task(
                payment_loop()
            )
        )

      @bot.command(name="removeproduct")
@commands.has_permissions(administrator=True)
async def removeproduct_command(ctx, product_id: str):
    products = load_json(PRODUCTS_FILE, {})

    if product_id not in products:
        return await ctx.send(f"❌ Product `{product_id}` doesn't exist.")

    product_name = products[product_id].get("name", product_id)

    del products[product_id]
    save_json(PRODUCTS_FILE, products)

    await ctx.send(
        f"🗑️ Removed product **{product_name}** (`{product_id}`) successfully."
    )

# ============================================================
# START
# ============================================================

ensure_data()

if not TOKEN:

    raise RuntimeError(
        "DISCORD_TOKEN is missing."
    )

bot.run(
    TOKEN
)
