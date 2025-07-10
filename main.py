import discord
from discord.ext import commands
import os
from openai import OpenAI
from dotenv import load_dotenv
import json
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import Column, String, Integer, select, delete

# Load environment variables
load_dotenv()
print("Loaded .env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
print("DISCORD_TOKEN:", DISCORD_TOKEN)
print("OPENAI_API_KEY:", OPENAI_API_KEY)

client = OpenAI(api_key=OPENAI_API_KEY)

intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True

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

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

class MyBot(commands.Bot):
    async def setup_hook(self):
        await init_db()

bot = MyBot(command_prefix="!", intents=intents)

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

async def moderate_message(message_content):
    if await is_whitelisted(message_content):
        return "SAFE"
    try:
        response = client.chat.completions.create(
            model="gpt-4-1106-preview",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict Discord moderation assistant for a Black Lives Matter server. "
                        "Your job is to detect racism, slurs, hate speech, or subtle dog whistles..."
                    )
                },
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


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    verdict = await moderate_message(message.content)

    if verdict == "DELETE" and not is_staff(message.author):
        try:
            await message.delete()
            await log_violation(message)
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
                for role in member.roles:
                    if role != guild.default_role:
                        await member.remove_roles(role)
                await member.add_roles(jail_role)
                await member.send("🚨 You have been jailed for repeated rule violations.")
                await set_warnings(user_id, 0)
                await add_to_jailed(user_id)
        except discord.Forbidden:
            print("⚠️ Missing permission to modify roles.")

@bot.command()
async def removewarnings(ctx, member: discord.Member):
    if not is_staff(ctx.author):
        return
    await set_warnings(str(member.id), 0)
    await ctx.send(f"✅ Warnings for {member.mention} have been cleared.")

@bot.command()
async def whitelist_add(ctx, *, phrase: str):
    if not is_staff(ctx.author):
        return
    async with AsyncSessionLocal() as session:
        if not await session.get(WhitelistEntry, phrase):
            session.add(WhitelistEntry(phrase=phrase))
            await session.commit()
    await ctx.send(f"✅ Added '{phrase}' to the whitelist.")

@bot.command()
async def whitelist_remove(ctx, *, phrase: str):
    if not is_staff(ctx.author):
        return
    async with AsyncSessionLocal() as session:
        result = await session.get(WhitelistEntry, phrase)
        if result:
            await session.delete(result)
            await session.commit()
            await ctx.send(f"✅ Removed '{phrase}' from the whitelist.")
        else:
            await ctx.send("⚠️ That phrase isn't in the whitelist.")

@bot.command()
async def whitelist_list(ctx):
    if not is_staff(ctx.author):
        return
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(WhitelistEntry))
        phrases = [row[0].phrase for row in result.all()]
    if not phrases:
        await ctx.send("⚠️ Whitelist is currently empty.")
    else:
        await ctx.send("📃 Whitelisted phrases:\n" + "\n".join(phrases))

@bot.command()
async def commands(ctx):
    if not is_staff(ctx.author):
        return
    cmds = [
        "!removewarnings @user - reset warnings",
        "!whitelist_add [phrase]",
        "!whitelist_remove [phrase]",
        "!whitelist_list"
    ]
    await ctx.send("🛠️ **Available Staff Commands:**\n" + "\n".join(cmds))

try:
    bot.run(DISCORD_TOKEN)
except Exception as e:
    print(f"❌ Bot failed to run: {e}")
