"""Валідація вхідних даних. Правила тут, щоб роути лишалися короткими."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models import Category, Role


class RegisterIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("name")
    @classmethod
    def trim(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Вкажіть ім'я")
        return value


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ClaimIn(BaseModel):
    amount_usd: Decimal = Field(gt=0, le=Decimal("1000000"), max_digits=12, decimal_places=2)
    category: Category
    description: str = Field(min_length=5, max_length=2000)
    expense_date: date
    payment_details: str = Field(min_length=3, max_length=500)

    @field_validator("expense_date")
    @classmethod
    def not_in_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("Дата витрати не може бути в майбутньому")
        return value


class RejectIn(BaseModel):
    """Відхилення без коментаря не проходить — це правило живе тут."""

    comment: str = Field(min_length=3, max_length=1000)

    @field_validator("comment")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Коментар обов'язковий при відхиленні")
        return value


class RolesIn(BaseModel):
    """Адміністратор призначає ролі користувачу."""

    roles: list[Role] = Field(min_length=1)


class RoutingIn(BaseModel):
    """Адміністратор призначає погоджувача категорії."""

    approver_id: int
