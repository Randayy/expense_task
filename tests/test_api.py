"""Тести на правила, які найлегше зламати при змінах."""

from datetime import date, timedelta

import pytest

from app.models import Category, Role
from tests.conftest import PASSWORD, login

YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


def submit(client, category=Category.office, amount="120.00", description="Папір і картриджі для офісу"):
    return client.post(
        "/api/claims",
        json={
            "amount_usd": amount,
            "category": category.value,
            "description": description,
            "expense_date": YESTERDAY,
            "payment_details": "Картка ****1234",
        },
    )


@pytest.fixture
def team(make_user, route):
    """Типова маленька компанія: заявник, двоє погоджувачів, адміністратор."""
    people = {
        "employee": make_user("employee@acme.com", "Заявник", Role.employee),
        "office": make_user("office@acme.com", "Погоджувач Office", Role.approver),
        "travel": make_user("travel@acme.com", "Погоджувач Travel", Role.approver),
        "admin": make_user("admin@acme.com", "Адміністратор", Role.admin, Role.employee),
    }
    route(Category.office, people["office"])
    route(Category.travel, people["travel"])
    return people


# --- реєстрація та вхід -----------------------------------------------------

def test_registration_creates_employee(client):
    response = client.post(
        "/api/register",
        json={"email": "New@Acme.com", "name": "Нова людина", "password": "password123"},
    )
    assert response.status_code == 201
    assert response.json()["roles"] == ["employee"]
    assert response.json()["email"] == "new@acme.com"  # email нормалізується


def test_duplicate_email_rejected(client):
    payload = {"email": "dup@acme.com", "name": "Хтось", "password": "password123"}
    assert client.post("/api/register", json=payload).status_code == 201
    assert client.post("/api/register", json=payload).status_code == 409


def test_short_password_rejected(client):
    response = client.post(
        "/api/register", json={"email": "a@acme.com", "name": "Хтось", "password": "korotk"}
    )
    assert response.status_code == 422


def test_wrong_password_rejected(client, team):
    assert client.post(
        "/api/login", json={"email": "employee@acme.com", "password": "невірний"}
    ).status_code == 401


def test_anonymous_sees_nothing(client):
    assert client.get("/api/claims/mine").status_code == 401


# --- маршрутизація ----------------------------------------------------------

def test_claim_goes_to_category_approver(client, team):
    login(client, "employee@acme.com")
    assert submit(client, Category.office).json()["approver_name"] == "Погоджувач Office"
    assert submit(client, Category.travel).json()["approver_name"] == "Погоджувач Travel"


def test_category_without_approver_is_blocked(client, team):
    login(client, "employee@acme.com")
    response = submit(client, Category.other)  # погоджувача не призначали
    assert response.status_code == 409
    assert "не призначено погоджувача" in response.json()["detail"]


def test_approver_does_not_approve_own_claim(client, team, make_user, route):
    """Погоджувач із роллю співробітника подає заявку у «свою» категорію."""
    dual = make_user("dual@acme.com", "Обидві ролі", Role.employee, Role.approver)
    route(Category.other, dual)

    login(client, "dual@acme.com")
    claim = submit(client, Category.other).json()
    assert claim["approver_name"] != "Обидві ролі"


def test_unknown_category_rejected(client, team):
    login(client, "employee@acme.com")
    response = client.post(
        "/api/claims",
        json={
            "amount_usd": "10.00",
            "category": "Космос",
            "description": "Політ на Марс",
            "expense_date": YESTERDAY,
            "payment_details": "Картка",
        },
    )
    assert response.status_code == 422


def test_future_date_rejected(client, team):
    login(client, "employee@acme.com")
    response = client.post(
        "/api/claims",
        json={
            "amount_usd": "10.00",
            "category": Category.office.value,
            "description": "Щось із майбутнього",
            "expense_date": (date.today() + timedelta(days=1)).isoformat(),
            "payment_details": "Картка",
        },
    )
    assert response.status_code == 422


def test_amount_keeps_cents(client, team):
    """Гроші зберігаються як NUMERIC — копійки не «пливуть»."""
    login(client, "employee@acme.com")
    assert submit(client, Category.office, amount="0.07").json()["amount_usd"] == 0.07
    assert submit(client, Category.office, amount="12345.67").json()["amount_usd"] == 12345.67


# --- видимість --------------------------------------------------------------

def test_queue_holds_only_my_claims(client, team):
    login(client, "employee@acme.com")
    submit(client, Category.office)
    submit(client, Category.travel)

    login(client, "travel@acme.com")
    queue = client.get("/api/claims/queue").json()
    assert len(queue) == 1
    assert queue[0]["category"] == "Travel"


