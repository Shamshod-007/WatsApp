from __future__ import annotations

import datetime
import mimetypes
import os
import re
import secrets
import sqlite3
import uuid
from pathlib import Path

try:
    from waitress import serve as waitress_serve  # type: ignore[import-not-found]
except ModuleNotFoundError:
    waitress_serve = None

try:
    from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for  # type: ignore[import-not-found]
    from flask_socketio import SocketIO, emit, join_room  # type: ignore[import-not-found]
    from werkzeug.security import check_password_hash, generate_password_hash  # type: ignore[import-not-found]
    from werkzeug.utils import secure_filename  # type: ignore[import-not-found]
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Flask dependencies are missing. Install them with: python -m pip install flask flask-socketio"
    ) from exc

def load_secret_key() -> str:
    """Ключ сессий: из SECRET_KEY или из файла .secret_key (создаётся один раз, случайный)."""
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    key_file = Path(__file__).resolve().parent / ".secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    try:
        key_file.write_text(key)
    except OSError:
        pass
    return key


app = Flask(__name__)
app.secret_key = load_secret_key()
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"  # 1 = только по HTTPS
app.config["PERMANENT_SESSION_LIFETIME"] = datetime.timedelta(days=60)  # приложение на телефоне не просит входить заново
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

socketio = SocketIO(app, cors_allowed_origins=os.environ.get("CORS_ORIGINS") or "*")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "messenger.db"
UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"
PORT = int(os.environ.get("PORT", "5001"))
INVITE_CODE = os.environ.get("INVITE_CODE", "").strip()  # если задан, регистрация только по коду
ONLINE_USERS: set[int] = set()
ONLINE_COUNTS: dict[int, int] = {}
ALLOWED_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".mov", ".mp3", ".ogg", ".wav", ".m4a",
    ".pdf", ".txt", ".zip", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
}
HASH_PREFIXES = ("pbkdf2:", "scrypt:")


def hash_pw(value: str) -> str:
    return generate_password_hash(value, method="pbkdf2:sha256")


def verify_password(stored, given: str) -> bool:
    stored = str(stored or "")
    if stored.startswith(HASH_PREFIXES):
        return check_password_hash(stored, given)
    return secrets.compare_digest(stored, given)


def is_member(conversation_id: int, user_id: int) -> bool:
    return any(int(m["id"]) == int(user_id) for m in get_conversation_members(conversation_id))


def notify_peers(user_id: int):
    for item in get_contact_list(user_id):
        peer_id = item.get("peer_id")
        if peer_id:
            socketio.emit("contacts_updated", {"contacts": get_contact_list(int(peer_id))}, room=f"user:{peer_id}")


def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_uploads_dir():
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)


