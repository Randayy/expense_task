"""Спільна підготовка тестів.

Тести ходять у справжній PostgreSQL — в окрему базу, яка створюється на час
прогону. Перед кожним тестом таблиці чистяться, щоб тести не залежали один
від одного.
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url

# Тестова база — сусідня до робочої. Треба виставити ДО імпорту app.*
MAIN_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://expense:expense@localhost:5434/expense_approval"
)
# render_as_string(hide_password=False) — бо str(url) замінює пароль зірочками
_main = make_url(MAIN_URL)
TEST_URL = _main.set(database=_main.database + "_test").render_as_string(hide_password=False)
os.environ["DATABASE_URL"] = TEST_URL
os.environ.pop("OPENAI_API_KEY", None)  # AI у тестах свідомо вимкнений

from fastapi.testclient import TestClient  # noqa: E402

from app.auth import hash_password  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Category, CategoryApprover, Role, User, UserRole  # noqa: E402

PASSWORD = "secret123"


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Створює тестову базу на час прогону і прибирає її після."""
    url = make_url(TEST_URL)
    admin_url = url.set(database="postgres").render_as_string(hide_password=False)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")

    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))

    Base.metadata.create_all(engine)
    yield

    engine.dispose()
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
    admin_engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables():
    """Чистий аркуш перед кожним тестом."""
    tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_user(db):
    """Створює користувача з потрібними ролями напряму в базі."""

    def factory(email: str, name: str, *roles: Role) -> User:
        user = User(
            email=email,
            name=name,
            password_hash=hash_password(PASSWORD),
            role_rows=[UserRole(role=role) for role in (roles or (Role.employee,))],
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    return factory


@pytest.fixture
def route(db):
    """Призначає погоджувача категорії (те, що в житті робить адміністратор)."""

    def assign(category: Category, approver: User) -> None:
        db.merge(CategoryApprover(category=category, approver_id=approver.id))
        db.commit()

    return assign


def login(client, email: str, password: str = PASSWORD):
    response = client.post("/api/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()
