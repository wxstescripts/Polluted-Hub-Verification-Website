import os
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, abort
from datetime import datetime, timedelta
from functools import wraps
from sqlalchemy import create_engine, text
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.engine.url import make_url
import psycopg2
import psycopg2.extras
from config import ADMIN_PASSWORD
import sqlite3

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Database configuration
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///local.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class Execution(db.Model):
    __tablename__ = 'executions'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64))
    count = db.Column(db.Integer)
    timestamp = db.Column(db.DateTime)
    source = db.Column(db.String(64))
    developer = db.Column(db.String(64))

with app.app_context():
    db.create_all()

settings = {
    "weekly_reset_day": "Sunday",
    "max_executions": 1000,
    "dark_mode": False,
    "auto_cleanup_enabled": True,
    "reset_mode": "monthly"
}

def get_db_connection():
    if DATABASE_URL.startswith("sqlite"):
        engine = db.get_engine()
        return engine.raw_connection()
    url = make_url(DATABASE_URL)
    conn = psycopg2.connect(
        dbname=url.database,
        user=url.username,
        password=url.password,
        host=url.host,
        port=url.port
    )
    return conn

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def delete_old_records():
    if not settings.get("auto_cleanup_enabled", True):
        return
    days = 7 if settings.get("reset_mode") == "weekly" else 30
    cutoff = datetime.utcnow() - timedelta(days=days)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM executions WHERE timestamp < %s', (cutoff,))
    conn.commit()
    cur.close()
    conn.close()

def get_oldest_entry_date():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT MIN(timestamp) FROM executions")
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def get_dict_cursor(conn):
    # Handle SQLite
    if isinstance(conn, sqlite3.Connection):
        conn.row_factory = sqlite3.Row
        return conn.cursor()
    
    # Handle PostgreSQL raw connection
    try:
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    except TypeError:
        # If conn is a SQLAlchemy connection, get raw connection
        raw_conn = conn.connection
        return raw_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/leaderboard')
def leaderboard():
    one_month_ago = datetime.utcnow() - timedelta(days=30)
    engine = db.get_engine()
    with engine.connect() as conn:
        result = conn.execute(text('''
            SELECT username, SUM(count) as total, source, developer
            FROM executions
            WHERE timestamp >= :date
            GROUP BY username, source, developer
            ORDER BY total DESC
            LIMIT 20
        '''), {'date': one_month_ago})
        leaderboard_data = [dict(row) for row in result]
    return render_template('leaderboard.html', leaderboard=leaderboard_data)

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        error = "Invalid password"
    return render_template('admin_login.html', error=error)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('home'))

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    return render_template('admin_panel.html',
                           dark_mode=settings["dark_mode"],
                           active_tab='admin_panel',
                           oldest_date=get_oldest_entry_date())

@app.route('/admin/reset_monthly')
@admin_required
def reset_monthly():
    delete_old_records()
    return jsonify({"status": "Old records deleted"}), 200

@app.route('/admin/reset_leaderboard', methods=['POST'])
@admin_required
def reset_leaderboard():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM executions')
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": "Leaderboard reset successful."}), 200

@app.route('/admin/reset_user', methods=['POST'])
@admin_required
def reset_user():
    username = request.form.get('username')
    if not username:
        return jsonify({"error": "Username required"}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM executions WHERE username = %s', (username,))
    conn.commit()
    deleted = cur.rowcount
    cur.close()
    conn.close()
    return jsonify({"status": f"{deleted} records deleted for {username}"}), 200

@app.route('/api/track', methods=['POST'])
def api_track():
    data = request.get_json()
    if not data or 'username' not in data or 'count' not in data:
        return jsonify({'error': 'Invalid data'}), 400

    username = data['username']
    try:
        count = int(data['count'])
    except ValueError:
        return jsonify({'error': 'Count must be an integer'}), 400

    source = data.get('source', 'Unknown')
    developer = data.get('developer', 'Unknown')
    timestamp = datetime.utcnow()

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO executions (username, count, timestamp, source, developer)
        VALUES (%s, %s, %s, %s, %s)
    ''', (username, count, timestamp, source, developer))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'status': 'success'}), 200

@app.route('/public/leaderboard')
def public_leaderboard_page():
    one_month_ago = datetime.utcnow() - timedelta(days=30)
    conn = get_db_connection()
    cur = get_dict_cursor(conn)
    cur.execute('''
        SELECT username, SUM(count) as total, source, developer
        FROM executions
        WHERE timestamp >= %s
        GROUP BY username, source, developer
        ORDER BY total DESC
        LIMIT 20
    ''', (one_month_ago,))
    data = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('public_leaderboard.html', leaderboard=data)

@app.route('/api/save-admin-settings', methods=['POST'])
@admin_required
def save_admin_settings():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing JSON data"}), 400

    for key in ['cleanup_enabled', 'public_view_enabled', 'auto_refresh_enabled']:
        if key in data and isinstance(data[key], bool):
            settings[key] = data[key]
        else:
            return jsonify({"error": f"Invalid format for {key}"}), 400

    return jsonify({"status": "Settings updated successfully"}), 200

@app.route('/admin/settings')
@admin_required
def settings_page():
    return render_template('admin_settings.html', settings=settings, active_tab='settings', admins=["admin1"])

@app.route('/api/export/<filetype>')
@admin_required
def api_export(filetype):
    if filetype == 'csv':
        csv_data = "Name,Score\nAlice,100\nBob,90"
        return app.response_class(
            csv_data,
            mimetype='text/csv',
            headers={"Content-disposition": "attachment; filename=export.csv"}
        )
    return "Unsupported export format", 400

@app.route('/admin/leaderboard')
@admin_required
def admin_leaderboard():
    one_month_ago = datetime.utcnow() - timedelta(days=30)
    conn = get_db_connection()
    cur = conn.cursor()

    query = '''
        SELECT username, SUM(count) as total, source, developer
        FROM executions
        WHERE timestamp >= %s
        GROUP BY username, source, developer
        ORDER BY total DESC
        LIMIT 20
    '''

    cur.execute(query, (one_month_ago,))

    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    leaderboard_data = [dict(zip(columns, row)) for row in rows]

    cur.close()
    conn.close()

    return render_template('admin_leaderboard.html', leaderboard=leaderboard_data, active_tab='admin_leaderboard')

@app.errorhandler(403)
def forbidden_error(error):
    return render_template('403.html'), 403

@app.route('/api/weekly-leaderboard')
def weekly_leaderboard():
    one_week_ago = datetime.utcnow() - timedelta(days=7)
    conn = get_db_connection()
    cur = get_dict_cursor(conn)
    cur.execute('''
        SELECT username, SUM(count) as executions, source, developer
        FROM executions
        WHERE timestamp >= %s
        GROUP BY username, source, developer
        ORDER BY executions DESC
        LIMIT 25
    ''', (one_week_ago,))
    data = cur.fetchall()
    cur.close()
    conn.close()

    # Add rank
    for i, row in enumerate(data, 1):
        row['rank'] = i

    return jsonify(data)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