def ensure_schema():
    ensure_uploads_dir()
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                display_name TEXT,
                phone_number TEXT,
                status TEXT DEFAULT 'online',
                avatar_color TEXT DEFAULT '#25d366',
                bio TEXT DEFAULT 'Available to chat'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user1_id INTEGER,
                user2_id INTEGER,
                type TEXT DEFAULT 'direct',
                title TEXT,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user1_id, user2_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_members (
                conversation_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (conversation_id, user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_type TEXT DEFAULT 'text',
                attachment_type TEXT,
                attachment_name TEXT,
                attachment_url TEXT,
                edited_at TIMESTAMP,
                deleted_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                conversation_id INTEGER NOT NULL,
                unread_count INTEGER DEFAULT 0,
                last_message TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, conversation_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                contact_user_id INTEGER NOT NULL,
                nickname TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, contact_user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS call_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                peer_id INTEGER NOT NULL,
                call_type TEXT NOT NULL,
                status TEXT DEFAULT 'completed',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        for column_name, definition in {
            "phone_number": "TEXT",
            "status": "TEXT DEFAULT 'online'",
            "avatar_color": "TEXT DEFAULT '#25d366'",
            "bio": "TEXT DEFAULT 'Available to chat'",
        }.items():
            if column_name not in user_columns:
                conn.execute(f"ALTER TABLE users ADD COLUMN {column_name} {definition}")

        conversation_columns = {row["name"] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()}
        for column_name, definition in {
            "type": "TEXT DEFAULT 'direct'",
            "title": "TEXT",
            "created_by": "INTEGER",
        }.items():
            if column_name not in conversation_columns:
                conn.execute(f"ALTER TABLE conversations ADD COLUMN {column_name} {definition}")

        message_columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
        for column_name, definition in {
            "message_type": "TEXT DEFAULT 'text'",
            "attachment_type": "TEXT",
            "attachment_name": "TEXT",
            "attachment_url": "TEXT",
            "edited_at": "TIMESTAMP",
            "deleted_at": "TIMESTAMP",
        }.items():
            if column_name not in message_columns:
                conn.execute(f"ALTER TABLE messages ADD COLUMN {column_name} {definition}")

        has_users = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
        seed_demo = os.environ.get("SEED_DEMO", "0" if has_users else "1") == "1"
        demo_users = [
            ("shamshod", "123456789", "Shamshod", "+15550000001", "online", "#25d366", "Designing better conversations"),
            ("maria", "123456789", "Maria", "+15550000002", "online", "#f59e0b", "Planning the weekly launch"),
            ("dmitriy", "123456789", "Dmitriy", "+15550000003", "offline", "#8b5cf6", "Working on product updates"),
            ("nora", "123456789", "Nora", "+15550000004", "online", "#06b6d4", "Always online for quick ideas"),
        ]
        for username, password, display_name, phone_number, status, avatar_color, bio in (demo_users if seed_demo else []):
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                continue
            conn.execute(
                "INSERT OR IGNORE INTO users(username, password, display_name, phone_number, status, avatar_color, bio) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (username, hash_pw(password), display_name, phone_number, status, avatar_color, bio),
            )
        conn.commit()


def initials(value: str | None) -> str:
    if not value:
        return "U"
    parts = [part for part in str(value).split() if part]
    if not parts:
        return "U"
    return "".join(part[0] for part in parts[:2]).upper()


def avatar_color(seed: str | int | None) -> str:
    palette = [
        "#25d366",
        "#0ea5e9",
        "#8b5cf6",
        "#f59e0b",
        "#ef4444",
        "#10b981",
        "#ec4899",
        "#6366f1",
        "#f97316",
        "#06b6d4",
    ]
    if seed is None:
        return palette[0]
    key = sum(ord(char) for char in str(seed))
    return palette[key % len(palette)]


def normalize_phone(value: str | None) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


def user_by_id(user_id: int):
    with get_db() as conn:
        user = conn.execute(
            "SELECT id, username, display_name, phone_number, status, avatar_color, bio FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if user is None:
        return None
    return dict(user)


def get_saved_contacts(current_user_id: int):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.phone_number, u.status, u.avatar_color, u.bio
            FROM user_contacts uc
            JOIN users u ON u.id = uc.contact_user_id
            WHERE uc.user_id = ?
            ORDER BY u.display_name, u.username
            """,
            (current_user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def search_users_for_contacts(current_user_id: int, query: str):
    term = (query or "").strip()
    phone_query = normalize_phone(term)
    if not term:
        return []

    with get_db() as conn:
        if phone_query:
            rows = conn.execute(
                """
                SELECT u.id, u.username, u.display_name, u.phone_number, u.status, u.avatar_color, u.bio
                FROM users u
                WHERE u.id != ?
                  AND (
                    u.username = ?
                    OR u.phone_number = ?
                    OR u.phone_number LIKE ?
                    OR LOWER(u.display_name) LIKE LOWER(?)
                  )
                ORDER BY u.display_name, u.username
                LIMIT 20
                """,
                (current_user_id, term, phone_query, f"%{phone_query}%", f"%{term}%"),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT u.id, u.username, u.display_name, u.phone_number, u.status, u.avatar_color, u.bio
                FROM users u
                WHERE u.id != ?
                  AND (
                    LOWER(u.username) LIKE LOWER(?)
                    OR LOWER(u.display_name) LIKE LOWER(?)
                    OR u.phone_number LIKE ?
                  )
                ORDER BY u.display_name, u.username
                LIMIT 20
                """,
                (current_user_id, f"%{term}%", f"%{term}%", f"%{term}%"),
            ).fetchall()
    return [dict(row) for row in rows]


def get_call_history(current_user_id: int):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT ch.id, ch.user_id, ch.peer_id, ch.call_type, ch.status, ch.created_at,
                   u.display_name AS peer_name, u.username AS peer_username, u.phone_number AS peer_phone
            FROM call_history ch
            JOIN users u ON u.id = ch.peer_id
            WHERE ch.user_id = ?
            ORDER BY ch.created_at DESC
            LIMIT 50
            """,
            (current_user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_conversation_members(conversation_id: int):
    with get_db() as conn:
        members = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.status, u.avatar_color, u.bio
            FROM conversation_members cm
            JOIN users u ON u.id = cm.user_id
            WHERE cm.conversation_id = ?
            ORDER BY u.display_name
            """,
            (conversation_id,),
        ).fetchall()
        if members:
            return [dict(member) for member in members]

        row = conn.execute(
            "SELECT user1_id, user2_id FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if not row:
            return []

        member_ids = [row["user1_id"], row["user2_id"]]
        result = []
        for member_id in sorted(set(member_id for member_id in member_ids if member_id)):
            member = conn.execute(
                "SELECT id, username, display_name, status, avatar_color, bio FROM users WHERE id = ?",
                (member_id,),
            ).fetchone()
            if member:
                result.append(dict(member))
        return result


def ensure_conversation_members(conversation_id: int, user_ids):
    with get_db() as conn:
        for user_id in sorted(set(int(user_id) for user_id in user_ids if user_id)):
            conn.execute(
                "INSERT OR IGNORE INTO conversation_members(conversation_id, user_id) VALUES (?, ?)",
                (conversation_id, user_id),
            )
        conn.commit()


def get_or_create_direct_conversation(user1_id: int, user2_id: int):
    if user1_id == user2_id:
        raise ValueError("You cannot start a chat with yourself")
    a, b = sorted((int(user1_id), int(user2_id)))

    with get_db() as conn:
        existing = conn.execute(
            """
            SELECT id FROM conversations
            WHERE ((user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?))
              AND (type IS NULL OR type = 'direct')
            LIMIT 1
            """,
            (a, b, b, a),
        ).fetchone()
        if existing:
            return int(existing["id"])

        cursor = conn.execute(
            """
            INSERT INTO conversations(user1_id, user2_id, type, title, created_by, updated_at)
            VALUES (?, ?, 'direct', ?, ?, CURRENT_TIMESTAMP)
            """,
            (a, b, f"Direct chat {a}:{b}", a),
        )
        conn.commit()
        conversation_id = int(cursor.lastrowid)
        ensure_conversation_members(conversation_id, [a, b])
        return conversation_id


def message_preview(message_row):
    if message_row is None:
        return "Start a conversation", None, None
    if message_row["deleted_at"]:
        return "Message deleted", message_row["created_at"], message_row["sender_id"]
    raw_text = (message_row["text"] or "").strip()
    if message_row["message_type"] == "media" and message_row["attachment_name"]:
        return f"📎 {message_row['attachment_name']}", message_row["created_at"], message_row["sender_id"]
    if raw_text:
        return raw_text, message_row["created_at"], message_row["sender_id"]
    return "📎 Media message", message_row["created_at"], message_row["sender_id"]


def get_contact_list(current_user_id: int):
    with get_db() as conn:
        notification_counts = {
            int(row["conversation_id"]): int(row["unread_count"])
            for row in conn.execute(
                "SELECT conversation_id, unread_count FROM notifications WHERE user_id = ?",
                (current_user_id,),
            ).fetchall()
        }

        conversation_rows = []
        direct_rows = conn.execute(
            """
            SELECT c.id, c.type, c.title, c.created_by, c.updated_at, c.user1_id, c.user2_id
            FROM conversations c
            WHERE (c.user1_id = ? OR c.user2_id = ?) AND (c.type IS NULL OR c.type = 'direct')
            """,
            (current_user_id, current_user_id),
        ).fetchall()
        conversation_rows.extend(direct_rows)

        group_rows = conn.execute(
            """
            SELECT c.id, c.type, c.title, c.created_by, c.updated_at, c.user1_id, c.user2_id
            FROM conversations c
            JOIN conversation_members cm ON cm.conversation_id = c.id
            WHERE cm.user_id = ? AND c.type = 'group'
            """,
            (current_user_id,),
        ).fetchall()
        conversation_rows.extend(group_rows)

        seen = set()
        contacts = []
        for row in conversation_rows:
            conversation_id = int(row["id"])
            if conversation_id in seen:
                continue
            seen.add(conversation_id)

            members = get_conversation_members(conversation_id)
            chosen_color = None
            if row["type"] == "group":
                title = row["title"] or "Group chat"
                display_name = title
                username = "group"
                status = "group"
                peer_id = None
                unread_count = notification_counts.get(conversation_id, 0)
            else:
                peer = next((member for member in members if int(member["id"]) != current_user_id), None)
                peer_id = int(peer["id"]) if peer else None
                display_name = (peer["display_name"] if peer else row["title"]) or "Unknown"
                username = peer["username"] if peer else "unknown"
                status = "online" if peer_id and peer_id in ONLINE_USERS else "offline"
                unread_count = notification_counts.get(conversation_id, 0)
                title = display_name
                chosen_color = peer.get("avatar_color") if peer else None

            last_message = conn.execute(
                """
                SELECT m.text, m.message_type, m.attachment_name, m.attachment_url, m.created_at, m.sender_id, m.deleted_at
                FROM messages m
                WHERE m.conversation_id = ? AND m.deleted_at IS NULL
                ORDER BY m.created_at DESC, m.id DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
            preview_text, last_message_time, sender_id = message_preview(last_message)

            contacts.append(
                {
                    "conversation_id": conversation_id,
                    "id": peer_id if peer_id is not None else conversation_id,
                    "type": row["type"] or "direct",
                    "title": title,
                    "display_name": display_name,
                    "username": username,
                    "status": status,
                    "peer_id": peer_id,
                    "avatar_text": initials(display_name),
                    "avatar_color": chosen_color or avatar_color(display_name),
                    "last_message": preview_text,
                    "last_message_time": last_message_time,
                    "unread_count": unread_count,
                    "member_count": len(members),
                    "last_sender_id": sender_id,
                }
            )

    contacts.sort(key=lambda item: (item["last_message_time"] or "0000-01-01T00:00:00", item["display_name"]))
    contacts.reverse()
    return contacts


def get_chat_payload(conversation_id: int, current_user_id: int):
    with get_db() as conn:
        conversation = conn.execute(
            "SELECT id, type, title, created_by, user1_id, user2_id FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if conversation is None:
            return None

        members = get_conversation_members(conversation_id)
        if conversation["type"] == "group":
            title = conversation["title"] or "Group chat"
            details = {
                "type": "group",
                "title": title,
                "display_name": title,
                "status": "group",
                "members": members,
                "member_count": len(members),
            }
        else:
            peer = next((member for member in members if int(member["id"]) != current_user_id), None)
            details = {
                "type": "direct",
                "title": (peer["display_name"] if peer else conversation["title"]) or "Unknown",
                "display_name": (peer["display_name"] if peer else conversation["title"]) or "Unknown",
                "status": "online" if peer and int(peer["id"]) in ONLINE_USERS else "offline",
                "members": members,
                "peer": peer,
            }

        rows = conn.execute(
            """
            SELECT m.id, m.conversation_id, m.sender_id, m.text, m.created_at,
                   m.message_type, m.attachment_type, m.attachment_name, m.attachment_url,
                   m.edited_at, m.deleted_at,
                   u.display_name AS sender_name, u.avatar_color AS sender_color, u.username AS sender_username
            FROM messages m
            JOIN users u ON u.id = m.sender_id
            WHERE m.conversation_id = ? AND m.deleted_at IS NULL
            ORDER BY m.created_at ASC, m.id ASC
            """,
            (conversation_id,),
        ).fetchall()

        conn.execute(
            "UPDATE notifications SET unread_count = 0, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND conversation_id = ?",
            (current_user_id, conversation_id),
        )
        conn.commit()

    return {"conversation": details, "messages": [dict(row) for row in rows]}


def persist_notification(user_id: int, conversation_id: int, last_message: str):
    with get_db() as conn:
        existing = conn.execute(
            "SELECT unread_count FROM notifications WHERE user_id = ? AND conversation_id = ?",
            (user_id, conversation_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE notifications SET unread_count = unread_count + 1, last_message = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND conversation_id = ?",
                (last_message, user_id, conversation_id),
            )
        else:
            conn.execute(
                "INSERT INTO notifications(user_id, conversation_id, unread_count, last_message, updated_at) VALUES (?, ?, 1, ?, CURRENT_TIMESTAMP)",
                (user_id, conversation_id, last_message),
            )
        conn.commit()


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        phone_value = normalize_phone(request.form.get("phone_number") or request.form.get("phone"))
        login_value = normalize_phone(username) if username else phone_value
        password = (request.form.get("password") or "").strip()
        if username and password:
            with get_db() as conn:
                user = conn.execute(
                    "SELECT id, username, display_name, password FROM users WHERE username = ? OR (phone_number != '' AND phone_number IN (?, ?))",
                    (username, login_value, phone_value),
                ).fetchone()
                if user and verify_password(user["password"], password):
                    if not str(user["password"]).startswith(HASH_PREFIXES):
                        conn.execute("UPDATE users SET password = ? WHERE id = ?", (hash_pw(password), user["id"]))
                    session.permanent = True
                    session["user_id"] = int(user["id"])
                    session["username"] = user["username"]
                    session["display_name"] = user["display_name"] or user["username"]
                    return redirect(url_for("messenger"))
        error = "Неверный логин, телефон или пароль"
    return render_template("login.html", error=error, registration_mode=False, invite_required=bool(INVITE_CODE), form=request.form)


@app.route("/register", methods=["GET", "POST"])
def register():
    error = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        phone_number = normalize_phone(request.form.get("phone_number") or request.form.get("phone"))
        password = (request.form.get("password") or "").strip()
        display_name = (request.form.get("display_name") or "").strip() or username

        if INVITE_CODE and (request.form.get("invite") or "").strip() != INVITE_CODE:
            error = "Неверный код приглашения"
        elif not username or not password or not phone_number:
            error = "Укажите логин, телефон и пароль"
        elif len(username) < 3 or " " in username:
            error = "Логин: минимум 3 символа, без пробелов"
        elif len(password) < 6:
            error = "Пароль: минимум 6 символов"
        else:
            with get_db() as conn:
                existing = conn.execute(
                    "SELECT id FROM users WHERE username = ? OR phone_number = ?", (username, phone_number)
                ).fetchone()
                if existing:
                    error = "Такой логин или телефон уже зарегистрирован"
                else:
                    cursor = conn.execute(
                        "INSERT INTO users(username, password, display_name, phone_number, status, avatar_color, bio) VALUES (?, ?, ?, ?, 'online', ?, 'Available to chat')",
                        (username, hash_pw(password), display_name, phone_number, avatar_color(display_name)),
                    )
                    session.permanent = True
                    session["user_id"] = int(cursor.lastrowid)
                    session["username"] = username
                    session["display_name"] = display_name
                    return redirect(url_for("messenger"))
    return render_template("login.html", error=error, registration_mode=True, invite_required=bool(INVITE_CODE), form=request.form)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return redirect(url_for("messenger"))


@app.route("/messenger")
def messenger():
    if "user_id" not in session:
        return redirect(url_for("login"))

    current_user = user_by_id(int(session["user_id"]))
    if current_user is None:
        session.clear()
        return redirect(url_for("login"))
    contacts = get_contact_list(int(session["user_id"]))
    requested_chat_id = request.args.get("chat_id", type=int)
    if requested_chat_id:
        active_contact = next((contact for contact in contacts if int(contact["conversation_id"] if "conversation_id" in contact else contact["id"]) == requested_chat_id), None)
    else:
        active_contact = contacts[0] if contacts else None
    return render_template(
        "messenger.html",
        user_id=int(current_user["id"]),
        username=current_user["username"],
        display_name=current_user["display_name"],
        bio=current_user["bio"],
        avatar_color=current_user["avatar_color"],
        contacts=contacts,
        active_contact=active_contact,
        default_chat_id=requested_chat_id,
    )


def profile_json(user):
    return {
        "id": int(user["id"]),
        "username": user["username"],
        "display_name": user["display_name"],
        "phone_number": user["phone_number"],
        "status": user["status"],
        "bio": user["bio"],
        "avatar_color": user["avatar_color"],
        "avatar_text": initials(user["display_name"] or user["username"]),
    }


@app.route("/api/profile", methods=["GET", "POST"])
def api_profile():
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    user_id = int(session["user_id"])
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        user = user_by_id(user_id)
        display_name = (data.get("display_name") or "").strip()[:40] or user["display_name"]
        bio = (data.get("bio") or "").strip()[:140]
        color = (data.get("avatar_color") or user["avatar_color"] or "").strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            color = user["avatar_color"]
        phone = normalize_phone(data.get("phone_number")) or user["phone_number"]
        with get_db() as conn:
            taken = conn.execute("SELECT id FROM users WHERE phone_number = ? AND id != ?", (phone, user_id)).fetchone()
            if taken:
                return jsonify({"error": "Этот телефон уже занят"}), 400
            new_password = (data.get("new_password") or "").strip()
            if new_password:
                row = conn.execute("SELECT password FROM users WHERE id = ?", (user_id,)).fetchone()
                if not verify_password(row["password"], (data.get("current_password") or "").strip()):
                    return jsonify({"error": "Текущий пароль неверный"}), 400
                if len(new_password) < 6:
                    return jsonify({"error": "Новый пароль: минимум 6 символов"}), 400
                conn.execute("UPDATE users SET password = ? WHERE id = ?", (hash_pw(new_password), user_id))
            conn.execute(
                "UPDATE users SET display_name = ?, bio = ?, avatar_color = ?, phone_number = ? WHERE id = ?",
                (display_name, bio, color, phone, user_id),
            )
        session["display_name"] = display_name
        notify_peers(user_id)
        socketio.emit("contacts_updated", {"contacts": get_contact_list(user_id)}, room=f"user:{user_id}")
    return jsonify({"profile": profile_json(user_by_id(user_id))})


@app.route("/api/users")
def api_users():
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    me = int(session["user_id"])
    term = (request.args.get("q") or "").strip()
    like = f"%{term}%"
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, username, display_name, bio, avatar_color FROM users
            WHERE id != ? AND (? = '' OR LOWER(username) LIKE LOWER(?) OR LOWER(display_name) LIKE LOWER(?) OR phone_number LIKE ?)
            ORDER BY display_name, username LIMIT 60
            """,
            (me, term, like, like, like),
        ).fetchall()
    users = []
    for row in rows:
        item = dict(row)
        item["avatar_text"] = initials(item["display_name"] or item["username"])
        item["online"] = item["id"] in ONLINE_USERS
        users.append(item)
    return jsonify({"users": users})


@app.route("/api/chat/start", methods=["POST"])
def api_chat_start():
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    me = int(session["user_id"])
    peer_id = int((request.get_json(silent=True) or {}).get("user_id") or 0)
    if peer_id == me or user_by_id(peer_id) is None:
        return jsonify({"error": "User not found"}), 404
    conversation_id = get_or_create_direct_conversation(me, peer_id)
    for uid in (me, peer_id):
        socketio.emit("contacts_updated", {"contacts": get_contact_list(uid)}, room=f"user:{uid}")
    return jsonify({"status": "ok", "conversation_id": conversation_id})


@app.route("/api/calls")
def api_calls():
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"calls": get_call_history(int(session["user_id"]))})


