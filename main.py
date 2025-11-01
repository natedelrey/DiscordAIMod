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
STAFF_ROLE_IDS = {
    1279603929356828682, 1161044541466484816, 1139374785592295484,
    1269504508912992328, 1279604226799964231, 1315356574356734064, 1269517409526616196
}

Base = declarative_base()
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

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
                await member.send("🚨 You have been jailed for repeated rule violations.")
                await set_warnings(user_id, 0)
                await add_to_jailed(user_id)
        except discord.Forbidden:
            print("⚠️ Missing permission to modify roles.")

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
