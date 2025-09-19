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

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Secret key for sessions
app.secret_key = os.getenv("SECRET_KEY") or os.urandom(24)

# Configure server-side sessions
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

# PostgreSQL connection (Render example: postgresql://USER:PASSWORD@HOST:PORT/DBNAME)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Define Executions model
class Execution(db.Model):
    __tablename__ = "executions"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64))
    count = db.Column(db.Integer)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    source = db.Column(db.String(64))
    developer = db.Column(db.String(64))

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "count": self.count,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "source": self.source,
            "developer": self.developer,
        }

# Initialize database
with app.app_context():
    db.create_all()

# Discord OAuth2 and Bot Config
CLIENT_ID = os.getenv("CLIENT_ID") or 'YOUR_CLIENT_ID'
CLIENT_SECRET = os.getenv("CLIENT_SECRET") or 'YOUR_CLIENT_SECRET'
REDIRECT_URI = os.getenv("REDIRECT_URI") or 'YOUR_REDIRECT_URL'
GUILD_ID = os.getenv("GUILD_ID") or 'YOUR_GUILD_ID'
ROLE_ID = os.getenv("ROLE_ID") or 'YOUR_ROLE_ID'
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN") or 'YOUR_BOT_TOKEN'
openai.api_key = os.getenv("OPENAI_API_KEY")

DISCORD_API_BASE = "https://discord.com/api"
DISCORD_OAUTH_SCOPES = "identify guilds.join"

# Dummy bot status and stats
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
    user = session.get('user')
    return render_template('index.html', user=user)

@app.route('/login')
def login():
    encoded_redirect_uri = urllib.parse.quote_plus(REDIRECT_URI)
    encoded_scopes = urllib.parse.quote_plus(DISCORD_OAUTH_SCOPES)
    discord_auth_url = (
        f"{DISCORD_API_BASE}/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={encoded_redirect_uri}"
        f"&response_type=code"
        f"&scope={encoded_scopes}"
    )
    return redirect(discord_auth_url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return "No code provided", 400

    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "scope": DISCORD_OAUTH_SCOPES,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    token_res = requests.post(f"{DISCORD_API_BASE}/oauth2/token", data=data, headers=headers)

    if token_res.status_code != 200:
        return f"Failed to get token: {token_res.text}", 400

    token_json = token_res.json()
    access_token = token_json.get('access_token')
    if not access_token:
        return "Failed to get access token", 400

    user_res = requests.get(f"{DISCORD_API_BASE}/users/@me", headers={
        "Authorization": f"Bearer {access_token}"
    })
    if user_res.status_code != 200:
        return "Failed to fetch user info", 400
    user_json = user_res.json()

    session['user'] = user_json
    user_id = int(user_json["id"])

    add_headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    add_payload = {"access_token": access_token}
    add_resp = requests.put(
        f"{DISCORD_API_BASE}/guilds/{GUILD_ID}/members/{user_id}",
        headers=add_headers,
        json=add_payload
    )
    if add_resp.status_code in (201, 204):
        print("User added to the guild successfully.")
    else:
        print(f"Failed to add user: {add_resp.status_code} - {add_resp.text}")

    bot.loop.create_task(add_role_to_user(user_id))
    return render_template("success.html", username=user_json["username"])

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/bot_status')
def bot_status():
    return jsonify({"online": BOT_ONLINE})

@app.route('/stats')
def stats():
    return jsonify(STATS)

# Store executions from Roblox scripts
@app.route('/executions', methods=['POST'])
def add_execution():
    data = request.get_json()
    if not data or "username" not in data or "count" not in data:
        return jsonify({"error": "Missing username or count"}), 400

    new_execution = Execution(
        username=data["username"],
        count=data["count"],
        source=data.get("source", "unknown"),
        developer=data.get("developer", "unknown")
    )
    db.session.add(new_execution)
    db.session.commit()
    return jsonify({"message": "Execution logged", "execution": new_execution.to_dict()}), 201

# Fetch all executions
@app.route('/executions', methods=['GET'])
def get_executions():
    executions = Execution.query.order_by(Execution.timestamp.desc()).all()
    return jsonify([e.to_dict() for e in executions])

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

    role = guild.get_role(int(ROLE_ID))
    if role:
        await member.add_roles(role, reason="Verified via website")
        print(f"Role added to {member.display_name}")
    else:
        print("Role not found.")

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

if __name__ == "__main__":
    def run_bot():
        bot.run(DISCORD_BOT_TOKEN)

    port = int(os.environ.get("PORT", 5000))
    threading.Thread(target=run_bot).start()
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)