from flask import Flask, session, redirect, url_for, request, render_template, jsonify
import requests
import os
from flask_session import Session
import discord
from discord.ext import commands
import threading
from dotenv import load_dotenv
import openai
import urllib.parse
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

load_dotenv()

app = Flask(__name__)

# Secret key for sessions
app.secret_key = os.getenv("SECRET_KEY") or os.urandom(24)

# Configure server-side sessions
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

# === Database config ===
db_url = os.getenv("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# === Define Models ===
class Execution(db.Model):
    __tablename__ = 'executions'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64))
    count = db.Column(db.Integer)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    source = db.Column(db.String(64))
    developer = db.Column(db.String(64))

# Discord OAuth2 and Bot Config from environment or hardcode for testing
CLIENT_ID = os.getenv("CLIENT_ID") or 'YOUR_CLIENT_ID'
CLIENT_SECRET = os.getenv("CLIENT_SECRET") or 'YOUR_CLIENT_SECRET'
REDIRECT_URI = os.getenv("REDIRECT_URI") or 'YOUR_REDIRECT_URL'
GUILD_ID = os.getenv("GUILD_ID") or 'YOUR_GUILD_ID'
ROLE_ID = os.getenv("ROLE_ID") or 'YOUR_ROLE_ID'
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN") or 'YOUR_BOT_TOKEN'
openai.api_key = os.getenv("OPENAI_API_KEY")

DISCORD_API_BASE = "https://discord.com/api"
DISCORD_OAUTH_SCOPES = "identify guilds.join"

# Dummy bot status and stats (replace or expand as needed)
BOT_ONLINE = True
STATS = {
    "verified_users": 1234,
    "total_verifications": 4321,
    "servers_using_bot": 25,
    "bot_uptime_percent": 99.9
}

# Discord bot setup
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot is ready. Logged in as {bot.user}")

@app.route('/')
@app.route('/index')
@app.route('/home')
def index():
    user = session.get('user')  # None if not logged in
    return render_template('index.html', user=user)

# Login route - redirect to Discord OAuth2 authorize URL
@app.route('/login')
def login():
    encoded_redirect_uri = urllib.parse.quote_plus(REDIRECT_URI)
    encoded_scopes = urllib.parse.quote_plus(DISCORD_OAUTH_SCOPES)
    discord_auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={encoded_redirect_uri}"
        f"&response_type=code"
        f"&scope={encoded_scopes}"
    )
    print("Redirecting to Discord OAuth2 URL:", discord_auth_url)  # Debug print
    return redirect(discord_auth_url)

# OAuth2 callback route
@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return "No code provided", 400

    # Exchange code for access token
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "scope": DISCORD_OAUTH_SCOPES,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    token_res = requests.post(f"{DISCORD_API_BASE}/oauth2/token", data=data, headers=headers)
    if token_res.status_code != 200:
        try:
            error_json = token_res.json()
        except Exception:
            error_json = token_res.text
        return f"Failed to get token: {error_json}", 400

    token_json = token_res.json()
    access_token = token_json.get('access_token')
    if not access_token:
        return "Failed to get access token", 400

    # Fetch user info from Discord
    user_res = requests.get(f"{DISCORD_API_BASE}/users/@me", headers={
        "Authorization": f"Bearer {access_token}"
    })
    if user_res.status_code != 200:
        return "Failed to fetch user info", 400
    user_json = user_res.json()

    # Save user info in session
    session['user'] = user_json

    user_id = int(user_json["id"])

    # Add user to guild
    add_headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    add_payload = {
        "access_token": access_token
    }
    add_resp = requests.put(
        f"{DISCORD_API_BASE}/guilds/{GUILD_ID}/members/{user_id}",
        headers=add_headers,
        json=add_payload
    )
    if add_resp.status_code in (201, 204):
        print("User added to the guild successfully.")
    else:
        print(f"Failed to add user to guild: {add_resp.status_code} - {add_resp.text}")

    # Schedule role addition asynchronously in bot loop
    bot.loop.create_task(add_role_to_user(user_id))

    # Example: log verification into executions table
    new_exec = Execution(
        username=user_json["username"],
        count=1,
        source="verification",
        developer="system"
    )
    db.session.add(new_exec)
    db.session.commit()

    return render_template("success.html", username=user_json["username"])

# Logout route
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# Bot status API endpoint for frontend
@app.route('/bot_status')
def bot_status():
    return jsonify({"online": BOT_ONLINE})

# Stats API endpoint for frontend
@app.route('/stats')
def stats():
    return jsonify(STATS)

# Coroutine to add role to user
async def add_role_to_user(user_id):
    guild = bot.get_guild(int(GUILD_ID))
    if not guild:
        print("Guild not found.")
        return
    try:
        member = await guild.fetch_member(user_id)
    except discord.NotFound:
        print("User not found in guild.")
        return
    except Exception as e:
        print(f"Error fetching member: {e}")
        return

    role = guild.get_role(int(ROLE_ID))
    if role:
        await member.add_roles(role, reason="Verified via website")
        print(f"Role added to {member.display_name}")
    else:
        print("Role not found.")

# AI-powered support endpoint
@app.route('/support', methods=['POST'])
def support():
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({"error": "No question provided"}), 400

    question = data['question']

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful support assistant for Polluted Hub."},
                {"role": "user", "content": question}
            ],
            max_tokens=150,
            temperature=0.7,
        )
        answer = response['choices'][0]['message']['content'].strip()
        return jsonify({"answer": answer})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Extra: manual execution logger endpoint
@app.route('/log_execution', methods=['POST'])
def log_execution():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing payload"}), 400

    new_exec = Execution(
        username=data.get("username"),
        count=data.get("count", 1),
        source=data.get("source", "api"),
        developer=data.get("developer", "unknown")
    )
    db.session.add(new_exec)
    db.session.commit()
    return jsonify({"status": "success", "id": new_exec.id})

# Run Discord bot and Flask app concurrently
if __name__ == "__main__":
    def run_bot():
        bot.run(DISCORD_BOT_TOKEN)

    port = int(os.environ.get("PORT", 5000))

    # Create tables if they don't exist yet
    with app.app_context():
        db.create_all()

    threading.Thread(target=run_bot).start()
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)