def test_stranger_cannot_open_claim(client, team):
    login(client, "employee@acme.com")
    claim_id = submit(client, Category.office).json()["id"]

    login(client, "travel@acme.com")  # погоджувач, але не цієї заявки
    assert client.get(f"/api/claims/{claim_id}").status_code == 404


def test_employee_sees_only_own_claims(client, team):
    login(client, "employee@acme.com")
    submit(client, Category.office)

    login(client, "admin@acme.com")
    assert client.get("/api/claims/mine").json() == []


# --- рішення ----------------------------------------------------------------

def test_reject_without_comment_fails(client, team):
    login(client, "employee@acme.com")
    claim_id = submit(client, Category.office).json()["id"]

    login(client, "office@acme.com")
    assert client.post(f"/api/claims/{claim_id}/reject", json={"comment": ""}).status_code == 422
    assert client.post(f"/api/claims/{claim_id}/reject", json={"comment": "   "}).status_code == 422
    assert client.post(f"/api/claims/{claim_id}/reject", json={}).status_code == 422
    assert client.get(f"/api/claims/{claim_id}").json()["status"] == "pending"


def test_reject_with_comment_works(client, team):
    login(client, "employee@acme.com")
    claim_id = submit(client, Category.office).json()["id"]

    login(client, "office@acme.com")
    response = client.post(f"/api/claims/{claim_id}/reject", json={"comment": "Немає чека"})
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["decision_comment"] == "Немає чека"


