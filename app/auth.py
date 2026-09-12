"""Паролі, сесії, перевірка ролей."""

import hashlib
import secrets

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.db import get_db
from app.models import Role, Session, User

SESSION_COOKIE = "session"
PBKDF2_ROUNDS = 200_000


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
    db.add(Session(token=token, user_id=user.id))
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

    user = db.scalar(select(User).join(Session).where(Session.token == session))
    if user is None:
        raise HTTPException(status_code=401, detail="Сесія недійсна, увійдіть ще раз")
    return user


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
