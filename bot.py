import os
import re
import html
import asyncio
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")

# Put your IDs here
TICKET_CATEGORY_ID = 1546004427931123722
TRANSCRIPT_CHANNEL_ID = 1546005594605879296
TERMS_CHANNEL_ID = 1545851197767024772

# Optional staff role. Put 0 if you don't want one.
STAFF_ROLE_ID = 123456789012345678

# Optional banner image
TICKETS_BANNER_URL = "https://i.imgur.com/XGPo1uo.png"

EMBED_COLOR = discord.Color.from_rgb(88, 101, 242)


# ============================================================
# BOT
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="$",
    intents=intents
)

tree = bot.tree


# ============================================================
# TICKET STORAGE
# ============================================================

# channel_id: {
#     "owner_id": int,
#     "type": "purchase" / "support",
#     "payment": str,
#     "concern": str
# }

tickets = {}


# ============================================================
# HELPERS
# ============================================================

def sanitize_channel_name(text: str):
    text = text.lower()
    text = re.sub(r"[^a-z0-9-]", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")[:80]


def ticket_id(channel_id: int):
    return f"#{str(channel_id)[-6:]}"


def get_staff_overwrite(guild: discord.Guild):

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False
        )
    }

    if STAFF_ROLE_ID:
        role = guild.get_role(STAFF_ROLE_ID)

        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True
            )

    overwrites[guild.me] = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        manage_channels=True,
        manage_messages=True,
        attach_files=True,
        embed_links=True
    )

    return overwrites


async def create_ticket_channel(
    interaction: discord.Interaction,
    name: str
):

    guild = interaction.guild

    category = guild.get_channel(TICKET_CATEGORY_ID)

    if not isinstance(category, discord.CategoryChannel):
        raise RuntimeError(
            "TICKET_CATEGORY_ID is not a valid category."
        )

    overwrites = get_staff_overwrite(guild)

    overwrites[interaction.user] = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        attach_files=True,
        embed_links=True
    )

    channel = await guild.create_text_channel(
        name=name,
        category=category,
        overwrites=overwrites,
        reason=f"Ticket created by {interaction.user}"
    )

    return channel


async def generate_transcript(channel: discord.TextChannel):

    messages = []

    async for message in channel.history(
        limit=None,
        oldest_first=True
    ):

        timestamp = message.created_at.strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

        content = html.escape(message.content or "")

        attachments = ""

        for attachment in message.attachments:

            attachments += (
                f'<br><a href="{attachment.url}">'
                f'{html.escape(attachment.filename)}</a>'
            )

        embeds = ""

        for embed in message.embeds:

            if embed.title:
                embeds += (
                    "<br><b>Embed:</b> "
                    + html.escape(embed.title)
                )

            if embed.description:
                embeds += (
                    "<br>"
                    + html.escape(embed.description)
                )

        messages.append(
            f"""
            <div class="message">
                <div class="author">
                    {html.escape(str(message.author))}
                </div>

                <div class="time">
                    {timestamp}
                </div>

                <div class="content">
                    {content}
                    {attachments}
                    {embeds}
                </div>
            </div>
            """
        )

    transcript = f"""
<!DOCTYPE html>
<html>
<head>

<meta charset="UTF-8">

<title>
Transcript - {html.escape(channel.name)}
</title>

<style>

body {{
    background: #313338;
    color: #dbdee1;
    font-family: Arial, sans-serif;
    padding: 25px;
}}

h1 {{
    color: #ffffff;
}}

.message {{
    background: #2b2d31;
    border-radius: 8px;
    padding: 15px;
    margin-bottom: 12px;
}}

.author {{
    color: #5865f2;
    font-weight: bold;
    font-size: 16px;
}}

.time {{
    color: #949ba4;
    font-size: 12px;
    margin-top: 4px;
}}

.content {{
    margin-top: 10px;
    white-space: pre-wrap;
    word-wrap: break-word;
}}

a {{
    color: #00a8fc;
}}

</style>

</head>

<body>

<h1>Ticket Transcript</h1>

<p>
Channel: #{html.escape(channel.name)}
</p>

{"".join(messages)}

</body>
</html>
"""

    filename = (
        sanitize_channel_name(channel.name)
        + "-transcript.html"
    )

    return discord.File(
        fp=discord.utils.BytesIO(
            transcript.encode("utf-8")
        ),
        filename=filename
    )


# ============================================================
# MAIN ORDER PANEL
# ============================================================

