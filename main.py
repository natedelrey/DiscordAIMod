import discord
from discord.ext import commands
import os
from openai import AsyncOpenAI
from dotenv import load_dotenv
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, String, Integer, select

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True

class MyBot(commands.Bot):
    async def setup_hook(self):
        await init_db()
        for command in [
            removewarnings,
            whitelist_add,
            whitelist_remove,
            whitelist_list,
            dm,
            summarize,
            commands,
            exempt,
            exemptremove,
            exempts_list,
        ]:
            self.tree.add_command(command)

bot = MyBot(command_prefix="!", intents=intents)

debug_guilds = []  # optionally add your guild ID(s) here for faster dev

LOG_CHANNEL_ID = 1384748303845167185
JAIL_ROLE_ID = 1292210864128004147
REVIEW_CHANNEL_ID = 1457762507484565687
TICKET_CATEGORY_ID = 1364267276169121872
STAFF_ROLE_IDS = {
    1279603929356828682, 1161044541466484816, 1139374785592295484,
    1269504508912992328, 1279604226799964231, 1315356574356734064, 1269517409526616196
}

Base = declarative_base()
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

flagged_messages = {}
pending_jail_reviews = {}
pending_jail_reviews_by_user = {}

class JailedUser(Base):
    __tablename__ = 'jailed_users'
    user_id = Column(String, primary_key=True)

class Warning(Base):
    __tablename__ = 'warnings'
    user_id = Column(String, primary_key=True)
    count = Column(Integer, default=0)

class WhitelistEntry(Base):
    __tablename__ = 'whitelist'
    phrase = Column(String, primary_key=True)

class ExemptUser(Base):
    __tablename__ = 'exempt_users'
    user_id = Column(String, primary_key=True)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

def is_staff(member):
    return any(role.id in STAFF_ROLE_IDS for role in member.roles)

async def get_warnings(user_id):
    async with AsyncSessionLocal() as session:
        result = await session.get(Warning, user_id)
        return result.count if result else 0

async def set_warnings(user_id, count):
    async with AsyncSessionLocal() as session:
        obj = await session.get(Warning, user_id)
        if obj:
            obj.count = count
        else:
            obj = Warning(user_id=user_id, count=count)
            session.add(obj)
        await session.commit()

async def add_to_jailed(user_id):
    async with AsyncSessionLocal() as session:
        if not await session.get(JailedUser, user_id):
            session.add(JailedUser(user_id=user_id))
            await session.commit()

async def remove_from_jailed(user_id):
    async with AsyncSessionLocal() as session:
        record = await session.get(JailedUser, user_id)
        if record:
            await session.delete(record)
            await session.commit()

async def is_jailed(user_id):
    async with AsyncSessionLocal() as session:
        return await session.get(JailedUser, user_id) is not None

async def is_whitelisted(message_content):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(WhitelistEntry))
        phrases = [row[0].phrase for row in result.all()]
        return any(phrase in message_content for phrase in phrases)

async def is_exempt(user_id):
    async with AsyncSessionLocal() as session:
        return await session.get(ExemptUser, user_id) is not None

async def add_exempt_user(user_id):
    async with AsyncSessionLocal() as session:
        if not await session.get(ExemptUser, user_id):
            session.add(ExemptUser(user_id=user_id))
            await session.commit()

async def remove_exempt_user(user_id):
    async with AsyncSessionLocal() as session:
        record = await session.get(ExemptUser, user_id)
        if record:
            await session.delete(record)
            await session.commit()

async def list_exempt_users():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ExemptUser))
        return [row[0].user_id for row in result.all()]

async def moderate_message(message_content, *, lenient=False):
    if await is_whitelisted(message_content):
        return "SAFE"
    try:
        if lenient:
            system_prompt = (
                "You are an AI content moderation system for a Discord server.\n\n"
                "Flag messages only when they contain explicit, unmistakable racist or hate-filled language.\n"
                "Ignore mild profanity, jokes, or context unless the message clearly includes outright racism or hate speech.\n\n"
                "If the message is explicitly racist or hate speech, respond only with: DELETE\n"
                "If it is not, respond only with: SAFE\n"
                "Do not explain your decision."
            )
        else:
            system_prompt = (
                "You are an AI content moderation system for a Discord server.\n\n"
                "Flag messages that contain clear or strongly implied:\n"
                "- Racism, hate speech, or slurs (even if censored)\n"
                "- Ableism, transphobia, homophobia, or sexism\n"
                "- Harassment, threats, incitement, or targeted bullying\n"
                "- Known dog whistles or coded hate terms\n\n"
                "Be alert for attempts to bypass filters using misspellings, emojis, slang, acronyms, or indirect phrasing — but do not flag unless the message is *reasonably likely* to be harmful or targeted.\n\n"
                "If the message violates these guidelines, respond only with: DELETE\n"
                "If it does not, respond only with: SAFE\n"
                "Do not explain your decision."
            )
        response = await openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message_content}
            ],
            temperature=0
        )
        return response.choices[0].message.content.strip().upper()
    except Exception as e:
        print(f"Moderation error: {e}")
        return "SAFE"