@app.route("/api/contacts")
def api_contacts():
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"contacts": get_contact_list(int(session["user_id"]))})


@app.route("/api/chat/<int:conversation_id>")
def api_chat(conversation_id: int):
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    if not is_member(conversation_id, int(session["user_id"])):
        return jsonify({"error": "Conversation not found"}), 404
    payload = get_chat_payload(conversation_id, int(session["user_id"]))
    if payload is None:
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify(payload)


@app.route("/contacts")
def contacts_page():
    return redirect(url_for("messenger"))


@app.route("/calls")
def calls_page():
    return redirect(url_for("messenger"))


legacy_pages = contacts_page


@app.route("/api/contacts/add", methods=["POST"])
def api_add_contact():
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or payload.get("username") or payload.get("phone_number") or "").strip()
    if not query:
        query = (request.form.get("query") or request.form.get("username") or request.form.get("phone_number") or "").strip()
    if not query:
        if request.form:
            return redirect(url_for("contacts_page"))
        return jsonify({"error": "Phone number or username is required"}), 400

    current_user_id = int(session["user_id"])
    matches = search_users_for_contacts(current_user_id, query)
    if not matches:
        if request.form:
            return redirect(url_for("contacts_page", q=query))
        return jsonify({"error": "User not found"}), 404

    contact = matches[0]
    contact_user_id = int(contact["id"])
    if contact_user_id == current_user_id:
        if request.form:
            return redirect(url_for("contacts_page", q=query))
        return jsonify({"error": "You cannot add yourself to contacts"}), 400

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM user_contacts WHERE user_id = ? AND contact_user_id = ?",
            (current_user_id, contact_user_id),
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO user_contacts(user_id, contact_user_id, nickname) VALUES (?, ?, ?)",
                (current_user_id, contact_user_id, contact["display_name"]),
            )
        conn.commit()

    conversation_id = get_or_create_direct_conversation(current_user_id, contact_user_id)

    if request.form:
        return redirect(url_for("messenger", chat_id=conversation_id))
    return jsonify({"status": "ok", "contact": contact, "conversation_id": conversation_id, "exists": bool(existing) if 'existing' in locals() else False})