class OrderTypeSelect(discord.ui.Select):

    def __init__(self):

        options = [

            discord.SelectOption(
                label="Purchase",
                description="Buy a product or service",
                emoji="🛒",
                value="purchase"
            ),

            discord.SelectOption(
                label="Support",
                description="Get help or ask a question",
                emoji="🔧",
                value="support"
            )

        ]

        super().__init__(
            placeholder="Choose your order type",
            min_values=1,
            max_values=1,
            options=options
        )


    async def callback(
        self,
        interaction: discord.Interaction
    ):

        selected = self.values[0]

        if selected == "purchase":

            await interaction.response.send_message(
                embed=purchase_terms_embed(),
                view=PurchaseTermsView(),
                ephemeral=True
            )

        elif selected == "support":

            await interaction.response.send_modal(
                SupportModal()
            )


class OrderPanelView(discord.ui.View):

    def __init__(self):

        super().__init__(
            timeout=None
        )

        self.add_item(
            OrderTypeSelect()
        )


def order_panel_embed():

    embed = discord.Embed(
        description=(
            "# 📣 Order Panel\n\n"
            "Create a service request with "
            "**Flow Services.**\n"
            "Our team will be with you shortly.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "## ☷ Start your order\n\n"
            "❯ Select a service from the menu below "
            "to open your ticket."
        ),
        color=EMBED_COLOR
    )

    if (
        TICKETS_BANNER_URL
        and "your-image-url-here" not in TICKETS_BANNER_URL
    ):

        embed.set_image(
            url=TICKETS_BANNER_URL
        )

    embed.add_field(
        name="",
        value=(
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🛡️ Flow Services • Ticket System"
        ),
        inline=False
    )

    return embed


# ============================================================
# PURCHASE TERMS
# ============================================================

def purchase_terms_embed():

    return discord.Embed(
        description=(
            "# 🛡️ Purchase Terms\n\n"
            "Read our Terms of Service "
            "before you continue.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "❔ Click **Accept and Continue** if you "
            "have read and agree to the terms.\n\n"
            "🗑️ Click **Cancel** if you do not want "
            "to continue.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🛡️ Flow Services | Purchase Agreement"
        ),
        color=EMBED_COLOR
    )