@bot.event
async def on_ready():
    print(f"✅ Bot connected as {bot.user}")
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(type=discord.ActivityType.watching, name="for hate speech 👀")
    )
    try:
        synced = await bot.tree.sync()
        print(f"🔁 Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if isinstance(message.channel, discord.TextChannel):
        if (
            message.channel.category_id == TICKET_CATEGORY_ID
            and message.channel.name.startswith("ticket")
        ):
            await bot.process_commands(message)
            return

    if is_staff(message.author):
        await bot.process_commands(message)
        return

    user_id = str(message.author.id)
    lenient = await is_exempt(user_id)
    verdict = await moderate_message(message.content, lenient=lenient)

    if verdict == "DELETE":
        try:
            await message.delete()
            await log_violation(message)
            if not is_staff(message.author):
                await warn_user(message.author, message.guild)
        except discord.Forbidden:
            print("⚠️ Missing permissions to delete message or manage roles.")

    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    if await is_jailed(str(member.id)):
        try:
            await member.ban(reason="Attempted to bypass jail role by rejoining.")
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(f"🚫 {member.mention} was banned for rejoining after being jailed.")
        except Exception as e:
            print(f"Failed to auto-ban {member.name}: {e}")

async def log_violation(message):
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    user_id = str(message.author.id)
    entry = f"#{message.channel} ({message.channel.id}): {message.content}"
    user_messages = flagged_messages.setdefault(user_id, [])
    user_messages.append(entry)
    if len(user_messages) > 5:
        flagged_messages[user_id] = user_messages[-5:]
    if log_channel:
        embed = discord.Embed(
            title="🛑 Message Deleted by AI Mod",
            description=f"**User:** {message.author.mention}\n**Channel:** {message.channel.mention}\n**Content:** {message.content}",
            color=discord.Color.red()
        )
        await log_channel.send(embed=embed)

async def warn_user(member, guild):
    user_id = str(member.id)
    warnings = await get_warnings(user_id)
    warnings += 1
    await set_warnings(user_id, warnings)

    try:
        await member.send(f"⚠️ You have been warned for violating server rules. Warning {warnings}/3.")
    except:
        pass

    if warnings >= 3:
        try:
            jail_role = guild.get_role(JAIL_ROLE_ID)
            if jail_role:
                await member.add_roles(jail_role)
                await member.send(
                    "🚨 You have been jailed for repeated rule violations. "
                    "Your case is pending our moderation team's review. "
                    "Expect a response soon, and if you have any further questions, "
                    "please open a ticket."
                )
                await set_warnings(user_id, 0)
                await add_to_jailed(user_id)
                await request_jail_review(member, guild)
        except discord.Forbidden:
            print("⚠️ Missing permission to modify roles.")

async def request_jail_review(member, guild):
    review_channel = bot.get_channel(REVIEW_CHANNEL_ID) or guild.get_channel(REVIEW_CHANNEL_ID)
    if not review_channel:
        try:
            review_channel = await guild.fetch_channel(REVIEW_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden) as e:
            print(f"⚠️ Unable to access jail review channel {REVIEW_CHANNEL_ID}: {e}")
            return

    user_id = str(member.id)
    existing_message_id = pending_jail_reviews_by_user.get(user_id)
    if existing_message_id:
        try:
            existing_message = await review_channel.fetch_message(existing_message_id)
        except discord.NotFound:
            pending_jail_reviews.pop(existing_message_id, None)
            pending_jail_reviews_by_user.pop(user_id, None)
        else:
            try:
                await existing_message.reply(
                    "⚠️ Additional jail trigger detected while review is pending.",
                    mention_author=False,
                )
            except discord.Forbidden:
                pass
            return
    messages = flagged_messages.get(user_id, [])
    if messages:
        formatted_messages = "\n".join(f"- {entry}" for entry in messages)
    else:
        formatted_messages = "- No cached flagged messages found."

    embed = discord.Embed(
        title="🚨 Jail Review Required",
        description=(
            f"**User:** {member.mention} ({member.id})\n"
            "Use the buttons below to unjail + exempt, or keep jailed."
        ),
        color=discord.Color.orange()
    )
    embed.add_field(name="Flagged Messages", value=formatted_messages, inline=False)
    try:
        review_message = await review_channel.send(embed=embed, view=JailReviewView())
    except discord.Forbidden as e:
        print(f"⚠️ Missing permission to send jail review message: {e}")
        return
    pending_jail_reviews[review_message.id] = user_id
    pending_jail_reviews_by_user[user_id] = review_message.id

async def close_jail_review_message(channel, message_id, moderator, decision):
    try:
        message = await channel.fetch_message(message_id)
    except discord.NotFound:
        return
    close_text = f"Closed by {moderator.mention}, jailed deemed {decision}."
    await message.edit(content=close_text, embed=None, view=None)
    try:
        await message.clear_reactions()
    except discord.Forbidden:
        pass

async def handle_jail_review_decision(interaction: discord.Interaction, decision: str):
    message = interaction.message
    if not message or message.id not in pending_jail_reviews:
        await interaction.response.send_message("⚠️ This jail review is already closed.", ephemeral=True)
        return

    if not interaction.guild:
        await interaction.response.send_message("⚠️ This action must be used in a server.", ephemeral=True)
        return

    moderator = interaction.user
    if not isinstance(moderator, discord.Member) or not is_staff(moderator):
        await interaction.response.send_message("⚠️ You don't have permission to review this.", ephemeral=True)
        return

    target_user_id = pending_jail_reviews[message.id]
    target_member = interaction.guild.get_member(int(target_user_id))
    if not target_member:
        pending_jail_reviews.pop(message.id, None)
        pending_jail_reviews_by_user.pop(target_user_id, None)
        await interaction.response.send_message("⚠️ User no longer in server; review cleared.", ephemeral=True)
        await close_jail_review_message(interaction.channel, message.id, moderator, "closed (user left)")
        return

    await interaction.response.defer(ephemeral=True)

    if decision == "not warranted":
        jail_role = interaction.guild.get_role(JAIL_ROLE_ID)
        if jail_role:
            await target_member.remove_roles(jail_role)
        await remove_from_jailed(target_user_id)
        await add_exempt_user(target_user_id)
        try:
            await target_member.send(
                "✅ After review, you have been unjailed and added to our exempt list. "
                "We apologize for the inconvenience. If you have any other concerns or another issue arises, "
                "please create a ticket."
            )
        except:
            pass
    else:
        try:
            await target_member.send(
                "⚠️ After review by our moderation team, your jail has been deemed correct. "
                "You will not be unjailed unless you create a ticket and request further review."
            )
        except:
            pass

    pending_jail_reviews.pop(message.id, None)
    pending_jail_reviews_by_user.pop(target_user_id, None)
    await close_jail_review_message(interaction.channel, message.id, moderator, decision)
    await interaction.followup.send("✅ Jail review updated.", ephemeral=True)


class JailReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Unjail + Exempt", style=discord.ButtonStyle.success, emoji="✅")
    async def unjail_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_jail_review_decision(interaction, "not warranted")

    @discord.ui.button(label="Keep Jailed", style=discord.ButtonStyle.danger, emoji="⛔")
    async def keep_jailed_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_jail_review_decision(interaction, "correct")

from discord import app_commands

@app_commands.command(name="removewarnings", description="Reset warnings for a user")
@app_commands.checks.has_any_role(*STAFF_ROLE_IDS)
async def removewarnings(interaction: discord.Interaction, member: discord.Member):
    await set_warnings(str(member.id), 0)
    await interaction.response.send_message(f"✅ Warnings for {member.mention} have been cleared.", ephemeral=True)

@app_commands.command(name="whitelist_add", description="Add a phrase to the whitelist")
@app_commands.checks.has_any_role(*STAFF_ROLE_IDS)
async def whitelist_add(interaction: discord.Interaction, phrase: str):
    async with AsyncSessionLocal() as session:
        if not await session.get(WhitelistEntry, phrase):
            session.add(WhitelistEntry(phrase=phrase))
            await session.commit()
    await interaction.response.send_message(f"✅ Added '{phrase}' to the whitelist.", ephemeral=True)

@app_commands.command(name="whitelist_remove", description="Remove a phrase from the whitelist")
@app_commands.checks.has_any_role(*STAFF_ROLE_IDS)
async def whitelist_remove(interaction: discord.Interaction, phrase: str):
    async with AsyncSessionLocal() as session:
        result = await session.get(WhitelistEntry, phrase)
        if result:
            await session.delete(result)
            await session.commit()
            await interaction.response.send_message(f"✅ Removed '{phrase}' from the whitelist.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ That phrase isn't in the whitelist.", ephemeral=True)

@app_commands.command(name="whitelist_list", description="List all whitelisted phrases")
@app_commands.checks.has_any_role(*STAFF_ROLE_IDS)
async def whitelist_list(interaction: discord.Interaction):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(WhitelistEntry))
        phrases = [row[0].phrase for row in result.all()]
    if not phrases:
        await interaction.response.send_message("⚠️ Whitelist is currently empty.", ephemeral=True)
    else:
        await interaction.response.send_message("📃 Whitelisted phrases:\n" + "\n".join(phrases), ephemeral=True)

