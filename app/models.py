"""Моделі SQLAlchemy. Одна таблиця — одна зрозуміла сутність."""

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    employee = "employee"   # подає заявки
    approver = "approver"   # ухвалює рішення
    admin = "admin"         # керує ролями і маршрутизацією


class Category(str, enum.Enum):
    office = "Office"
    travel = "Travel"
    client_entertainment = "Client Entertainment"
    software = "Software/Subscriptions"
    other = "Other"


class Status(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    withdrawn = "withdrawn"


def enum_column(py_enum, name):
    """Зберігаємо значення енама (Office), а не його ім'я (office)."""
    return Enum(py_enum, name=name, values_callable=lambda e: [m.value for m in e])


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    role_rows: Mapped[list["UserRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def roles(self) -> set[Role]:
        return {row.role for row in self.role_rows}

    def has(self, role: Role) -> bool:
        return role in self.roles


class UserRole(Base):
    """Ролі окремою таблицею: одна людина може мати їх кілька."""

    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[Role] = mapped_column(enum_column(Role, "role"), primary_key=True)

    user: Mapped[User] = relationship(back_populates="role_rows")


class CategoryApprover(Base):
    """Маршрутизація: кожній категорії — свій погоджувач. Керує адміністратор."""

    __tablename__ = "category_approvers"

    category: Mapped[Category] = mapped_column(enum_column(Category, "category"), primary_key=True)
    approver_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    approver: Mapped[User] = relationship(lazy="joined")


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint("amount_usd > 0", name="amount_positive"),
        # коментар обов'язковий саме для відхилення — правило живе і в БД теж
        CheckConstraint(
            "status <> 'rejected' OR (decision_comment IS NOT NULL AND btrim(decision_comment) <> '')",
            name="rejected_needs_comment",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    approver_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)

    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    category: Mapped[Category] = mapped_column(enum_column(Category, "category"))
    description: Mapped[str] = mapped_column(Text)
    expense_date: Mapped[date] = mapped_column(Date)
    payment_details: Mapped[str] = mapped_column(String(500))

    status: Mapped[Status] = mapped_column(enum_column(Status, "status"), default=Status.pending, index=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    employee: Mapped[User] = relationship(foreign_keys=[employee_id], lazy="joined")
    approver: Mapped[User] = relationship(foreign_keys=[approver_id], lazy="joined")
    ai_review: Mapped["AiReview | None"] = relationship(
        back_populates="claim", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )


class AiReview(Base):
    """Кеш AI-висновку — щоб не смикати модель при кожному відкритті заявки."""

    __tablename__ = "ai_reviews"

    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True)
    summary: Mapped[str] = mapped_column(Text)
    flagged: Mapped[bool]
    flag_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    claim: Mapped[Claim] = relationship(back_populates="ai_review")


class Session(Base):
    """Сесія входу. Токен живе в httponly-кукі й має строк придатності."""

    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LoginAttempt(Base):
    """Невдалі спроби входу — щоб пароль не можна було підібрати перебором.

    Успішний вхід стирає історію по цьому email.
    """

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    ip: Mapped[str] = mapped_column(String(45))  # вистачить і на IPv6
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