class PurchaseTermsView(discord.ui.View):

    def __init__(self):

        super().__init__(
            timeout=300
        )

        terms_channel = f"<#{TERMS_CHANNEL_ID}>"

        self.add_item(
            discord.ui.Button(
                label="Read Terms of Service",
                emoji="↗️",
                style=discord.ButtonStyle.link,
                url=(
                    f"https://discord.com/channels/"
                    f"@me/{TERMS_CHANNEL_ID}"
                )
            )
        )


    @discord.ui.button(
        label="Accept and Continue",
        emoji="❔",
        style=discord.ButtonStyle.success
    )
    async def accept(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.edit_message(
            embed=payment_embed(),
            view=PaymentView()
        )


    @discord.ui.button(
        label="Cancel",
        emoji="🗑️",
        style=discord.ButtonStyle.secondary
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.edit_message(
            content="❌ Purchase cancelled.",
            embed=None,
            view=None
        )


# ============================================================
# PAYMENT METHOD
# ============================================================

def payment_embed():

    return discord.Embed(
        description=(
            "# 💳 Select Payment Method\n\n"
            "Choose how you would like to "
            "pay for your order.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🛡️ **Agreement Reference**\n"
            "Terms accepted"
        ),
        color=EMBED_COLOR
    )


class PaymentButton(
    discord.ui.Button
):

    def __init__(
        self,
        payment_name: str,
        emoji: str
    ):

        super().__init__(
            label=payment_name,
            emoji=emoji,
            style=discord.ButtonStyle.secondary
        )

        self.payment_name = payment_name


    async def callback(
        self,
        interaction: discord.Interaction
    ):

        await create_purchase_ticket(
            interaction,
            self.payment_name
        )


class PaymentView(discord.ui.View):

    def __init__(self):

        super().__init__(
            timeout=300
        )

        self.add_item(
            PaymentButton(
                "SOL",
                "💳"
            )
        )

        self.add_item(
            PaymentButton(
                "LTC",
                "💳"
            )
        )

        self.add_item(
            PaymentButton(
                "Other",
                "💳"
            )
        )


# ============================================================
# CREATE PURCHASE TICKET
# ============================================================

async def create_purchase_ticket(
    interaction: discord.Interaction,
    payment_method: str
):

    await interaction.response.defer(
        ephemeral=True,
        thinking=True
    )

    short_id = str(interaction.user.id)[-4:]

    channel = await create_ticket_channel(
        interaction,
        f"purchase-{short_id}"
    )

    tickets[channel.id] = {

        "owner_id": interaction.user.id,
        "type": "purchase",
        "payment": payment_method,
        "product": "Not selected",
        "quantity": "1"

    }

    await channel.send(
        content=(
            f"{interaction.user.mention} "
            f"<@&{STAFF_ROLE_ID}>"
            if STAFF_ROLE_ID
            else interaction.user.mention
        )
    )

    await channel.send(
        embed=purchase_ticket_embed(
            interaction.user,
            payment_method,
            "Not selected"
        ),
        view=PurchaseCloseView()
    )

    await channel.send(
        embed=product_selection_embed(),
        view=ProductSelectView()
    )

    await interaction.followup.send(
        f"🛒 Your purchase ticket has been created: "
        f"{channel.mention}",
        ephemeral=True
    )


def purchase_ticket_embed(
    user,
    payment_method,
    product
):

    return discord.Embed(
        description=(
            f"{user.mention}\n\n"
            "🛒 **Customer Status**\n"
            "First Purchase\n\n"
            "**Product**\n"
            f"{product}\n\n"
            "**Quantity**\n"
            "1\n\n"
            "💲 **Payment Method**\n"
            f"{payment_method}\n\n"
            "👍 A staff member will assist you shortly."
        ),
        color=EMBED_COLOR
    )


def product_selection_embed():

    return discord.Embed(
        description=(
            "# 🛒 What product are you buying?\n\n"
            "To help our team assist you as quickly "
            "and accurately as possible, please select "
            "the product category that best matches "
            "your purchase from the menu below."
        ),
        color=EMBED_COLOR
    )


class ProductCategorySelect(
    discord.ui.Select
):

    def __init__(self):

        options = [

            discord.SelectOption(
                label="Spotify",
                emoji="🎵"
            ),

            discord.SelectOption(
                label="Discord",
                emoji="💬"
            ),

            discord.SelectOption(
                label="Digital Product",
                emoji="💻"
            ),

            discord.SelectOption(
                label="Other",
                emoji="📦"
            )

        ]

        super().__init__(
            placeholder="Select a product category",
            options=options
        )


    async def callback(
        self,
        interaction: discord.Interaction
    ):

        tickets[
            interaction.channel.id
        ]["product"] = self.values[0]

        await interaction.response.send_message(
            f"✅ Product selected: "
            f"**{self.values[0]}**",
            ephemeral=True
        )


class ProductSelectView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=None
        )

        self.add_item(
            ProductCategorySelect()
        )


class PurchaseCloseView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=None
        )


    @discord.ui.button(
        label="Close Ticket",
        emoji="🗑️",
        style=discord.ButtonStyle.secondary
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await close_ticket_system(
            interaction
        )


# ============================================================
# SUPPORT MODAL
# ============================================================

class SupportModal(
    discord.ui.Modal,
    title="Support Ticket"
):

    concern = discord.ui.TextInput(
        label="What is your concern?",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=4000
    )


    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        await interaction.response.defer(
            ephemeral=True,
            thinking=True
        )

        short_id = str(
            interaction.user.id
        )[-4:]

        channel = await create_ticket_channel(
            interaction,
            f"support-{short_id}"
        )

        tickets[channel.id] = {

            "owner_id": interaction.user.id,
            "type": "support",
            "concern": str(self.concern)

        }

        await channel.send(
            content=(
                f"{interaction.user.mention} "
                f"<@&{STAFF_ROLE_ID}>"
                if STAFF_ROLE_ID
                else interaction.user.mention
            )
        )

        await channel.send(
            embed=support_ticket_embed(
                interaction.user,
                channel,
                str(self.concern)
            ),
            view=SupportCloseView()
        )

        await channel.send(
            embed=ticket_controls_embed(),
            view=TicketControlsView()
        )

        await interaction.followup.send(
            f"🔧 Your support ticket has been created: "
            f"{channel.mention}",
            ephemeral=True
        )


def support_ticket_embed(
    user,
    channel,
    concern
):

    return discord.Embed(
        description=(
            "# 🔧 Support Ticket\n\n"
            f"Hey {user.mention}! "
            "A support member will be with you shortly.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📞 **Ticket ID** · "
            f"{ticket_id(channel.id)}\n"
            f"☷ **Concern** · {concern}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🛡️ Flow Services"
        ),
        color=EMBED_COLOR
    )


def ticket_controls_embed():

    return discord.Embed(
        description=(
            "# 🔧 Ticket Controls\n\n"
            "Manage this ticket below. "
            "Staff-only actions are protected."
        ),
        color=EMBED_COLOR
    )


class SupportCloseView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=None
        )


    @discord.ui.button(
        label="Close Ticket",
        emoji="🔒",
        style=discord.ButtonStyle.secondary
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await close_ticket_system(
            interaction
        )


# ============================================================
# TICKET CONTROLS
# ============================================================

class RenameModal(
    discord.ui.Modal,
    title="Rename Ticket"
):

    name = discord.ui.TextInput(
        label="New ticket name",
        required=True,
        max_length=80
    )


    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        new_name = sanitize_channel_name(
            str(self.name)
        )

        await interaction.channel.edit(
            name=new_name
        )

        await interaction.response.send_message(
            f"✅ Ticket renamed to `{new_name}`",
            ephemeral=True
        )


class AddUserModal(
    discord.ui.Modal,
    title="Add User"
):

    user_id = discord.ui.TextInput(
        label="User ID",
        required=True,
        placeholder="Paste the Discord user ID"
    )


    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        try:

            member = interaction.guild.get_member(
                int(str(self.user_id))
            )

            if not member:

                return await interaction.response.send_message(
                    "❌ User not found.",
                    ephemeral=True
                )

            await interaction.channel.set_permissions(
                member,
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

            await interaction.response.send_message(
                f"✅ Added {member.mention} to this ticket.",
                ephemeral=True
            )

        except ValueError:

            await interaction.response.send_message(
                "❌ Invalid user ID.",
                ephemeral=True
            )


class TicketControlsView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=None
        )


    @discord.ui.button(
        label="Mark Completed",
        emoji="❔",
        style=discord.ButtonStyle.secondary
    )
    async def completed(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.send_message(
            "✅ This ticket has been marked as completed.",
            ephemeral=False
        )


    @discord.ui.button(
        label="Save Transcript",
        emoji="☷",
        style=discord.ButtonStyle.secondary
    )
    async def save_transcript(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        file = await generate_transcript(
            interaction.channel
        )

        await interaction.response.send_message(
            "📄 Transcript generated.",
            file=file,
            ephemeral=True
        )


    @discord.ui.button(
        label="Rename",
        emoji="☷",
        style=discord.ButtonStyle.secondary
    )
    async def rename(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.send_modal(
            RenameModal()
        )


    @discord.ui.button(
        label="Close Ticket",
        emoji="🔒",
        style=discord.ButtonStyle.danger
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await close_ticket_system(
            interaction
        )


    @discord.ui.button(
        label="Add a user",
        emoji="➜",
        style=discord.ButtonStyle.secondary
    )
    async def add_user(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.send_modal(
            AddUserModal()
        )


# ============================================================
# CLOSE TICKET + TRANSCRIPT
# ============================================================

async def close_ticket_system(
    interaction: discord.Interaction
):

    channel = interaction.channel

    if channel.id not in tickets:

        return await interaction.response.send_message(
            "❌ This ticket is not registered.",
            ephemeral=True
        )

    await interaction.response.defer(
        ephemeral=True
    )

    data = tickets[channel.id]

    owner = interaction.guild.get_member(
        data["owner_id"]
    )

    if owner is None:

        try:

            owner = await bot.fetch_user(
                data["owner_id"]
            )

        except Exception:

            owner = None

    # Generate transcript for DM
    transcript_for_owner = await generate_transcript(
        channel
    )

    # DM ticket owner
    if owner:

        try:

            await owner.send(
                "📄 Your ticket has been closed. "
                "Here is your transcript:",
                file=transcript_for_owner
            )

        except discord.Forbidden:

            pass

    # Generate another copy for logs
    log_channel = interaction.guild.get_channel(
        TRANSCRIPT_CHANNEL_ID
    )

    if isinstance(
        log_channel,
        discord.TextChannel
    ):

        transcript_for_logs = await generate_transcript(
            channel
        )

        await log_channel.send(
            content=(
                f"📁 **Ticket Closed**\n"
                f"Owner: <@{data['owner_id']}>\n"
                f"Channel: `{channel.name}`\n"
                f"Type: `{data['type']}`"
            ),
            file=transcript_for_logs
        )

    await interaction.followup.send(
        "🔒 Ticket closed. The transcript was "
        "automatically sent to the ticket owner.",
        ephemeral=True
    )

    await asyncio.sleep(3)

    tickets.pop(
        channel.id,
        None
    )

    await channel.delete(
        reason="Ticket closed"
    )


# ============================================================
# SLASH COMMAND
# ============================================================

@tree.command(
    name="orderpanel",
    description="Send the order and support panel"
)
@app_commands.default_permissions(
    manage_guild=True
)
async def orderpanel(
    interaction: discord.Interaction
):

    await interaction.response.send_message(
        embed=order_panel_embed(),
        view=OrderPanelView()
    )


# ============================================================
# BOT READY
# ============================================================

@bot.event
async def on_ready():

    try:

        await tree.sync()

        print(
            f"Logged in as {bot.user}"
        )

    except Exception as error:

        print(
            f"Failed to sync commands: {error}"
        )


# ============================================================
# RUN
# ============================================================

if not TOKEN:

    raise RuntimeError(
        "DISCORD_TOKEN environment variable is missing."
    )

bot.run(TOKEN)