def test_database_also_forbids_rejection_without_comment(db, team):
    """Те саме правило продубльоване в самій БД — на випадок обходу застосунку."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    db.execute(
        text(
            "INSERT INTO claims (employee_id, approver_id, amount_usd, category, description,"
            " expense_date, payment_details, status, created_at)"
            " VALUES (:e, :a, 10, 'Office', 'опис', :d, 'картка', 'pending', now())"
        ),
        {"e": team["employee"].id, "a": team["office"].id, "d": YESTERDAY},
    )
    db.commit()

    with pytest.raises(IntegrityError):
        db.execute(text("UPDATE claims SET status = 'rejected' WHERE id = 1"))
        db.commit()
    db.rollback()


def test_approve_works_and_is_final(client, team):
    login(client, "employee@acme.com")
    claim_id = submit(client, Category.office).json()["id"]

    login(client, "office@acme.com")
    assert client.post(f"/api/claims/{claim_id}/approve").json()["status"] == "approved"
    assert client.post(
        f"/api/claims/{claim_id}/reject", json={"comment": "передумав"}
    ).status_code == 409


def test_foreign_approver_cannot_decide(client, team):
    login(client, "employee@acme.com")
    claim_id = submit(client, Category.office).json()["id"]

    login(client, "travel@acme.com")
    assert client.post(f"/api/claims/{claim_id}/approve").status_code == 404


# --- відкликання ------------------------------------------------------------

def test_author_withdraws_pending_claim(client, team):
    login(client, "employee@acme.com")
    claim_id = submit(client, Category.office).json()["id"]
    assert client.post(f"/api/claims/{claim_id}/withdraw").json()["status"] == "withdrawn"


def test_cannot_withdraw_after_decision(client, team):
    login(client, "employee@acme.com")
    claim_id = submit(client, Category.office).json()["id"]

    login(client, "office@acme.com")
    client.post(f"/api/claims/{claim_id}/approve")

    login(client, "employee@acme.com")
    assert client.post(f"/api/claims/{claim_id}/withdraw").status_code == 409


def test_approver_cannot_withdraw_someone_elses_claim(client, team):
    login(client, "employee@acme.com")
    claim_id = submit(client, Category.office).json()["id"]

    login(client, "office@acme.com")
    assert client.post(f"/api/claims/{claim_id}/withdraw").status_code == 403


def test_withdrawn_claim_cannot_be_approved(client, team):
    login(client, "employee@acme.com")
    claim_id = submit(client, Category.office).json()["id"]
    client.post(f"/api/claims/{claim_id}/withdraw")

    login(client, "office@acme.com")
    assert client.post(f"/api/claims/{claim_id}/approve").status_code == 409


# --- ролі -------------------------------------------------------------------

def test_approver_only_user_cannot_submit(client, team):
    login(client, "office@acme.com")
    assert submit(client, Category.office).status_code == 403


def test_employee_only_user_has_no_queue(client, team):
    login(client, "employee@acme.com")
    assert client.get("/api/claims/queue").status_code == 403


def test_dual_role_user_can_do_both(client, team, make_user, route):
    dual = make_user("dual@acme.com", "Обидві ролі", Role.employee, Role.approver)
    route(Category.other, dual)

    login(client, "dual@acme.com")
    assert submit(client, Category.office).status_code == 201
    assert client.get("/api/claims/queue").status_code == 200
    assert client.get("/api/claims/mine").status_code == 200


# --- адміністрування --------------------------------------------------------

def test_admin_panel_closed_for_others(client, team):
    login(client, "employee@acme.com")
    assert client.get("/api/admin/users").status_code == 403
    assert client.get("/api/admin/routing").status_code == 403
    login(client, "office@acme.com")
    assert client.put("/api/admin/users/1/roles", json={"roles": ["admin"]}).status_code == 403


def test_admin_grants_approver_role(client, team):
    new_id = client.post(
        "/api/register", json={"email": "fresh@acme.com", "name": "Новенький", "password": "password123"}
    ).json()["id"]

    login(client, "admin@acme.com")
    response = client.put(f"/api/admin/users/{new_id}/roles", json={"roles": ["employee", "approver"]})
    assert response.status_code == 200
    assert response.json()["roles"] == ["approver", "employee"]

    login(client, "fresh@acme.com", "password123")
    assert client.get("/api/claims/queue").status_code == 200


def test_admin_reassigns_category(client, team):
    login(client, "admin@acme.com")
    response = client.put(
        f"/api/admin/routing/{Category.office.value}",
        json={"approver_id": team["travel"].id},
    )
    assert response.status_code == 200

    login(client, "employee@acme.com")
    assert submit(client, Category.office).json()["approver_name"] == "Погоджувач Travel"


def test_cannot_route_category_to_non_approver(client, team):
    login(client, "admin@acme.com")
    response = client.put(
        f"/api/admin/routing/{Category.office.value}",
        json={"approver_id": team["employee"].id},
    )
    assert response.status_code == 409
    assert "не має ролі погоджувача" in response.json()["detail"]


def test_cannot_strip_approver_who_still_owns_categories(client, team):
    """Спершу треба передати категорії, інакше заявки нікуди буде слати."""
    login(client, "admin@acme.com")
    response = client.put(
        f"/api/admin/users/{team['office'].id}/roles", json={"roles": ["employee"]}
    )
    assert response.status_code == 409
    assert "Office" in response.json()["detail"]


def test_admin_cannot_demote_self(client, team):
    login(client, "admin@acme.com")
    response = client.put(
        f"/api/admin/users/{team['admin'].id}/roles", json={"roles": ["employee"]}
    )
    assert response.status_code == 409


# --- AI ---------------------------------------------------------------------

def test_missing_ai_does_not_block_approver(client, team):
    login(client, "employee@acme.com")
    claim_id = submit(client, Category.office).json()["id"]

    login(client, "office@acme.com")
    review = client.get(f"/api/claims/{claim_id}/ai-review")
    assert review.status_code == 200
    assert review.json()["available"] is False

    assert client.post(f"/api/claims/{claim_id}/approve").status_code == 200


def test_ai_hint_is_shown_and_cached(client, team, monkeypatch):
    """Коли AI відповів — погоджувач бачить опис і прапорець; вдруге беремо з кешу."""
    from app import ai

    calls = []

    async def fake_openai(claim):
        calls.append(claim.id)
        return {
            "summary": "Квиток на літак до Лондона на 480 доларів.",
            "flagged": True,
            "flag_reason": "Категорія «Office», а опис — про авіаквиток",
        }

    monkeypatch.setattr(ai, "_ask_openai", fake_openai)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    login(client, "employee@acme.com")
    claim_id = submit(client, Category.office, "480.00", "Квиток на літак до Лондона").json()["id"]

    login(client, "office@acme.com")
    first = client.get(f"/api/claims/{claim_id}/ai-review").json()
    assert first["available"] is True
    assert first["flagged"] is True
    assert "Office" in first["flag_reason"]

    second = client.get(f"/api/claims/{claim_id}/ai-review").json()
    assert second == first
    assert calls == [claim_id]  # модель смикнули лише раз


def test_broken_ai_degrades_quietly(client, team, monkeypatch):
    from app import ai

    async def boom(claim):
        raise RuntimeError("OpenAI лежить")

    monkeypatch.setattr(ai, "_ask_openai", boom)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    login(client, "employee@acme.com")
    claim_id = submit(client, Category.office).json()["id"]

    login(client, "office@acme.com")
    review = client.get(f"/api/claims/{claim_id}/ai-review")
    assert review.status_code == 200
    assert review.json()["available"] is False
    assert client.post(f"/api/claims/{claim_id}/approve").status_code == 200