@app.route("/contacts/<int:contact_user_id>/chat", methods=["POST"])
def start_contact_chat(contact_user_id: int):
    if "user_id" not in session:
        return redirect(url_for("login"))

    current_user_id = int(session["user_id"])
    if contact_user_id == current_user_id:
        return redirect(url_for("contacts_page"))

    conversation_id = get_or_create_direct_conversation(current_user_id, contact_user_id)
    return redirect(url_for("messenger", chat_id=conversation_id))


@app.route("/api/messages/<int:message_id>/edit", methods=["POST"])
def api_edit_message(message_id: int):
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    new_text = (payload.get("text") or "").strip()
    if not new_text:
        return jsonify({"error": "Message text is required"}), 400

    user_id = int(session["user_id"])
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, sender_id, conversation_id, text, message_type, deleted_at FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            return jsonify({"error": "Message not found"}), 404
        if row["deleted_at"]:
            return jsonify({"error": "Message was deleted"}), 404
        if int(row["sender_id"]) != user_id:
            return jsonify({"error": "You can only edit your own messages"}), 403
        if row["message_type"] != "text":
            return jsonify({"error": "Only text messages can be edited"}), 400

        conn.execute(
            "UPDATE messages SET text = ?, edited_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_text, message_id),
        )
        conn.commit()
        updated = conn.execute(
            """
            SELECT m.id, m.conversation_id, m.sender_id, m.text, m.created_at,
                   m.message_type, m.attachment_type, m.attachment_name, m.attachment_url,
                   m.edited_at, m.deleted_at,
                   u.display_name AS sender_name, u.avatar_color AS sender_color, u.username AS sender_username
            FROM messages m
            JOIN users u ON u.id = m.sender_id
            WHERE m.id = ?
            """,
            (message_id,),
        ).fetchone()

    members = get_conversation_members(int(row["conversation_id"]))
    payload = {"status": "ok", "message": dict(updated)}
    for member in members:
        member_id = int(member["id"])
        socketio.emit("message_updated", payload, room=f"user:{member_id}")
        socketio.emit("contacts_updated", {"contacts": get_contact_list(member_id)}, room=f"user:{member_id}")
    return jsonify(payload)


