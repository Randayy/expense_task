"""Невелика консольна утиліта.

Потрібна рівно для одного: створити першого адміністратора одразу після
розгортання. Далі всім керують через інтерфейс.

    python -m app.cli create-admin --email admin@company.com --name "Ім'я"
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.auth import hash_password
from app.db import SessionLocal
from app.models import Role, User, UserRole


def create_admin(email: str, name: str, password: str | None) -> None:
    email = email.strip().lower()
    password = password or getpass.getpass("Пароль: ")
    if len(password) < 8:
        sys.exit("Пароль має бути не коротшим за 8 символів")

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))

        if user is None:
            user = User(email=email, name=name, password_hash=hash_password(password))
            db.add(user)
            action = "Створено"
        else:
            action = "Оновлено"

        roles = {row.role for row in user.role_rows} | {Role.admin, Role.employee}
        user.role_rows = [UserRole(role=role) for role in roles]
        db.commit()

    print(f"{action} адміністратора: {email}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    admin = commands.add_parser("create-admin", help="створити першого адміністратора")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)
    admin.add_argument("--password", help="якщо не вказати — спитає інтерактивно")

    args = parser.parse_args()
    if args.command == "create-admin":
        create_admin(args.email, args.name, args.password)


if __name__ == "__main__":
    main()
