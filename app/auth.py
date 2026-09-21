"""Паролі, сесії, перевірка ролей."""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DbSession

from app.db import get_db
from app.models import LoginAttempt, Role, Session, User

SESSION_COOKIE = "session"
PBKDF2_ROUNDS = 200_000

# Скільки живе сесія від моменту входу
SESSION_TTL = timedelta(days=int(os.getenv("SESSION_TTL_DAYS", "7")))

# Захист від перебору пароля: скільки невдалих спроб і за який час
MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
LOGIN_WINDOW = timedelta(minutes=int(os.getenv("LOGIN_WINDOW_MINUTES", "15")))


def now() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS)
    return secrets.compare_digest(check.hex(), digest)


def create_session(db: DbSession, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(Session(token=token, user_id=user.id, expires_at=now() + SESSION_TTL))
    # заразом прибираємо протухлі сесії цієї людини
    db.execute(delete(Session).where(Session.user_id == user.id, Session.expires_at <= now()))
    db.commit()
    return token


def drop_session(db: DbSession, token: str) -> None:
    session = db.get(Session, token)
    if session:
        db.delete(session)
        db.commit()


def current_user(
    session: str | None = Cookie(default=None),
    db: DbSession = Depends(get_db),
) -> User:
    """Залежність FastAPI: хто зараз користується системою."""
    if not session:
        raise HTTPException(status_code=401, detail="Потрібно увійти")

    row = db.get(Session, session)
    if row is None:
        raise HTTPException(status_code=401, detail="Сесія недійсна, увійдіть ще раз")

    if row.expires_at <= now():
        db.delete(row)
        db.commit()
        raise HTTPException(status_code=401, detail="Сесія завершилася, увійдіть ще раз")

    return db.get(User, row.user_id)


def requires(role: Role, message: str):
    """Фабрика залежностей: пускає далі лише власника потрібної ролі."""

    def dependency(user: User = Depends(current_user)) -> User:
        if not user.has(role):
            raise HTTPException(status_code=403, detail=message)
        return user

    return dependency


require_employee = requires(Role.employee, "Ви не можете подавати заявки")
require_approver = requires(Role.approver, "Ви не погоджувач")
require_admin = requires(Role.admin, "Потрібні права адміністратора")


# --- захист входу від перебору ---------------------------------------------

def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def check_login_allowed(db: DbSession, email: str) -> None:
    """Кидає 429, якщо для цього email забагато невдалих спроб поспіль."""
    db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < now() - LOGIN_WINDOW))
    db.commit()

    failures = db.scalar(
        select(func.count()).select_from(LoginAttempt).where(LoginAttempt.email == email)
    )
    if failures >= MAX_LOGIN_ATTEMPTS:
        minutes = int(LOGIN_WINDOW.total_seconds() // 60)
        raise HTTPException(
            status_code=429,
            detail=f"Забагато невдалих спроб. Спробуйте ще раз через {minutes} хв",
            headers={"Retry-After": str(int(LOGIN_WINDOW.total_seconds()))},
        )


def record_failed_login(db: DbSession, email: str, ip: str) -> None:
    db.add(LoginAttempt(email=email, ip=ip))
    db.commit()


def clear_failed_logins(db: DbSession, email: str) -> None:
    db.execute(delete(LoginAttempt).where(LoginAttempt.email == email))
    db.commit()
