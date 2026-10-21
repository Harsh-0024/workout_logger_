"""The admin-only AI coach: a Gemini conversation that answers from the user's own logs.

The model never sees the database. It gets a set of read-only tools (services.coach_tools),
asks for what it needs, and writes its answer from the results. When there is no key or Gemini
is down, questions the rule-based assistant (services.history_qa) knows still get answered.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import requests

from config import Config
from services import coach_tools
from services.history_qa import answer_history_question
from utils.dates import utc_now
from utils.logger import logger

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
MODEL_CANDIDATES = ("gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash")
MAX_TOOL_ROUNDS = 6
MAX_HISTORY_TURNS = 12
MAX_MESSAGE_CHARS = 1500
REQUEST_TIMEOUT_S = 30

# Per-user request budget, in-process like the auth rate limits.
RATE_LIMIT = 30
RATE_WINDOW_S = 600
_rate_lock = threading.Lock()
_rate_hits: Dict[int, Deque[float]] = {}

# A model that 404s for a key (retired, or not on that key's tier) is skipped for a while.
_bad_models: Dict[str, float] = {}
_BAD_MODEL_TTL_S = 3600

TOOL_LABELS = {
    "list_exercises": "Exercise list",
    "get_exercise_history": "Exercise history",
    "get_personal_records": "Personal records",
    "get_workouts": "Workouts",
    "get_training_summary": "Training summary",
    "get_progress_trend": "Progress trend",
    "compare_periods": "Period comparison",
    "get_plan": "Workout plan",
}


class CoachUnavailable(Exception):
    pass


def allow_request(user_id: int, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    with _rate_lock:
        hits = _rate_hits.setdefault(int(user_id), deque())
        while hits and now - hits[0] > RATE_WINDOW_S:
            hits.popleft()
        if len(hits) >= RATE_LIMIT:
            return False
        hits.append(now)
        return True


def system_prompt(user, context: Optional[Dict[str, Any]] = None) -> str:
    today = utc_now().date()
    name = (getattr(user, "full_name", None) or getattr(user, "username", None) or "the lifter").split(" ")[0]
    bodyweight = getattr(user, "bodyweight", None)
    lines = [
        "You are Coach, a sharp, friendly strength coach built into a workout logging app.",
        f"You are talking with {name}. Today is {today.isoformat()} ({today.strftime('%A')}). Weights are in kg.",
        f"Their bodyweight is {bodyweight:g} kg." if bodyweight else "",
        "",
        "How to work:",
        "- Answer from their real data. Call tools to look things up; never guess or invent numbers, dates or lifts.",
        "- Call several tools if needed (e.g. summary + trend) before answering. Resolve vague exercise names with list_exercises.",
        "- If a tool returns an error, fix the arguments or tell the user plainly what's missing.",
        "- e1RM means estimated one-rep max (from the logged weight and reps).",
        "",
        "How to answer:",
        "- Lead with the answer in one sentence, then supporting numbers. Keep it under ~150 words unless asked for detail.",
        "- Use short bullet points ('- ') and **bold** for key numbers. No tables, no headings.",
        "- Write dates as YYYY-MM-DD so the app can link them to the workout.",
        "- When it helps, end with one concrete, specific next step (e.g. a load/rep target for next session).",
        "- Training advice should be evidence-based and conservative; for pain or injury, suggest seeing a professional.",
        "- Stay on training, nutrition-for-training and recovery. Politely decline unrelated requests.",
    ]
    ctx = context or {}
    page_bits = []
    if ctx.get("page"):
        page_bits.append(f"page '{ctx['page']}'")
    if ctx.get("exercise"):
        page_bits.append(f"exercise '{ctx['exercise']}'")
    if ctx.get("metric"):
        page_bits.append(f"chart metric '{ctx['metric']}'")
    if ctx.get("date"):
        page_bits.append(f"selected date {ctx['date']}")
    if page_bits:
        lines += ["", "The user is currently looking at " + ", ".join(page_bits) +
                  ". 'This exercise', 'this day' or 'the graph' refer to that."]
    return "\n".join(l for l in lines if l is not None)


def _clean_history(messages: Any) -> List[Dict[str, Any]]:
    """Client-sent turns -> Gemini contents. Only text survives; the last turn must be the user's."""
    out: List[Dict[str, Any]] = []
    if not isinstance(messages, list):
        return out
    for m in messages[-MAX_HISTORY_TURNS:]:
        if not isinstance(m, dict):
            continue
        role = "model" if m.get("role") in ("assistant", "model") else "user"
        text = str(m.get("text") or "").strip()[:MAX_MESSAGE_CHARS]
        if not text:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["parts"][0]["text"] += "\n\n" + text
        else:
            out.append({"role": role, "parts": [{"text": text}]})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def _keys(api_keys: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    keys = [k for k in (api_keys or []) if isinstance(k, dict) and k.get("api_key")]
    env_key = getattr(Config, "GEMINI_API_KEY", None)
    if env_key:
        keys.append({"id": None, "api_key": env_key, "source": "env"})
    return keys[:3]


def _models() -> List[str]:
    preferred = str(getattr(Config, "GEMINI_COACH_MODEL", "") or "").strip()
    models = [preferred] if preferred else []
    return models + [m for m in MODEL_CANDIDATES if m != preferred]


def _post(api_key: str, model: str, payload: dict) -> dict:
    resp = requests.post(
        f"{BASE_URL}/models/{model}:generateContent",
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=REQUEST_TIMEOUT_S,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else {}


class _Transport:
    """Sticks with the first key/model that works for the rest of the conversation turn."""

    def __init__(self, keys: List[Dict[str, Any]]):
        self.keys = keys
        self.chosen: Optional[tuple] = None

    def generate(self, payload: dict) -> tuple[dict, Dict[str, Any], str]:
        if self.chosen:
            key, model = self.chosen
            return _post(key["api_key"], model, payload), key, model
        last_error: Optional[Exception] = None
        for key in self.keys:
            for model in _models():
                bad_key = f"{key.get('id') or key.get('source')}:{model}"
                if _bad_models.get(bad_key, 0) > time.time():
                    continue
                try:
                    data = _post(key["api_key"], model, payload)
                    self.chosen = (key, model)
                    return data, key, model
                except requests.HTTPError as e:
                    last_error = e
                    status = e.response.status_code if e.response is not None else None
                    if status == 404:
                        _bad_models[bad_key] = time.time() + _BAD_MODEL_TTL_S
                        continue
                    if status in (401, 403):
                        break  # this key is bad; try the next key
                    if status == 429 or (status and status >= 500):
                        continue
                    break
                except requests.RequestException as e:
                    last_error = e
                    continue
        raise CoachUnavailable(type(last_error).__name__ if last_error else "no usable key")


def _candidate_parts(data: dict) -> List[Dict[str, Any]]:
    try:
        cand = (data.get("candidates") or [])[0]
        return list((cand.get("content") or {}).get("parts") or [])
    except (AttributeError, IndexError, TypeError):
        return []


def _finish_reason(data: dict) -> str:
    try:
        return str((data.get("candidates") or [])[0].get("finishReason") or "")
    except (AttributeError, IndexError, TypeError):
        return ""


_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def _links_for(text: str) -> List[Dict[str, str]]:
    seen, links = set(), []
    for d in _DATE_RE.findall(text or ""):
        if d not in seen:
            seen.add(d)
            links.append({"label": d, "url": f"/workout/{d}"})
    return links[:6]


def run_coach(
    db_session,
    user,
    *,
    messages: List[Dict[str, Any]],
    api_keys: Optional[List[Dict[str, Any]]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    contents = _clean_history(messages)
    if not contents or contents[-1]["role"] != "user":
        return {"ok": False, "error": "Please enter a question."}
    question = contents[-1]["parts"][0]["text"]
    ctx = context or {}

    keys = _keys(api_keys)
    if not keys:
        return _rules_answer(db_session, user, question, ctx, note="No Gemini key is set up, so only built-in questions work. "
                                                                    "Add one in Settings → Integrations.")

    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt(user, ctx)}]},
        "contents": contents,
        "tools": [{"functionDeclarations": coach_tools.function_declarations()}],
        "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
    }
    transport = _Transport(keys)
    steps: List[Dict[str, Any]] = []
    try:
        for _ in range(MAX_TOOL_ROUNDS + 1):
            data, key, model = transport.generate(payload)
            parts = _candidate_parts(data)
            calls = [p["functionCall"] for p in parts if isinstance(p, dict) and isinstance(p.get("functionCall"), dict)]
            if not calls:
                text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")).strip()
                if not text:
                    reason = _finish_reason(data)
                    raise CoachUnavailable(f"empty answer ({reason or 'no reason'})")
                return {
                    "ok": True,
                    "answer": text,
                    "steps": steps,
                    "links": _links_for(text),
                    "model": model,
                    "key_id": key.get("id"),
                    "source": "ai",
                }
            if len(steps) >= MAX_TOOL_ROUNDS * 4:
                break
            # Echo the model's turn back unchanged (it may carry thought signatures), then the results.
            payload["contents"].append({"role": "model", "parts": parts})
            responses = []
            for call in calls[:6]:
                name = str(call.get("name") or "")
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                started = time.perf_counter()
                result = coach_tools.run_tool(db_session, user, name, args)
                steps.append({
                    "tool": name,
                    "label": TOOL_LABELS.get(name, name),
                    "args": {k: v for k, v in args.items() if v not in (None, "")},
                    "ok": "error" not in result,
                    "ms": int((time.perf_counter() - started) * 1000),
                })
                responses.append({"functionResponse": {"name": name, "response": _compact(result)}})
            payload["contents"].append({"role": "user", "parts": responses})
        raise CoachUnavailable("too many tool rounds")
    except CoachUnavailable as e:
        logger.warning(f"AI coach unavailable: {e}")
        return _rules_answer(db_session, user, question, ctx, note="The AI is unavailable right now, so this is the built-in answer.")
    except Exception as e:
        logger.error(f"AI coach failed: {type(e).__name__}: {e}", exc_info=True)
        return _rules_answer(db_session, user, question, ctx, note="The AI hit an error, so this is the built-in answer.")