@app_commands.command(name="dm", description="Send a DM to a user")
@app_commands.checks.has_any_role(*STAFF_ROLE_IDS)
async def dm(interaction: discord.Interaction, user: discord.User, message: str):
    try:
        await user.send(message)
        await interaction.response.send_message(f"📬 Message sent to {user.mention}.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message("⚠️ Failed to send the message.", ephemeral=True)
        print(f"DM error: {e}")

@app_commands.command(name="summarize", description="Summarize recent messages in the channel")
@app_commands.checks.has_any_role(*STAFF_ROLE_IDS)
async def summarize(interaction: discord.Interaction, limit: int = 20):
    if limit > 100:
        await interaction.response.send_message("❌ You can only summarize up to 100 messages at a time.", ephemeral=True)
        return
    try:
        messages = [msg async for msg in interaction.channel.history(limit=limit)]
        content_to_summarize = "\n".join([
            f"{msg.author.name}: {msg.content}"
            for msg in reversed(messages) if not msg.author.bot and msg.content
        ])
        if not content_to_summarize.strip():
            await interaction.response.send_message("⚠️ No messages to summarize.", ephemeral=True)
            return
        response = await openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Summarize the following Discord conversation in a short, clear paragraph."},
                {"role": "user", "content": content_to_summarize}
            ],
            temperature=0.5
        )
        summary = response.choices[0].message.content.strip()
        await interaction.response.send_message(f"📝 **Summary of the last {limit} messages:**\n{summary}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message("⚠️ Failed to summarize messages.", ephemeral=True)
        print("Summary error:", e)

@app_commands.command(name="commands", description="List available staff commands")
@app_commands.checks.has_any_role(*STAFF_ROLE_IDS)
async def commands(interaction: discord.Interaction):
    cmds = [
        "/removewarnings @user - reset warnings",
        "/whitelist_add phrase",
        "/whitelist_remove phrase",
        "/whitelist_list",
        "/dm @user message",
        "/summarize [# of messages]",
        "/exempt @user",
        "/exemptremove @user",
        "/exemtplist"
    ]
    await interaction.response.send_message("🛠️ **Available Staff Commands:**\n" + "\n".join(cmds), ephemeral=True)

@app_commands.command(name="exempt", description="Give a user lenient moderation")
@app_commands.checks.has_any_role(*STAFF_ROLE_IDS)
async def exempt(interaction: discord.Interaction, member: discord.Member):
    await add_exempt_user(str(member.id))
    await interaction.response.send_message(
        f"✅ {member.mention} will now only be flagged for explicit hate speech.",
        ephemeral=True
    )

@app_commands.command(name="exemptremove", description="Remove a user's lenient moderation")
@app_commands.checks.has_any_role(*STAFF_ROLE_IDS)
async def exemptremove(interaction: discord.Interaction, member: discord.Member):
    await remove_exempt_user(str(member.id))
    await interaction.response.send_message(
        f"✅ {member.mention} is now subject to normal moderation.",
        ephemeral=True
    )

@app_commands.command(name="exemtplist", description="List users with lenient moderation")
@app_commands.checks.has_any_role(*STAFF_ROLE_IDS)
async def exempts_list(interaction: discord.Interaction):
    user_ids = await list_exempt_users()
    if not user_ids:
        await interaction.response.send_message("ℹ️ No users are currently exempt.", ephemeral=True)
        return

    mentions = []
    for user_id in user_ids:
        member = interaction.guild.get_member(int(user_id)) if interaction.guild else None
        mentions.append(member.mention if member else f"<@{user_id}>")

    await interaction.response.send_message(
        "📜 **Exempt Users:**\n" + "\n".join(mentions),
        ephemeral=True
    )

try:
    bot.run(DISCORD_TOKEN)
except Exception as e:
    print(f"❌ Bot failed to run: {e}")
