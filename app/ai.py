"""AI-підказка для погоджувача.

Два правила, які тут важливіші за сам промпт:
  1. AI нічого не вирішує — лише описує заявку і піднімає прапорець сумніву.
  2. Якщо OpenAI недоступний, повільний або без ключа — погоджувач цього
     майже не помічає: він просто не бачить блок підказки і працює далі.
"""

import asyncio
import json
import logging
import os
from openai import AsyncOpenAI

from sqlalchemy.orm import Session as DbSession

from app.models import AiReview, Claim

log = logging.getLogger("expense.ai")

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TIMEOUT_SECONDS = float(os.getenv("AI_TIMEOUT", "8"))

SYSTEM_PROMPT = """You are an assistant to an expense approver at a small company.

<task>
You receive ONE expense claim. Produce two things:
1. `summary` — 1-2 plain sentences saying what the expense is.
2. `flagged` — whether the amount, category and description clearly contradict
   each other, plus `flag_reason` explaining the contradiction.
</task>

<categories>
What each category normally covers:
- Office — supplies, furniture, equipment, small items for the workplace:
  paper, cartridges, lamps, cables, chairs, coffee and tea for the kitchen.
- Travel — getting to and staying somewhere for work: flights, trains, taxis,
  hotels, fuel.
- Client Entertainment — hosting clients: restaurant bills, gifts, event tickets.
- Software/Subscriptions — software licences and recurring online services.
- Other — anything that genuinely fits none of the above.
</categories>

<how_to_decide_the_flag>
Default to `flagged: false`. A claim is NOT a problem just because it is
unusual, expensive, or briefly described.

Set `flagged: true` ONLY when a reasonable approver would immediately see a
contradiction, such as:
- the description clearly belongs to a different category on the list above;
- the amount is wildly out of proportion to what is described.

Before flagging, ask yourself: "could this description plausibly belong to this
category?" If yes — do not flag.
</how_to_decide_the_flag>

<rules>
- Never decide the claim. Do not recommend approving or rejecting it.
- Do not invent facts that are not in the claim.
- LANGUAGE: write `summary` and `flag_reason` in the SAME language as the text
  inside <description>. If the description is in Ukrainian, answer in Ukrainian.
  Never translate the answer into English. This instruction is in English only
  because it is addressed to you, not to the reader of your answer.
</rules>

<examples>
These are written in English only to show the reasoning. They say nothing about
the language of your answer — that always follows the description.

- category "Office", description "flight ticket to London for a conference",
  $480 -> flagged: true. A flight belongs to Travel, not Office.
- category "Office", description "printer paper", $4500 -> flagged: true.
  The amount is wildly out of proportion to printer paper.
- category "Office", description "two desk lamps and an extension cord", $89
  -> flagged: false. Lamps and cables are ordinary office items.
- category "Travel", description "train ticket Kyiv-Lviv, return", $210
  -> flagged: false. Exactly what Travel is for.
- category "Other", description "taxi from the airport after a work trip", $35
  -> flagged: false. Travel would fit better, but "Other" is not a contradiction.
</examples>

<output_format>
Return JSON only, with exactly these keys:
{
  "summary": "1-2 sentences describing the expense, in the description's language",
  "flagged": true | false,
  "flag_reason": "one sentence naming the contradiction in the description's language, or an empty string when flagged is false"
}
</output_format>"""


def _claim_as_text(claim: Claim) -> str:
    """Claim fields in a fixed order, so the model always sees the same shape."""
    return (
        f"<claim>\n"
        f"  <amount_usd>{claim.amount_usd:.2f}</amount_usd>\n"
        f"  <category>{claim.category.value}</category>\n"
        f"  <expense_date>{claim.expense_date.isoformat()}</expense_date>\n"
        f"  <description>{claim.description}</description>\n"
        f"  <payment_details>{claim.payment_details}</payment_details>\n"
        f"</claim>"
    )


async def _ask_openai(claim: Claim) -> dict:  
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=TIMEOUT_SECONDS)
    response = await client.chat.completions.create(
        model=MODEL,
        temperature=0.2,
        max_tokens=300,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _claim_as_text(claim)},
        ],
    )
    data = json.loads(response.choices[0].message.content)
    return {
        "summary": str(data.get("summary", "")).strip(),
        "flagged": bool(data.get("flagged", False)),
        "flag_reason": str(data.get("flag_reason", "")).strip(),
    }


def _cached(claim: Claim) -> dict | None:
    row = claim.ai_review
    if row is None:
        return None
    return {
        "available": True,
        "summary": row.summary,
        "flagged": row.flagged,
        "flag_reason": row.flag_reason or "",
    }


def _save(claim: Claim, result: dict, db: DbSession) -> None:
    db.merge(
        AiReview(
            claim_id=claim.id,
            summary=result["summary"],
            flagged=result["flagged"],
            flag_reason=result["flag_reason"],
        )
    )
    db.commit()


def _unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason, "summary": None, "flagged": False, "flag_reason": ""}


async def review(claim: Claim, db: DbSession) -> dict:
    """Повертає AI-висновок по заявці. Ніколи не кидає виняток."""
    cached = _cached(claim)
    if cached:
        return cached

    if not os.getenv("OPENAI_API_KEY"):
        return _unavailable("AI-підказка вимкнена: не налаштовано OPENAI_API_KEY")

    try:
        result = await asyncio.wait_for(_ask_openai(claim), timeout=TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        log.warning("AI review timed out for claim %s", claim.id)
        return _unavailable("AI-сервіс не відповів вчасно")
    except Exception as exc:  # мережа, ліміти, зміна формату відповіді — байдуже
        log.warning("AI review failed for claim %s: %s", claim.id, exc)
        return _unavailable("AI-сервіс зараз недоступний")

    _save(claim, result, db)
    return {"available": True, **result}
