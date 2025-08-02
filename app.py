from flask import Flask, redirect, request, session, url_for, render_template
import requests
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
GUILD_ID = os.getenv("GUILD_ID")
ROLE_ID = os.getenv("ROLE_ID")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

DISCORD_API_BASE = "https://discord.com/api"

# Discord bot setup
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot is ready. Logged in as {bot.user}")

@app.route('/')
@app.route('/index')
def index():
    return render_template('index.html')

@app.route('/login')
def login():
    return redirect(f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds.join")

@app.route('/callback')
def callback():
    code = request.args.get("code")
    if not code:
        return "No code provided."

    # Exchange code for access_token
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "scope": "identify guilds.join"
    }

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    r = requests.post(f"{DISCORD_API_BASE}/oauth2/token", data=data, headers=headers)
    r.raise_for_status()
    tokens = r.json()
    access_token = tokens["access_token"]

    # Get user info
    headers = {"Authorization": f"Bearer {access_token}"}
    r = requests.get(f"{DISCORD_API_BASE}/users/@me", headers=headers)
    r.raise_for_status()
    user = r.json()
    user_id = int(user["id"])

    # Add user to guild using guilds.join
    add_headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    add_payload = {
        "access_token": access_token
    }

    add_resp = requests.put(f"{DISCORD_API_BASE}/guilds/{GUILD_ID}/members/{user_id}", headers=add_headers, json=add_payload)
    if add_resp.status_code in (201, 204):
        print("User added to the guild successfully.")
    else:
        print(f"Failed to add user to guild: {add_resp.status_code} - {add_resp.text}")

    # Assign role asynchronously
    bot.loop.create_task(add_role_to_user(user_id))

    return render_template("success.html", username=user["username"])

async def add_role_to_user(user_id):
    guild = bot.get_guild(int(GUILD_ID))
    if guild is None:
        print("Guild not found.")
        return
    try:
        member = await guild.fetch_member(user_id)
    except discord.NotFound:
        print("User not found in guild yet.")
        return
    except Exception as e:
        print(f"Error fetching member: {e}")
        return
    role = guild.get_role(int(ROLE_ID))
    if role:
        await member.add_roles(role, reason="Verified via website")
        print(f"Verified and added role to {member.display_name}")
    else:
        print("Role not found.")

@app.route('/')
def home():
    return render_template('home.html')  # just one route for '/'

if __name__ == '__main__':
    import threading

    def run_bot():
        bot.run(DISCORD_BOT_TOKEN)  # Use the loaded token variable

    threading.Thread(target=run_bot).start()
    app.run(debug=False, use_reloader=False)