@app.route("/api/messages/<int:message_id>/delete", methods=["POST"])
def api_delete_message(message_id: int):
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = int(session["user_id"])
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, sender_id, conversation_id, text, message_type, deleted_at FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            return jsonify({"error": "Message not found"}), 404
        if row["deleted_at"]:
            return jsonify({"error": "Message was already deleted"}), 400
        if int(row["sender_id"]) != user_id:
            return jsonify({"error": "You can only delete your own messages"}), 403

        conn.execute(
            "UPDATE messages SET deleted_at = CURRENT_TIMESTAMP, text = '[deleted]' WHERE id = ?",
            (message_id,),
        )
        conn.commit()

    members = get_conversation_members(int(row["conversation_id"]))
    payload = {"status": "ok", "message_id": message_id, "conversation_id": int(row["conversation_id"])}
    for member in members:
        member_id = int(member["id"])
        socketio.emit("message_deleted", payload, room=f"user:{member_id}")
        socketio.emit("contacts_updated", {"contacts": get_contact_list(member_id)}, room=f"user:{member_id}")
    return jsonify(payload)


@app.route("/api/call/<call_type>", methods=["POST"])
def api_call(call_type: str):
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    call_type = (call_type or "voice").lower()
    if call_type not in {"voice", "video"}:
        return jsonify({"error": "Call type must be voice or video"}), 400

    payload = request.get_json(silent=True) or {}
    conversation_id = int(payload.get("conversation_id") or 0)
    recipient_id = int(payload.get("recipient_id") or 0)

    if conversation_id <= 0 and recipient_id <= 0:
        return jsonify({"error": "Select a contact to call"}), 400

    user_id = int(session["user_id"])
    if conversation_id > 0:
        current_chat = get_chat_payload(conversation_id, user_id)
        if current_chat is None:
            return jsonify({"error": "Conversation not found"}), 404
        peer = next((member for member in current_chat["conversation"]["members"] if int(member["id"]) != user_id), None)
        recipient_id = int(peer["id"]) if peer else recipient_id
    elif recipient_id > 0:
        recipient_id = recipient_id

    peer_user = user_by_id(recipient_id)
    if peer_user is None:
        return jsonify({"error": "User not found"}), 404

    with get_db() as conn:
        conn.execute(
            "INSERT INTO call_history(user_id, peer_id, call_type, status, created_at) VALUES (?, ?, ?, 'completed', CURRENT_TIMESTAMP)",
            (user_id, int(peer_user["id"]), call_type),
        )
        conn.commit()

    return jsonify({
        "status": "ringing",
        "type": call_type,
        "recipient": {"id": int(peer_user["id"]), "display_name": peer_user["display_name"], "username": peer_user["username"]},
        "conversation_id": conversation_id,
        "message": f"{call_type.title()} call started with {peer_user['display_name']}"
    })


