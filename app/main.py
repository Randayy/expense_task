"""Expense Approval — узгодження заявок на відшкодування витрат."""

from datetime import datetime, timezone
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app import ai
from app.auth import (
    SESSION_COOKIE,
    create_session,
    current_user,
    drop_session,
    hash_password,
    require_admin,
    require_approver,
    require_employee,
    verify_password,
)
from app.db import get_db
from app.models import Category, CategoryApprover, Claim, Role, Status, User, UserRole
from app.schemas import ClaimIn, LoginIn, RegisterIn, RejectIn, RolesIn, RoutingIn

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Expense Approval")


def now() -> datetime:
    return datetime.now(timezone.utc)


# --- перетворення у відповідь ----------------------------------------------

def user_out(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "roles": sorted(role.value for role in user.roles),
    }


def claim_out(claim: Claim) -> dict:
    return {
        "id": claim.id,
        "amount_usd": float(claim.amount_usd),
        "category": claim.category.value,
        "description": claim.description,
        "expense_date": claim.expense_date.isoformat(),
        "payment_details": claim.payment_details,
        "status": claim.status.value,
        "decision_comment": claim.decision_comment,
        "decided_at": claim.decided_at.isoformat() if claim.decided_at else None,
        "created_at": claim.created_at.isoformat(),
        "employee_name": claim.employee.name,
        "approver_name": claim.approver.name,
    }


# --- доступ до заявки -------------------------------------------------------

def load_visible_claim(claim_id: int, user: User, db: DbSession) -> Claim:
    """Заявку бачать рівно двоє: її автор і призначений погоджувач.

    Стороннім віддаємо 404, а не 403 — щоб не підтверджувати існування заявки.
    """
    claim = db.get(Claim, claim_id)
    if claim is None or user.id not in (claim.employee_id, claim.approver_id):
        raise HTTPException(status_code=404, detail="Заявку не знайдено")
    return claim


def pick_approver(category: Category, author: User, db: DbSession) -> int:
    """Маршрутизація: категорія -> погоджувач.

    Один нюанс: якщо погоджувач сам подав заявку у «свою» категорію, вона йде
    до іншого погоджувача. Сам себе ніхто не погоджує.
    """
    routing = db.get(CategoryApprover, category)
    if routing is None:
        raise HTTPException(
            status_code=409,
            detail=f"Для категорії «{category.value}» ще не призначено погоджувача",
        )

    if routing.approver_id != author.id:
        return routing.approver_id

    substitute = db.scalar(
        select(User.id)
        .join(UserRole)
        .where(UserRole.role == Role.approver, User.id != author.id)
        .order_by(User.id)
        .limit(1)
    )
    if substitute is None:
        raise HTTPException(
            status_code=409,
            detail="Ви єдиний погоджувач цієї категорії — потрібен ще один погоджувач",
        )
    return substitute


# --- реєстрація та вхід -----------------------------------------------------

