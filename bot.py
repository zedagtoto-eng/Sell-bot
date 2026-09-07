import os
import re
import html
import asyncio
import io

import discord
from discord.ext import commands


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")

# Put your IDs here
TICKET_CATEGORY_ID = 1546004427931123722
TRANSCRIPT_CHANNEL_ID = 1546005594605879296
TERMS_CHANNEL_ID = 1545851197767024772

# Staff role allowed to use $say and $close
# Put 0 if only the server owner should be allowed.
STAFF_ROLE_ID = 1546004683871490170

# Your ticket panel banner
TICKETS_BANNER_URL = "https://i.imgur.com/0MxHVkI.png"

# Discord blurple / blue
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


# ============================================================
# TICKET STORAGE
# ============================================================

tickets = {}


# ============================================================
# HELPERS
# ============================================================

def sanitize_channel_name(text: str):

    text = text.lower()

    text = re.sub(
        r"[^a-z0-9-]",
        "-",
        text
    )

    text = re.sub(
        r"-+",
        "-",
        text
    )

    return text.strip("-")[:80]


def ticket_id(channel_id: int):

    return f"#{str(channel_id)[-6:]}"


def is_owner_or_staff(member):

    if member.guild.owner_id == member.id:

        return True

    if STAFF_ROLE_ID:

        role = member.guild.get_role(
            STAFF_ROLE_ID
        )

        if role and role in member.roles:

            return True

    return False


def get_staff_overwrite(
    guild: discord.Guild
):

    overwrites = {

        guild.default_role: discord.PermissionOverwrite(
            view_channel=False
        )

    }

    if STAFF_ROLE_ID:

        role = guild.get_role(
            STAFF_ROLE_ID
        )

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

    category = guild.get_channel(
        TICKET_CATEGORY_ID
    )

    if not isinstance(
        category,
        discord.CategoryChannel
    ):

        raise RuntimeError(
            "TICKET_CATEGORY_ID is not a valid category."
        )

    overwrites = get_staff_overwrite(
        guild
    )

    overwrites[interaction.user] = (
        discord.PermissionOverwrite(

            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True

        )
    )

    channel = await guild.create_text_channel(

        name=name,
        category=category,
        overwrites=overwrites,
        reason=f"Ticket created by {interaction.user}"

    )

    return channel


# ============================================================
# TRANSCRIPT
# ============================================================