@app.route("/api/groups", methods=["POST"])
def api_groups():
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    member_ids = payload.get("member_ids") or []
    if not title:
        return jsonify({"error": "Group name is required"}), 400
    if not member_ids:
        return jsonify({"error": "Select at least one member"}), 400

    user_id = int(session["user_id"])
    selected_ids = sorted({int(member_id) for member_id in member_ids if member_id})
    selected_ids = [member_id for member_id in selected_ids if member_id != user_id]
    selected_ids.append(user_id)
    selected_ids = sorted(set(selected_ids))

    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO conversations(type, title, created_by, user1_id, user2_id, updated_at) VALUES ('group', ?, ?, NULL, NULL, CURRENT_TIMESTAMP)",
            (title, user_id),
        )
        conversation_id = int(cursor.lastrowid)
        conn.commit()
        ensure_conversation_members(conversation_id, selected_ids)
        conn.execute(
            "INSERT OR IGNORE INTO notifications(user_id, conversation_id, unread_count, last_message, updated_at) VALUES (?, ?, 0, 'New group', CURRENT_TIMESTAMP)",
            (user_id, conversation_id),
        )
        for member_id in selected_ids:
            if member_id == user_id:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO notifications(user_id, conversation_id, unread_count, last_message, updated_at) VALUES (?, ?, 0, 'New group', CURRENT_TIMESTAMP)",
                (member_id, conversation_id),
            )
        conn.commit()

    group_preview = {
        "conversation_id": conversation_id,
        "id": conversation_id,
        "type": "group",
        "title": title,
        "display_name": title,
        "status": "group",
        "username": "group",
        "avatar_text": initials(title),
        "avatar_color": avatar_color(title),
        "member_count": len(selected_ids),
        "last_message": "New group",
        "last_message_time": None,
        "unread_count": 0,
    }
    for member_id in selected_ids:
        socketio.emit("contacts_updated", {"contacts": get_contact_list(member_id)}, room=f"user:{member_id}")
    return jsonify({"status": "ok", "conversation": group_preview})


