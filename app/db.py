"""Підключення до PostgreSQL."""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://expense:expense@localhost:5434/expense_approval",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    """Залежність FastAPI: одна сесія БД на один запит."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
