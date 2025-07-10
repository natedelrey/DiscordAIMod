import requests
import os
from dotenv import load_dotenv

# Load your bot token from .env
load_dotenv()
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
APPLICATION_ID = os.getenv("DISCORD_APP_ID")  # You may need to add this to your .env
COMMAND_IDS = [
    "1384297978046713987",
    "1384297978046713988",
    "1384297978046713992",
    "1384297978046713990",
    "1384297978046713991",
    "1384297978046713989"
]

headers = {
    "Authorization": f"Bot {BOT_TOKEN}"
}

for cmd_id in COMMAND_IDS:
    url = f"https://discord.com/api/v10/applications/{APPLICATION_ID}/commands/{cmd_id}"
    response = requests.delete(url, headers=headers)

    if response.status_code == 204:
        print(f"✅ Deleted command {cmd_id}")
    else:
        print(f"❌ Failed to delete {cmd_id}: {response.status_code} - {response.text}")
