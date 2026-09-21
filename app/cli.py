"""Невелика консольна утиліта.

Потрібна рівно для одного: створити першого адміністратора одразу після
розгортання. Далі всім керують через інтерфейс.

    python -m app.cli create-admin --email admin@company.com --name "Ім'я"
    python -m app.cli set-password --email someone@company.com
    python -m app.cli list-users
    python -m app.cli demo-data
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


def set_password(email: str, password: str | None) -> None:
    """Скинути пароль. Рятує, коли людина забула, з чим реєструвалася."""
    email = email.strip().lower()
    password = password or getpass.getpass("Новий пароль: ")
    if len(password) < 8:
        sys.exit("Пароль має бути не коротшим за 8 символів")

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            sys.exit(f"Користувача {email} немає в базі")
        user.password_hash = hash_password(password)
        db.commit()

    print(f"Пароль оновлено: {email}")


def list_users() -> None:
    """Хто взагалі є в системі і з якими ролями."""
    with SessionLocal() as db:
        users = db.scalars(select(User).order_by(User.id)).unique().all()

    if not users:
        print("Користувачів ще немає — створіть адміністратора командою create-admin")
        return

    for user in users:
        roles = ", ".join(sorted(role.value for role in user.roles))
        print(f"{user.id:>3}  {user.email:<32} {user.name:<24} [{roles}]")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    admin = commands.add_parser("create-admin", help="створити першого адміністратора")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)
    admin.add_argument("--password", help="якщо не вказати — спитає інтерактивно")

    password = commands.add_parser("set-password", help="скинути пароль користувачу")
    password.add_argument("--email", required=True)
    password.add_argument("--password", help="якщо не вказати — спитає інтерактивно")

    commands.add_parser("list-users", help="показати всіх користувачів і їхні ролі")
    commands.add_parser("demo-data", help="наповнити базу прикладами для тестування")

    args = parser.parse_args()
    if args.command == "create-admin":
        create_admin(args.email, args.name, args.password)
    elif args.command == "set-password":
        set_password(args.email, args.password)
    elif args.command == "list-users":
        list_users()
    elif args.command == "demo-data":
        from app.demo import seed

        seed()


if __name__ == "__main__":
    main()