@app.route("/api/send", methods=["POST"])
def api_send():
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    sender_id = int(session["user_id"])
    conversation_id = 0
    recipient_id = 0
    data = request.get_json(silent=True) or {}
    if isinstance(data, dict):
        conversation_id = int(data.get("conversation_id") or 0)
        recipient_id = int(data.get("recipient_id") or 0)

    if conversation_id <= 0:
        conversation_id = int(request.form.get("conversation_id") or 0)
    if recipient_id <= 0:
        recipient_id = int(request.form.get("recipient_id") or 0)

    if conversation_id <= 0 and recipient_id <= 0:
        return jsonify({"error": "Select a chat to send a message"}), 400

    if conversation_id <= 0:
        if recipient_id == sender_id or user_by_id(recipient_id) is None:
            return jsonify({"error": "User not found"}), 404
        conversation_id = get_or_create_direct_conversation(sender_id, recipient_id)
    elif not is_member(conversation_id, sender_id):
        return jsonify({"error": "Conversation not found"}), 404

    attachment_url = None
    attachment_name = None
    attachment_type = None
    message_type = "text"
    media_file = request.files.get("media") if request.files else None

    if media_file and media_file.filename:
        # secure_filename вырезает кириллицу, поэтому имя для показа чистим сами (на диске файл хранится под uuid)
        original = Path((media_file.filename or "").replace("\\", "/")).name.strip()
        safe_name = re.sub(r'[\x00-\x1f<>:"|?*]', "", original)[:120]
        if not safe_name:
            return jsonify({"error": "Choose a valid file"}), 400
        extension = Path(safe_name).suffix.lower()
        if extension not in ALLOWED_EXT:
            return jsonify({"error": "Этот тип файла не поддерживается"}), 400
        unique_name = f"{uuid.uuid4().hex}{extension}"
        destination = UPLOAD_FOLDER / unique_name
        media_file.save(destination)
        attachment_url = f"/static/uploads/{unique_name}"
        attachment_name = safe_name
        attachment_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        message_type = "media"

    text = ""
    if request.form.get("text") is not None:
        text = (request.form.get("text") or "").strip()
    elif isinstance(data, dict):
        text = (data.get("text") or "").strip()

    if not text and not attachment_url:
        return jsonify({"error": "Please add a message or media"}), 400

    with get_db() as conn:
        row = conn.execute(
            "INSERT INTO messages(conversation_id, sender_id, text, message_type, attachment_type, attachment_name, attachment_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (conversation_id, sender_id, text or "", message_type, attachment_type, attachment_name, attachment_url),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conversation_id,),
        )
        conn.commit()
        new_message = conn.execute(
            """
            SELECT m.id, m.conversation_id, m.sender_id, m.text, m.created_at,
                   m.message_type, m.attachment_type, m.attachment_name, m.attachment_url,
                   u.display_name AS sender_name, u.avatar_color AS sender_color, u.username AS sender_username
            FROM messages m
            JOIN users u ON u.id = m.sender_id
            WHERE m.id = ?
            """,
            (row.lastrowid,),
        ).fetchone()

    members = get_conversation_members(conversation_id)
    preview = text or (f"📎 {attachment_name}" if attachment_name else "Media message")
    for member in members:
        member_id = int(member["id"])
        if member_id == sender_id:
            continue
        persist_notification(member_id, conversation_id, preview)

    payload = {"message": dict(new_message), "conversation_id": conversation_id}
    for member in members:
        member_id = int(member["id"])
        socketio.emit("message_received", payload, room=f"user:{member_id}")
        socketio.emit("contacts_updated", {"contacts": get_contact_list(member_id)}, room=f"user:{member_id}")

    return jsonify({"status": "ok", "message": dict(new_message)})