def _compact(result: Dict[str, Any], limit: int = 12000) -> Dict[str, Any]:
    text = json.dumps(result, default=str, ensure_ascii=False)
    if len(text) <= limit:
        return json.loads(text)
    return {"truncated": True, "partial_json": text[:limit]}


def _rules_answer(db_session, user, question: str, ctx: Dict[str, Any], *, note: str) -> Dict[str, Any]:
    exercise_options = coach_tools._logged_names(db_session, user)
    result = answer_history_question(
        db_session,
        user,
        question=question,
        context_exercise=ctx.get("exercise") or None,
        context_metric=ctx.get("metric") or None,
        context_date=ctx.get("date") or None,
        api_keys=None,
        exercise_options=exercise_options,
        use_ai=False,
    )
    if not result.get("ok"):
        return {"ok": False, "error": f"{note} {result.get('error') or ''}".strip(), "source": "rules"}
    lines = [str(result.get("answer") or "")]
    for r in (result.get("results") or [])[:6]:
        bits = [r.get("exercise") or r.get("date") or ""]
        if r.get("top_weight") is not None and r.get("top_reps") is not None:
            bits.append(f"{r['top_weight']:g} x {r['top_reps']}")
        if r.get("estimated_1rm"):
            bits.append(f"e1RM {float(r['estimated_1rm']):.1f}")
        lines.append("- " + " — ".join(b for b in bits if b))
    links = [{"label": "Open workout", "url": result["workout_url"]}] if result.get("workout_url") else []
    return {"ok": True, "answer": "\n".join(lines), "steps": [], "links": links, "source": "rules", "note": note}


def suggestions(db_session, user) -> List[str]:
    """Starter questions built from what this user actually trains."""
    top = coach_tools.list_exercises(db_session, user, limit=3).get("exercises") or []
    out = ["How has my training gone over the last 4 weeks?"]
    if top:
        out.append(f"How is my {top[0]['exercise']} progressing?")
    out.append("Which muscle groups am I neglecting?")
    if len(top) > 1:
        out.append(f"What should I aim for next time on {top[1]['exercise']}?")
    out += ["What are my strongest lifts?", "What should I train today?"]
    return out[:6]
