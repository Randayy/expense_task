"""Людські повідомлення про помилки валідації.

Pydantic пише англійською й технічно — «String should have at least 8
characters». Користувач має бачити українською і про конкретне поле.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

FIELD_NAMES = {
    "name": "Ім'я",
    "email": "Email",
    "password": "Пароль",
    "amount_usd": "Сума",
    "category": "Категорія",
    "description": "Опис",
    "expense_date": "Дата витрати",
    "payment_details": "Деталі для оплати",
    "comment": "Коментар",
    "roles": "Ролі",
    "approver_id": "Погоджувач",
    "limit": "Розмір сторінки",
    "offset": "Зсув",
}


def describe(error: dict) -> str:
    """Один зрозумілий рядок замість технічного опису помилки."""
    field = FIELD_NAMES.get(str(error["loc"][-1]), "Поле")
    kind = error.get("type", "")
    ctx = error.get("ctx") or {}

    if kind == "missing":
        return f"{field}: обов'язкове поле"
    if kind in ("string_too_short", "too_short"):
        return f"{field}: щонайменше {ctx.get('min_length', ctx.get('min_length', 1))} символів"
    if kind in ("string_too_long", "too_long"):
        return f"{field}: не довше за {ctx.get('max_length')} символів"
    if kind == "greater_than":
        return f"{field}: має бути більше за {ctx.get('gt')}"
    if kind == "less_than_equal":
        return f"{field}: має бути не більше за {ctx.get('le')}"
    if kind.startswith("enum"):
        return f"{field}: недопустиме значення"
    if kind in ("int_parsing", "decimal_parsing", "float_parsing"):
        return f"{field}: треба число"
    if kind == "date_from_datetime_parsing" or kind == "date_parsing":
        return f"{field}: неправильна дата"
    if kind == "value_error":
        # це наші власні перевірки — вони вже написані українською
        message = str(error.get("msg", "")).replace("Value error, ", "")
        if "valid email" in message:
            return f"{field}: схоже на неправильну адресу"
        return message or f"{field}: неправильне значення"

    return f"{field}: неправильне значення"


def install(app: FastAPI) -> None:
    """Підміняє стандартну відповідь FastAPI на зрозумілу людині."""

    @app.exception_handler(RequestValidationError)
    async def handle(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        detail = describe(errors[0]) if errors else "Перевірте введені дані"
        return JSONResponse(status_code=422, content={"detail": detail})
