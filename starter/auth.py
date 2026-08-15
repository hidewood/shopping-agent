"""User authentication: SQLite users table + JWT + bcrypt password hashing.

MVP 阶段用 SQLite + 标准库 sqlite3，后续可平滑迁移 PostgreSQL（换连接即可）。
用户数据存 ``local_state/users.db``（gitignored，不提交）。
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from dotenv import load_dotenv
from passlib.context import CryptContext

PROJECT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_DIR / ".env")
DB_PATH = PROJECT_DIR / "local_state" / "users.db"

# JWT 配置。开发环境允许短期默认值；生产环境必须显式配置随机密钥。
_DEFAULT_DEV_SECRET = "dev-secret-change-me-in-production"
_APP_ENV = os.getenv("APP_ENV", "development").strip().casefold()
SECRET_KEY = os.getenv("JWT_SECRET_KEY", _DEFAULT_DEV_SECRET)
if _APP_ENV == "production" and (not SECRET_KEY or SECRET_KEY == _DEFAULT_DEV_SECRET):
    raise RuntimeError("JWT_SECRET_KEY must be explicitly set when APP_ENV=production.")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthError(Exception):
    """Raised when a credential or token is invalid."""


def configure_db_path(path: str | Path) -> None:
    """Point the module at an isolated SQLite database (primarily for tests)."""
    global DB_PATH
    DB_PATH = Path(path)
    init_db()


def _connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name          TEXT,
            role          TEXT NOT NULL DEFAULT 'user',
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # 迁移：旧表可能没有 role 字段
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "role" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    conn.commit()
    conn.close()


def _hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def _verify_password(password: str, password_hash: str) -> bool:
    return _pwd_context.verify(password, password_hash)


def create_user(email: str, password: str, name: str | None = None) -> dict:
    """Create a user.  Raises AuthError if the email is already taken."""
    email = email.strip().lower()
    if not email or not password:
        raise AuthError("邮箱和密码不能为空")
    # Public registration can never grant an administrative role. The initial
    # administrator is created only by the explicit bootstrap path below.
    role = "user"
    user_id = uuid.uuid4().hex
    conn = _connection()
    try:
        conn.execute(
            "INSERT INTO users (id, email, password_hash, name, role) VALUES (?, ?, ?, ?, ?)",
            (user_id, email, _hash_password(password), name, role),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise AuthError("该邮箱已注册") from None
    finally:
        conn.close()
    return {"id": user_id, "email": email, "name": name, "role": role}


def authenticate_user(email: str, password: str) -> dict:
    """Verify credentials and return the user dict, or raise AuthError."""
    email = email.strip().lower()
    conn = _connection()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    if row is None or not _verify_password(password, row["password_hash"]):
        raise AuthError("邮箱或密码错误")
    return {"id": row["id"], "email": row["email"], "name": row["name"], "role": row["role"]}


def get_user_by_id(user_id: str) -> dict | None:
    conn = _connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    return {"id": row["id"], "email": row["email"], "name": row["name"], "role": row["role"]}


def set_admin_role(email: str) -> None:
    """Promote a user to admin."""
    conn = _connection()
    conn.execute("UPDATE users SET role = 'admin' WHERE email = ?", (email.strip().lower(),))
    conn.commit()
    conn.close()


def bootstrap_initial_admin() -> None:
    """Create the configured initial administrator exactly once.

    Credentials remain in environment variables and the password is immediately
    hashed. Existing accounts keep their password and profile intact.
    """
    email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("ADMIN_INITIAL_PASSWORD", "")
    if not email or len(password) < 6:
        return
    conn = _connection()
    exists = conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
    if exists is None:
        conn.execute(
            "INSERT INTO users (id, email, password_hash, name, role) VALUES (?, ?, ?, ?, 'admin')",
            (uuid.uuid4().hex, email, _hash_password(password), "管理员"),
        )
        conn.commit()
    # Never promote an account that was created through public registration.
    # Operators must choose a fresh ADMIN_EMAIL or promote it out-of-band.
    conn.close()


def list_users() -> list[dict]:
    """Return all users (id/email/name/role/created_at)."""
    conn = _connection()
    rows = conn.execute("SELECT id, email, name, role, created_at FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str:
    """Decode a token and return the user_id, or raise AuthError."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload["sub"]
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, KeyError):
        raise AuthError("无效或过期的令牌") from None


init_db()
bootstrap_initial_admin()