@app.post("/api/register", status_code=201)
def register(data: RegisterIn, response: Response, db: DbSession = Depends(get_db)):
    """Реєстрація. Нова людина — завжди співробітник; решту ролей дає адміністратор."""
    user = User(
        email=data.email.lower(),
        name=data.name,
        password_hash=hash_password(data.password),
        role_rows=[UserRole(role=Role.employee)],
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Користувач із таким email уже існує")

    token = create_session(db, user)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", path="/")
    return user_out(user)


@app.post("/api/login")
def login(data: LoginIn, response: Response, db: DbSession = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Невірний email або пароль")

    token = create_session(db, user)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", path="/")
    return user_out(user)


@app.post("/api/logout")
def logout(
    response: Response,
    session: str | None = Cookie(default=None),
    db: DbSession = Depends(get_db),
):
    if session:
        drop_session(db, session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


@app.get("/api/categories")
def categories(db: DbSession = Depends(get_db)):
    """Категорії та хто їх погоджує — щоб заявник бачив, куди піде заявка."""
    routing = {row.category: row.approver.name for row in db.scalars(select(CategoryApprover))}
    return [
        {"category": c.value, "approver": routing.get(c)}  # None = погоджувача ще немає
        for c in Category
    ]


# --- заявник ----------------------------------------------------------------

@app.post("/api/claims", status_code=201)
def create_claim(
    data: ClaimIn,
    user: User = Depends(require_employee),
    db: DbSession = Depends(get_db),
):
    claim = Claim(
        employee_id=user.id,
        approver_id=pick_approver(data.category, user, db),
        amount_usd=data.amount_usd,
        category=data.category,
        description=data.description.strip(),
        expense_date=data.expense_date,
        payment_details=data.payment_details.strip(),
        status=Status.pending,
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return claim_out(claim)


@app.get("/api/claims/mine")
def my_claims(user: User = Depends(current_user), db: DbSession = Depends(get_db)):
    claims = db.scalars(
        select(Claim).where(Claim.employee_id == user.id).order_by(Claim.id.desc())
    ).unique()
    return [claim_out(c) for c in claims]


@app.post("/api/claims/{claim_id}/withdraw")
def withdraw(claim_id: int, user: User = Depends(current_user), db: DbSession = Depends(get_db)):
    claim = load_visible_claim(claim_id, user, db)

    if claim.employee_id != user.id:
        raise HTTPException(status_code=403, detail="Відкликати заявку може лише її автор")
    if claim.status is not Status.pending:
        raise HTTPException(status_code=409, detail="Заявку вже розглянуто — відкликати не можна")

    claim.status = Status.withdrawn
    claim.decided_at = now()
    db.commit()
    db.refresh(claim)
    return claim_out(claim)


# --- погоджувач -------------------------------------------------------------

@app.get("/api/claims/queue")
def queue(user: User = Depends(require_approver), db: DbSession = Depends(get_db)):
    """Черга саме цього погоджувача: спочатку те, що чекає рішення."""
    claims = db.scalars(
        select(Claim)
        .where(Claim.approver_id == user.id)
        .order_by((Claim.status != Status.pending), Claim.id.desc())
    ).unique()
    return [claim_out(c) for c in claims]


@app.get("/api/claims/{claim_id}")
def claim_detail(claim_id: int, user: User = Depends(current_user), db: DbSession = Depends(get_db)):
    return claim_out(load_visible_claim(claim_id, user, db))


def decide(claim_id: int, user: User, db: DbSession, status: Status, comment: str | None) -> dict:
    claim = load_visible_claim(claim_id, user, db)

    if claim.approver_id != user.id:
        raise HTTPException(status_code=403, detail="Ця заявка направлена не вам")
    if claim.status is not Status.pending:
        raise HTTPException(status_code=409, detail=f"Заявка вже має статус «{claim.status.value}»")

    # Блокуємо рядок заявки, щоб два одночасні рішення не розійшлися.
    # Беремо лише один стовпець: із JOIN-ами (lazy="joined") FOR UPDATE не працює.
    locked_status = db.scalar(select(Claim.status).where(Claim.id == claim_id).with_for_update())
    if locked_status is not Status.pending:
        raise HTTPException(status_code=409, detail="Заявку щойно вже розглянули")

    claim.status = status
    claim.decision_comment = comment
    claim.decided_at = now()
    db.commit()
    db.refresh(claim)
    return claim_out(claim)


@app.post("/api/claims/{claim_id}/approve")
def approve(claim_id: int, user: User = Depends(require_approver), db: DbSession = Depends(get_db)):
    return decide(claim_id, user, db, Status.approved, None)


@app.post("/api/claims/{claim_id}/reject")
def reject(
    claim_id: int,
    data: RejectIn,
    user: User = Depends(require_approver),
    db: DbSession = Depends(get_db),
):
    # Коментар уже перевірено схемою RejectIn — без нього запит сюди не дійде.
    return decide(claim_id, user, db, Status.rejected, data.comment)


@app.get("/api/claims/{claim_id}/ai-review")
async def ai_review(
    claim_id: int,
    user: User = Depends(require_approver),
    db: DbSession = Depends(get_db),
):
    """Підказка для погоджувача. Завжди 200 — навіть коли AI недоступний."""
    claim = load_visible_claim(claim_id, user, db)
    if claim.approver_id != user.id:
        raise HTTPException(status_code=403, detail="Ця заявка направлена не вам")
    return await ai.review(claim, db)


# --- адміністратор ----------------------------------------------------------

@app.get("/api/admin/users")
def admin_users(_: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.id)).unique()
    return [user_out(u) for u in users]


@app.put("/api/admin/users/{user_id}/roles")
def admin_set_roles(
    user_id: int,
    data: RolesIn,
    admin: User = Depends(require_admin),
    db: DbSession = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Користувача не знайдено")

    wanted = set(data.roles)

    if user.id == admin.id and Role.admin not in wanted:
        raise HTTPException(status_code=409, detail="Не можна зняти адміністратора із себе")

    if Role.approver not in wanted:
        assigned = db.scalars(
            select(CategoryApprover.category).where(CategoryApprover.approver_id == user.id)
        ).all()
        if assigned:
            names = ", ".join(c.value for c in assigned)
            raise HTTPException(
                status_code=409,
                detail=f"Спершу передайте комусь категорії: {names}",
            )

    user.role_rows = [UserRole(user_id=user.id, role=role) for role in sorted(wanted, key=lambda r: r.value)]
    db.commit()
    db.refresh(user)
    return user_out(user)


@app.get("/api/admin/routing")
def admin_routing(_: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    routing = {row.category: row.approver for row in db.scalars(select(CategoryApprover))}
    approvers = db.scalars(
        select(User).join(UserRole).where(UserRole.role == Role.approver).order_by(User.name)
    ).unique()
    return {
        "categories": [
            {
                "category": c.value,
                "approver_id": routing[c].id if c in routing else None,
                "approver_name": routing[c].name if c in routing else None,
            }
            for c in Category
        ],
        "approvers": [{"id": u.id, "name": u.name, "email": u.email} for u in approvers],
    }


@app.put("/api/admin/routing/{category}")
def admin_set_routing(
    category: Category,
    data: RoutingIn,
    _: User = Depends(require_admin),
    db: DbSession = Depends(get_db),
):
    approver = db.get(User, data.approver_id)
    if approver is None:
        raise HTTPException(status_code=404, detail="Користувача не знайдено")
    if not approver.has(Role.approver):
        raise HTTPException(status_code=409, detail=f"{approver.name} не має ролі погоджувача")

    routing = db.get(CategoryApprover, category)
    if routing is None:
        db.add(CategoryApprover(category=category, approver_id=approver.id))
    else:
        routing.approver_id = approver.id
    db.commit()

    return {"category": category.value, "approver_id": approver.id, "approver_name": approver.name}


# --- фронтенд ---------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
