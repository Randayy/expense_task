"""Наповнення бази прикладами — щоб було що дивитися й тестувати.

Це НЕ частина застосунку: жодних демо-користувачів він сам не створює.
Команду запускають руками на тестовому середовищі:

    python -m app.cli demo-data
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.auth import hash_password
from app.db import SessionLocal
from app.models import Category, CategoryApprover, Claim, Role, Status, User, UserRole

PASSWORD = "demo12345"

# Хто є в компанії. Марія має обидві ролі одночасно — так буває в малій команді.
PEOPLE = [
    ("admin@acme.com", "Адміністратор", [Role.admin, Role.employee]),
    ("ivan@acme.com", "Іван Ковальчук", [Role.employee]),
    ("sofia@acme.com", "Софія Литвин", [Role.employee]),
    ("olena@acme.com", "Олена Шевчук", [Role.approver]),
    ("petro@acme.com", "Петро Мельник", [Role.approver]),
    ("maria@acme.com", "Марія Бондар", [Role.employee, Role.approver]),
]

# Маршрутизація: кожній категорії — свій погоджувач
ROUTING = {
    Category.office: "olena@acme.com",
    Category.software: "olena@acme.com",
    Category.travel: "petro@acme.com",
    Category.client_entertainment: "petro@acme.com",
    Category.other: "maria@acme.com",
}

# Заявки: автор, категорія, сума, опис, скільки днів тому, чим скінчилось.
# Перша — навмисне неузгоджена: категорія Office, а в описі авіаквиток.
CLAIMS = [
    ("ivan@acme.com", Category.office, "480.00",
     "Квиток на літак до Лондона на конференцію", 3, None, None),
    ("ivan@acme.com", Category.office, "89.40",
     "Дві офісні лампи та подовжувач", 5, None, None),
    ("sofia@acme.com", Category.travel, "210.00",
     "Потяг Київ-Львів на зустріч із клієнтом, туди й назад", 6, None, None),
    ("sofia@acme.com", Category.software, "45.90",
     "Підписка Figma на вересень", 8, Status.rejected,
     "Потрібен чек — додай, будь ласка, і подавай знову"),
    ("ivan@acme.com", Category.software, "120.00",
     "Ліцензія JetBrains на рік", 10, Status.approved, None),
    ("maria@acme.com", Category.client_entertainment, "260.75",
     "Вечеря з клієнтом після підписання контракту, троє людей", 12, Status.approved, None),
    ("sofia@acme.com", Category.other, "35.00",
     "Таксі з аеропорту після відрядження", 14, Status.withdrawn, None),
    ("ivan@acme.com", Category.travel, "1450.00",
     "Готель у Берліні, чотири ночі", 15, Status.rejected,
     "Перевищено ліміт на проживання — оформи різницю окремо"),
    ("maria@acme.com", Category.office, "18.20",
     "Кава та чай для кухні", 18, Status.approved, None),
]


def seed() -> None:
    with SessionLocal() as db:
        if db.scalar(select(Claim).limit(1)) is not None:
            print("У базі вже є заявки — нічого не додаю.")
            print("Щоб почати з чистого аркуша: docker compose down -v && docker compose up -d")
            return

        people = {}
        for email, name, roles in PEOPLE:
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    email=email,
                    name=name,
                    password_hash=hash_password(PASSWORD),
                    role_rows=[UserRole(role=role) for role in roles],
                )
                db.add(user)
            people[email] = user
        db.flush()

        for category, approver_email in ROUTING.items():
            db.merge(CategoryApprover(category=category, approver_id=people[approver_email].id))
        db.flush()

        for email, category, amount, description, days_ago, status, comment in CLAIMS:
            author = people[email]
            approver = people[ROUTING[category]]
            if approver.id == author.id:  # сам себе ніхто не погоджує
                approver = people["olena@acme.com"]

            db.add(
                Claim(
                    employee_id=author.id,
                    approver_id=approver.id,
                    amount_usd=Decimal(amount),
                    category=category,
                    description=description,
                    expense_date=date.today() - timedelta(days=days_ago),
                    payment_details="Картка ****1234",
                    status=status or Status.pending,
                    decision_comment=comment,
                )
            )

        db.commit()

    print(f"Готово. Додано {len(PEOPLE)} людей і {len(CLAIMS)} заявок.")
    print(f"Пароль у всіх: {PASSWORD}\n")
    for email, name, roles in PEOPLE:
        print(f"  {email:<22} {name:<20} {', '.join(r.value for r in roles)}")