@socketio.on("connect")
def handle_connect():
    user_id = session.get("user_id")
    if user_id is None:
        return
    user_id = int(user_id)
    join_room(f"user:{user_id}")
    ONLINE_COUNTS[user_id] = ONLINE_COUNTS.get(user_id, 0) + 1
    first = user_id not in ONLINE_USERS
    ONLINE_USERS.add(user_id)
    emit("presence", {"user_id": user_id, "online": True}, to=f"user:{user_id}")
    socketio.emit("contacts_updated", {"contacts": get_contact_list(user_id)}, room=f"user:{user_id}")
    if first:
        notify_peers(user_id)


@socketio.on("disconnect")
def handle_disconnect():
    user_id = session.get("user_id")
    if not user_id:
        return
    user_id = int(user_id)
    ONLINE_COUNTS[user_id] = max(0, ONLINE_COUNTS.get(user_id, 1) - 1)
    if ONLINE_COUNTS[user_id] == 0:
        ONLINE_USERS.discard(user_id)
        notify_peers(user_id)


@socketio.on("call_signal")
def handle_call_signal(data):
    me = session.get("user_id")
    if not me or not isinstance(data, dict):
        return
    me = int(me)
    target = int(data.get("to") or 0)
    kind = data.get("kind")
    if kind not in {"offer", "answer", "ice", "end", "reject"} or target <= 0 or target == me:
        return
    if kind == "offer" and target not in ONLINE_USERS:
        socketio.emit("call_signal", {"from": target, "kind": "unavailable"}, room=f"user:{me}")
        return
    user = user_by_id(me)
    socketio.emit(
        "call_signal",
        {
            "from": me,
            "from_name": user["display_name"] or user["username"],
            "from_color": user["avatar_color"],
            "kind": kind,
            "call_type": data.get("call_type"),
            "payload": data.get("payload"),
        },
        room=f"user:{target}",
    )


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


@app.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(app.static_folder, "manifest.webmanifest", mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    response = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/api/ice")
def api_ice():
    """Серверы для звонков. Для связи между разными сетями (Wi-Fi <-> мобильный интернет) нужен TURN:
    задайте TURN_URL, TURN_USER, TURN_PASS (например turn:turn.example.com:3478)."""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    servers = [{"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]}]
    turn_url = os.environ.get("TURN_URL")
    if turn_url:
        servers.append({
            "urls": [u.strip() for u in turn_url.split(",") if u.strip()],
            "username": os.environ.get("TURN_USER", ""),
            "credential": os.environ.get("TURN_PASS", ""),
        })
    return jsonify({"iceServers": servers})


@app.route("/api/chat/<int:conversation_id>/read", methods=["POST"])
def api_chat_read(conversation_id: int):
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    user_id = int(session["user_id"])
    if not is_member(conversation_id, user_id):
        return jsonify({"error": "Conversation not found"}), 404
    with get_db() as conn:
        conn.execute(
            "UPDATE notifications SET unread_count = 0, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND conversation_id = ?",
            (user_id, conversation_id),
        )
        conn.commit()
    socketio.emit("contacts_updated", {"contacts": get_contact_list(user_id)}, room=f"user:{user_id}")
    return jsonify({"status": "ok"})


@socketio.on("typing")
def handle_typing(data):
    me = session.get("user_id")
    if not me or not isinstance(data, dict):
        return
    me = int(me)
    try:
        conversation_id = int(data.get("conversation_id") or 0)
    except (TypeError, ValueError):
        return
    if conversation_id <= 0 or not is_member(conversation_id, me):
        return
    user = user_by_id(me)
    name = (user["display_name"] or user["username"]) if user else ""
    for member in get_conversation_members(conversation_id):
        if int(member["id"]) != me:
            socketio.emit(
                "typing",
                {"conversation_id": conversation_id, "user_id": me, "name": name},
                room=f"user:{int(member['id'])}",
            )


ensure_schema()

if __name__ == "__main__":
    print(f"Messenger запущен: http://localhost:{PORT}  (остановка: Ctrl+C)", flush=True)
    cert, key = os.environ.get("SSL_CERT"), os.environ.get("SSL_KEY")
    if cert and key:
        socketio.run(app, host="0.0.0.0", port=PORT, ssl_context=(cert, key), allow_unsafe_werkzeug=True)
    elif os.environ.get("USE_WAITRESS") == "1" and waitress_serve is not None:
        waitress_serve(app, host="0.0.0.0", port=PORT, threads=32)  # без WebSocket, только long-polling
    else:
        socketio.run(app, host="0.0.0.0", port=PORT, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
