"""Every AI coach answer is kept (CoachAnswer) so its quality can be reviewed and models compared.

The coach is admin-only while it's tested; the review page reads what's recorded here.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from models import CoachAnswer
from utils.logger import logger

MAX_COMMENT_CHARS = 1000
RATINGS = (-1, 1)


def record(db_session, user, result: Dict[str, Any]) -> Optional[int]:
    """Store an answered question; returns its id, or None if there was nothing to keep."""
    usage = result.get("usage") or {}
    if not result.get("ok") or not usage.get("question"):
        return None
    try:
        row = CoachAnswer(
            user_id=user.id,
            question=str(usage["question"]),
            answer=str(result.get("answer") or ""),
            source=str(result.get("source") or "ai")[:8],
            model=(str(result["model"])[:80] if result.get("model") else None),
            steps=[
                {"tool": s.get("tool"), "args": s.get("args") or {}, "ok": s.get("ok", True)}
                for s in (usage.get("steps") or [])
            ],
            calls=usage.get("calls"),
            tokens=usage.get("tokens"),
            ms=usage.get("ms"),
            failed=(str(usage.get("failed") or "")[:300] or None),
        )
        db_session.add(row)
        db_session.commit()
        return row.id
    except Exception as e:  # recording must never cost the user their answer
        db_session.rollback()
        logger.warning(f"Could not record coach answer: {e}")
        return None


def rate(db_session, user, answer_id: Any, rating: Any, comment: Any = None) -> bool:
    """Set (or clear, with rating 0) the user's rating on one of their own answers."""
    try:
        answer_id, rating = int(answer_id), int(rating)
    except (TypeError, ValueError):
        return False
    if rating not in RATINGS + (0,):
        return False
    row = db_session.query(CoachAnswer).filter_by(id=answer_id, user_id=user.id).first()
    if not row:
        return False
    row.rating = rating or None
    if comment is not None:
        row.comment = str(comment).strip()[:MAX_COMMENT_CHARS] or None
    if not row.rating:
        row.comment = None
    db_session.commit()
    return True


def model_summary(db_session) -> List[Dict[str, Any]]:
    """Per model: answers, ratings, and typical time and tokens. Best-rated first."""
    by_model: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"answers": 0, "up": 0, "down": 0, "ms": [], "tokens": []})
    for model, source, rating, ms, tokens in db_session.query(
        CoachAnswer.model, CoachAnswer.source, CoachAnswer.rating, CoachAnswer.ms, CoachAnswer.tokens
    ).all():
        s = by_model[model if source == "ai" and model else "Built-in answer"]
        s["answers"] += 1
        s["up"] += rating == 1
        s["down"] += rating == -1
        if ms:
            s["ms"].append(ms)
        if tokens:
            s["tokens"].append(tokens)
    out = []
    for name, s in by_model.items():
        rated = s["up"] + s["down"]
        out.append({
            "model": name,
            "answers": s["answers"],
            "up": s["up"],
            "down": s["down"],
            "helpful_pct": round(100 * s["up"] / rated) if rated else None,
            "median_s": round(_median(s["ms"]) / 1000, 1) if s["ms"] else None,
            "median_tokens": int(_median(s["tokens"])) if s["tokens"] else None,
        })
    return sorted(out, key=lambda r: (r["helpful_pct"] is None, -(r["helpful_pct"] or 0), -r["answers"]))


def recent(db_session, *, show: str = "all", limit: int = 50) -> List[CoachAnswer]:
    q = db_session.query(CoachAnswer)
    if show == "down":
        q = q.filter(CoachAnswer.rating == -1)
    elif show == "up":
        q = q.filter(CoachAnswer.rating == 1)
    elif show == "unrated":
        q = q.filter(CoachAnswer.rating.is_(None))
    return q.order_by(CoachAnswer.created_at.desc(), CoachAnswer.id.desc()).limit(limit).all()


def _median(values: List[float]) -> float:
    values = sorted(values)
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