async def generate_transcript(
    channel: discord.TextChannel
):

    messages = []

    async for message in channel.history(

        limit=None,
        oldest_first=True

    ):

        timestamp = message.created_at.strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

        content = html.escape(
            message.content or ""
        )

        attachments = ""

        for attachment in message.attachments:

            attachments += (

                f'<br><a href="{attachment.url}">'
                f'{html.escape(attachment.filename)}'
                f"</a>"

            )

        embeds = ""

        for embed in message.embeds:

            if embed.title:

                embeds += (

                    "<br><b>Embed:</b> "
                    + html.escape(
                        embed.title
                    )

                )

            if embed.description:

                embeds += (

                    "<br>"
                    + html.escape(
                        embed.description
                    )

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

<h1>Elite Stock Ticket Transcript</h1>

<p>
Channel: #{html.escape(channel.name)}
</p>

{"".join(messages)}

</body>

</html>
"""

    filename = (

        sanitize_channel_name(
            channel.name
        )

        + "-transcript.html"

    )

    return discord.File(

        fp=io.BytesIO(
            transcript.encode(
                "utf-8"
            )
        ),

        filename=filename

    )


# ============================================================
# MAIN ORDER PANEL
# ============================================================

class OrderTypeSelect(
    discord.ui.Select
):

    def __init__(self):

        options = [

            discord.SelectOption(

                label="Purchase",
                description="Create a ticket to purchase a product.",
                emoji="🛒",
                value="purchase"

            ),

            discord.SelectOption(

                label="Support",
                description="Create a ticket if you need assistance.",
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


class OrderPanelView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=None
        )

        self.add_item(
            OrderTypeSelect()
        )


def order_panel_embed():

    embed = discord.Embed(

        title="Order Panel",

        description=(

            "Create a service request with **Elite Stock**.\n"
            "Our team will be with you shortly.\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n\n"

            "☷ **Start your order**\n\n"

            "› Select a service from the menu below to "
            "open your ticket.\n\n"

            "━━━━━━━━━━━━━━━━━━━━"

        ),

        color=EMBED_COLOR

    )

    if TICKETS_BANNER_URL:

        embed.set_image(
            url=TICKETS_BANNER_URL
        )

    embed.set_footer(

        text="🛡️ Elite Stock • Ticket System"

    )

    return embed


# ============================================================
# PURCHASE TERMS
# ============================================================

def purchase_terms_embed():

    return discord.Embed(

        description=(

            "# 🛡️ Purchase Terms\n\n"

            "Please read our Terms of Service "
            "before continuing with your purchase.\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n\n"

            "Click **Accept and Continue** "
            "if you agree to our terms.\n\n"

            "Click **Cancel** "
            "if you do not want to continue.\n\n"

            "━━━━━━━━━━━━━━━━━━━━"

        ),

        color=EMBED_COLOR

    )


class PurchaseTermsView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=300
        )

        if TERMS_CHANNEL_ID:

            self.add_item(

                discord.ui.Button(

                    label="Terms of Service",
                    emoji="📜",
                    style=discord.ButtonStyle.link,

                    url=(
                        "https://discord.com/channels/"
                        f"{0}/{TERMS_CHANNEL_ID}"
                    )

                )

            )


    @discord.ui.button(

        label="Accept and Continue",
        emoji="✅",
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
        emoji="✖️",
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
# PAYMENT
# ============================================================

def payment_embed():

    return discord.Embed(

        description=(

            "# 💳 Select Payment Method\n\n"

            "Choose your preferred payment "
            "method below.\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n\n"

            "🟣 **SOL**\n"
            "🟢 **LTC**\n"
            "💳 **Other Payment Method**"

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


class PaymentView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=300
        )

        self.add_item(
            PaymentButton("SOL", "🟣")
        )

        self.add_item(
            PaymentButton("LTC", "🟢")
        )

        self.add_item(
            PaymentButton("Other", "💳")
        )


# ============================================================
# PURCHASE TICKET
# ============================================================

async def create_purchase_ticket(

    interaction: discord.Interaction,
    payment_method: str

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
        f"purchase-{short_id}"

    )

    tickets[channel.id] = {

        "owner_id": interaction.user.id,
        "type": "purchase",
        "payment": payment_method,
        "product": "Not selected"

    }

    mention = interaction.user.mention

    if STAFF_ROLE_ID:

        mention += (
            f" <@&{STAFF_ROLE_ID}>"
        )

    await channel.send(
        content=mention
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

            "# 🛒 Purchase Ticket\n\n"

            f"Hello {user.mention}!\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n\n"

            "🛒 **Product**\n"
            f"{product}\n\n"

            "💳 **Payment Method**\n"
            f"{payment_method}\n\n"

            "👍 A staff member will assist you shortly.\n\n"

            "━━━━━━━━━━━━━━━━━━━━"

        ),

        color=EMBED_COLOR

    )


def product_selection_embed():

    return discord.Embed(

        description=(

            "# 🛒 What are you purchasing?\n\n"

            "Select the product category "
            "from the menu below."

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

        if interaction.channel.id in tickets:

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
        emoji="🔒",
        style=discord.ButtonStyle.secondary

    )

    async def close_ticket(

        self,
        interaction: discord.Interaction,
        button: discord.ui.Button

    ):

        if not is_owner_or_staff(
            interaction.user
        ):

            return await interaction.response.send_message(

                "❌ Only the server owner or "
                "assigned staff role can close tickets.",

                ephemeral=True

            )

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
            "concern": str(
                self.concern
            )

        }

        mention = interaction.user.mention

        if STAFF_ROLE_ID:

            mention += (
                f" <@&{STAFF_ROLE_ID}>"
            )

        await channel.send(
            content=mention
        )

        await channel.send(

            embed=support_ticket_embed(

                interaction.user,
                channel,
                str(self.concern)

            ),

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

            f"Hey {user.mention}!\n"
            "A support member will be with you shortly.\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n\n"

            f"📞 **Ticket ID** · "
            f"{ticket_id(channel.id)}\n\n"

            f"💬 **Concern** · "
            f"{concern}\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n\n"

            "**R&D Market**"

        ),

        color=EMBED_COLOR

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

        if not is_owner_or_staff(
            interaction.user
        ):

            return await interaction.response.send_message(

                "❌ You do not have permission.",
                ephemeral=True

            )

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

        if not is_owner_or_staff(
            interaction.user
        ):

            return await interaction.response.send_message(

                "❌ You do not have permission.",
                ephemeral=True

            )

        try:

            member = interaction.guild.get_member(

                int(
                    str(self.user_id)
                )

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

                f"✅ Added {member.mention} "
                f"to this ticket.",

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
        emoji="✅",
        style=discord.ButtonStyle.secondary

    )

    async def completed(

        self,
        interaction: discord.Interaction,
        button: discord.ui.Button

    ):

        if not is_owner_or_staff(
            interaction.user
        ):

            return await interaction.response.send_message(

                "❌ Staff only.",
                ephemeral=True

            )

        await interaction.response.send_message(

            "✅ This ticket has been marked "
            "as completed."

        )


    @discord.ui.button(

        label="Save Transcript",
        emoji="📄",
        style=discord.ButtonStyle.secondary

    )

    async def save_transcript(

        self,
        interaction: discord.Interaction,
        button: discord.ui.Button

    ):

        if not is_owner_or_staff(
            interaction.user
        ):

            return await interaction.response.send_message(

                "❌ Staff only.",
                ephemeral=True

            )

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
        emoji="✏️",
        style=discord.ButtonStyle.secondary

    )

    async def rename(

        self,
        interaction: discord.Interaction,
        button: discord.ui.Button

    ):

        if not is_owner_or_staff(
            interaction.user
        ):

            return await interaction.response.send_message(

                "❌ Staff only.",
                ephemeral=True

            )

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

        if not is_owner_or_staff(
            interaction.user
        ):

            return await interaction.response.send_message(

                "❌ Staff only.",
                ephemeral=True

            )

        await close_ticket_system(
            interaction
        )


    @discord.ui.button(

        label="Add a user",
        emoji="➕",
        style=discord.ButtonStyle.secondary

    )

    async def add_user(

        self,
        interaction: discord.Interaction,
        button: discord.ui.Button

    ):

        if not is_owner_or_staff(
            interaction.user
        ):

            return await interaction.response.send_message(

                "❌ Staff only.",
                ephemeral=True

            )

        await interaction.response.send_modal(
            AddUserModal()
        )


# ============================================================
# CLOSE TICKET SYSTEM
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


    if owner:

        try:

            transcript_for_owner = (
                await generate_transcript(
                    channel
                )
            )

            await owner.send(

                "📄 Your **Elite Stock** ticket "
                "has been closed.\n\n"
                "Here is your transcript:",

                file=transcript_for_owner

            )

        except discord.Forbidden:

            pass


    log_channel = interaction.guild.get_channel(
        TRANSCRIPT_CHANNEL_ID
    )

    if isinstance(
        log_channel,
        discord.TextChannel
    ):

        transcript_for_logs = (
            await generate_transcript(
                channel
            )
        )

        await log_channel.send(

            content=(

                "📁 **Ticket Closed**\n\n"

                f"Owner: <@{data['owner_id']}>\n"

                f"Channel: `{channel.name}`\n"

                f"Type: `{data['type']}`\n"

                f"Closed by: "
                f"{interaction.user.mention}"

            ),

            file=transcript_for_logs

        )


    await interaction.followup.send(

        "🔒 Ticket closed. The transcript "
        "was automatically sent to the "
        "ticket owner.",

        ephemeral=True

    )

    await asyncio.sleep(2)

    tickets.pop(
        channel.id,
        None
    )

    await channel.delete(

        reason=(
            f"Ticket closed by "
            f"{interaction.user}"
        )

    )


# ============================================================
# $PANEL COMMAND
# ============================================================

@bot.command()
async def panel(ctx):

    if not is_owner_or_staff(
        ctx.author
    ):

        return await ctx.send(

            "❌ Only the server owner or "
            "assigned staff role can use `$panel`."

        )

    await ctx.send(

        embed=order_panel_embed(),
        view=OrderPanelView()

    )


# ============================================================
# $CLOSE COMMAND
# ============================================================

@bot.command()
async def close(ctx):

    if not is_owner_or_staff(
        ctx.author
    ):

        return await ctx.send(

            "❌ Only the **server owner** or "
            "assigned **staff role** can use `$close`."

        )

    channel = ctx.channel

    if channel.id not in tickets:

        return await ctx.send(

            "❌ This is not a registered ticket."

        )

    await ctx.send(

        "🔒 Closing ticket and generating transcript..."

    )

    data = tickets[channel.id]

    owner = ctx.guild.get_member(
        data["owner_id"]
    )

    if owner is None:

        try:

            owner = await bot.fetch_user(
                data["owner_id"]
            )

        except Exception:

            owner = None


    if owner:

        try:

            transcript_for_owner = (
                await generate_transcript(
                    channel
                )
            )

            await owner.send(

                "📄 Your **Elite Stock** ticket "
                "has been closed.\n\n"
                "Here is your transcript:",

                file=transcript_for_owner

            )

        except discord.Forbidden:

            pass


    log_channel = ctx.guild.get_channel(
        TRANSCRIPT_CHANNEL_ID
    )

    if isinstance(
        log_channel,
        discord.TextChannel
    ):

        transcript_for_logs = (
            await generate_transcript(
                channel
            )
        )

        await log_channel.send(

            content=(

                "📁 **Ticket Closed**\n\n"

                f"Owner: <@{data['owner_id']}>\n"

                f"Channel: `{channel.name}`\n"

                f"Type: `{data['type']}`\n"

                f"Closed by: {ctx.author.mention}"

            ),

            file=transcript_for_logs

        )


    tickets.pop(
        channel.id,
        None
    )

    await asyncio.sleep(2)

    await channel.delete(

        reason=f"Ticket closed by {ctx.author}"

    )


# ============================================================
# $SAY COMMAND
# ============================================================

@bot.command()
async def say(

    ctx,
    *,
    message: str

):

    if not is_owner_or_staff(
        ctx.author
    ):

        return await ctx.send(

            "❌ Only the **server owner** or "
            "assigned **staff role** can use `$say`."

        )

    await ctx.send(
        message
    )

    try:

        await ctx.message.delete()

    except discord.Forbidden:

        pass


@say.error
async def say_error(
    ctx,
    error
):

    if isinstance(
        error,
        commands.MissingRequiredArgument
    ):

        await ctx.send(

            "❌ Usage: `$say <message>`"

        )


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():

    bot.add_view(
        OrderPanelView()
    )

    bot.add_view(
        ProductSelectView()
    )

    bot.add_view(
        PurchaseCloseView()
    )

    bot.add_view(
        TicketControlsView()
    )

    print(
        f"Logged in as {bot.user}"
    )


# ============================================================
# RUN
# ============================================================

if not TOKEN:

    raise RuntimeError(

        "DISCORD_TOKEN environment "
        "variable is missing."

    )


bot.run(TOKEN)
