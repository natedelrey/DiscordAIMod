import discord
from discord.ext import commands
import os
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
print("Loaded .env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
print("DISCORD_TOKEN:", DISCORD_TOKEN)
print("OPENAI_API_KEY:", OPENAI_API_KEY)

# OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Constants
LOG_CHANNEL_ID = 1384748303845167185
JAIL_ROLE_ID = 1292210864128004147
STAFF_ROLE_IDS = {
    1279603929356828682, 1161044541466484816, 1139374785592295484,
    1269504508912992328, 1279604226799964231, 1315356574356734064, 1269517409526616196
}
warning_counts = {}  # key: user_id, value: int (warning count)

# Moderation logic (using new OpenAI client)
async def moderate_message(message_content):
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict Discord moderation assistant for a Black Lives Matter server. "
                        "Your job is to detect racism, slurs, hate speech, or subtle dog whistles, especially ones meant to belittle or dismiss BLM. "
                        "This includes terms like 'BLDM' (Black Lives Don't Matter), 'TND', 4chan-style phrases, mocking slogans like 'We wuz kings', or use of 'coon', 'chimp', 'monkey', etc. "
                        "You do not tolerate veiled bigotry, coded language, or edgy 'jokes' at the expense of Black communities. "
                        "If the message is even *borderline offensive* or *deliberately provocative*, respond with 'DELETE'. Otherwise, respond with 'SAFE'. "
                        "Respond only with 'SAFE' or 'DELETE' — no explanations."
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

    if any(role.id in STAFF_ROLE_IDS for role in message.author.roles):
        return

    verdict = await moderate_message(message.content)

    if verdict == "DELETE":
        try:
            await message.delete()
            await log_violation(message)
            await warn_user(message.author, message.guild)
        except discord.Forbidden:
            print("⚠️ Missing permissions to delete message or manage roles.")
    else:
        await bot.process_commands(message)

async def log_violation(message):
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(
            title="🛑 Message Deleted by AI Mod",
            description=f"**User:** {message.author.mention}\n"
                        f"**Channel:** {message.channel.mention}\n"
                        f"**Content:** {message.content}",
            color=discord.Color.red()
        )
        await log_channel.send(embed=embed)

async def warn_user(member, guild):
    user_id = member.id
    warning_counts[user_id] = warning_counts.get(user_id, 0) + 1
    warnings = warning_counts[user_id]

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
                warning_counts[user_id] = 0
        except discord.Forbidden:
            print("⚠️ Missing permission to modify roles.")

# Start the bot
try:
    bot.run(DISCORD_TOKEN)
except Exception as e:
    print(f"❌ Bot failed to run: {e}")
