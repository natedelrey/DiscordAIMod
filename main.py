import discord
from discord.ext import commands
import os
from openai import OpenAI
from dotenv import load_dotenv
import json

# Load environment variables
load_dotenv()
print("Loaded .env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
print("DISCORD_TOKEN:", DISCORD_TOKEN)
print("OPENAI_API_KEY:", OPENAI_API_KEY)

client = OpenAI(api_key=OPENAI_API_KEY)

intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

LOG_CHANNEL_ID = 1384748303845167185
JAIL_ROLE_ID = 1292210864128004147
STAFF_ROLE_IDS = {
    1279603929356828682, 1161044541466484816, 1139374785592295484,
    1269504508912992328, 1279604226799964231, 1315356574356734064, 1269517409526616196
}
warning_counts = {}
whitelist = []  # list of whitelisted words/phrases

JAILED_USERS_FILE = "jailed_users.json"

def load_jailed_users():
    try:
        with open(JAILED_USERS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_jailed_users(user_ids):
    with open(JAILED_USERS_FILE, "w") as f:
        json.dump(user_ids, f)

def is_staff(member):
    return any(role.id in STAFF_ROLE_IDS for role in member.roles)

async def moderate_message(message_content):
    for phrase in whitelist:
        if phrase in message_content:
            return "SAFE"
    try:
        response = client.chat.completions.create(
            model="gpt-4-1106-preview",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict Discord moderation assistant for a Black Lives Matter server. "
                        "Your job is to detect racism, slurs, hate speech, or subtle dog whistles, especially ones meant to belittle or dismiss BLM. "
                        "This includes terms like 'BLDM', 'TND', 'We wuz kings', or 'coon', 'chimp', 'monkey', etc. "
                        "You do not tolerate veiled bigotry, coded language, or edgy 'jokes' at the expense of Black communities. "
                        "If the message is even *borderline offensive*, respond with 'DELETE'. Otherwise, respond with 'SAFE'. "
                        "Respond only with 'SAFE' or 'DELETE'."
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
    jailed_users = load_jailed_users()
    if str(member.id) in jailed_users:
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

                jailed_users = load_jailed_users()
                if user_id not in jailed_users:
                    jailed_users.append(user_id)
                    save_jailed_users(jailed_users)
        except discord.Forbidden:
            print("⚠️ Missing permission to modify roles.")

@bot.command()
async def summarize(ctx, limit: int = 20):
    if limit > 100:
        await ctx.send("❌ You can only summarize up to 100 messages at a time.")
        return

    try:
        messages = [msg async for msg in ctx.channel.history(limit=limit)]
        content_to_summarize = "\n".join([
            f"{msg.author.name}: {msg.content}"
            for msg in reversed(messages) if not msg.author.bot and msg.content
        ])

        if not content_to_summarize.strip():
            await ctx.send("⚠️ No messages to summarize.")
            return

        response = client.chat.completions.create(
            model="gpt-4-1106-preview",
            messages=[
                {"role": "system", "content": "Summarize the following Discord conversation in a short, clear paragraph."},
                {"role": "user", "content": content_to_summarize}
            ],
            temperature=0.5
        )
        summary = response.choices[0].message.content.strip()
        await ctx.send(f"📝 **Summary of the last {limit} messages:**\n{summary}")
    except Exception as e:
        await ctx.send("⚠️ Failed to summarize messages.")
        print("Summary error:", e)

@bot.command()
async def dm(ctx, user: discord.User, *, message: str):
    if not is_staff(ctx.author):
        await ctx.send("❌ You do not have permission to use this command.")
        return

    try:
        await user.send(message)
        await ctx.send(f"📬 Message sent to {user.mention}.")
    except Exception as e:
        await ctx.send("⚠️ Failed to send the message.")
        print(f"DM error: {e}")

@bot.command()
async def whitelist_add(ctx, *, phrase):
    if not is_staff(ctx.author):
        return
    if phrase not in whitelist:
        whitelist.append(phrase)
        await ctx.send(f"✅ Whitelisted: `{phrase}`")
    else:
        await ctx.send("Phrase is already whitelisted.")

@bot.command()
async def whitelist_remove(ctx, *, phrase):
    if not is_staff(ctx.author):
        return
    if phrase in whitelist:
        whitelist.remove(phrase)
        await ctx.send(f"❌ Removed from whitelist: `{phrase}`")
    else:
        await ctx.send("Phrase not found in whitelist.")

@bot.command()
async def whitelist_list(ctx):
    if not is_staff(ctx.author):
        return
    if not whitelist:
        await ctx.send("⚠️ Whitelist is empty.")
    else:
        await ctx.send("📜 Whitelisted phrases:\n" + "\n".join(f"- {w}" for w in whitelist))

@bot.command()
async def commands(ctx):
    cmds = [
        "!summarize [#] - Summarize recent messages",
        "!dm @user message - DM a user (staff only)",
        "!whitelist add/remove/list - Manage AI ignore list (staff only)",
        "!removewarnings @user - Reset warning count (staff only)"
    ]
    await ctx.send("📘 Available Commands:\n" + "\n".join(cmds))

@bot.command()
async def removewarnings(ctx, member: discord.Member):
    if not is_staff(ctx.author):
        return
    user_id = str(member.id)
    if user_id in warning_counts:
        warning_counts[user_id] = 0
        await ctx.send(f"✅ Warnings for {member.mention} have been cleared.")
    else:
        await ctx.send(f"ℹ️ {member.mention} has no warnings.")

# Start the bot
try:
    bot.run(DISCORD_TOKEN)
except Exception as e:
    print(f"❌ Bot failed to run: {e}")
