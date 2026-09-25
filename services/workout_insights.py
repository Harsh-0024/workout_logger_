"""
Per-exercise insights for the workout detail page.

Everything is scored with the same estimators as the medals (Epley e1RM for
strength, weight * sqrt(seconds) for timed) so the numbers on the page never
contradict the badges.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

from services.best_scoring import best_workout_strength_score, best_workout_timed_score
from services.helpers import timed_set_score
from services.workout_quality import WorkoutQualityScorer

# Trend: today vs the average of this many previous sessions.
TREND_WINDOW = 3
# Changes inside +/- this band read as "Steady" (symmetric, so it hides noise
# on both sides equally). One extra rep is ~2%, so real gains still register.
STEADY_BAND_PCT = 1.0
SPARKLINE_SESSIONS = 8

MEDAL_KEYS = {
    "gold_strength", "silver_strength", "bronze_strength",
    "gold_load", "silver_load", "bronze_load",
}
BELOW_BEST_KEYS = {"slightly_off", "moderately_off", "significantly_off"}
_MEDAL_EMOJI = {"gold": "🥇", "silver": "🥈", "bronze": "🥉"}
_RANK_NAMES = ["strongest set", "2nd set", "3rd set"]


def _num(value: float) -> str:
    return f"{float(value):g}"


def format_pct(value: Optional[float], *, signed: bool = True) -> str:
    """'+4.8%', '-12%', '+0.4%'. One decimal below 10%, whole numbers above."""
    if value is None:
        return ""
    magnitude = abs(float(value))
    text = f"{magnitude:.0f}" if magnitude >= 10 else f"{magnitude:.1f}".rstrip("0").rstrip(".")
    if not signed:
        return f"{text}%"
    sign = "+" if value > 0 else ("−" if value < 0 else "")
    return f"{sign}{text}%"


def format_set(weight: float, reps: int, *, uses_bodyweight: bool, is_timed: bool) -> str:
    if uses_bodyweight:
        offset = float(weight)
        load = "BW" if offset == 0 else (f"BW+{_num(offset)}" if offset > 0 else f"BW−{_num(-offset)}")
    else:
        load = _num(weight)
    return f"{load}×{int(reps)}s" if is_timed else f"{load}×{int(reps)}"


def _pairs(sets_json: Optional[Dict]) -> List[tuple]:
    if not sets_json or not isinstance(sets_json, dict):
        return []
    out = []
    for weight, reps in zip(sets_json.get("weights") or [], sets_json.get("reps") or []):
        try:
            out.append((float(weight), int(reps)))
        except (TypeError, ValueError):
            continue
    return out


def format_sets_line(sets_json: Optional[Dict], *, uses_bodyweight: bool, is_timed: bool) -> str:
    """Sets in the order they were logged: '50×14 · 50×13 · 43.6×13'."""
    return " · ".join(
        format_set(w, r, uses_bodyweight=uses_bodyweight, is_timed=is_timed)
        for w, r in _pairs(sets_json)
        if r > 0
    )


def readable_set(weight: float, reps: int, *, uses_bodyweight: bool, is_timed: bool) -> str:
    """
    Newcomer-friendly set text for sharing: '35 kg × 8', 'Bodyweight × 15',
    'Bodyweight + 2.5 kg × 20', '30 kg × 45 s'. No app shorthand.
    """
    offset = float(weight)
    if uses_bodyweight:
        if offset == 0:
            load = "Bodyweight"
        elif offset > 0:
            load = f"Bodyweight + {_num(offset)} kg"
        else:
            load = f"Bodyweight − {_num(-offset)} kg"
    else:
        load = f"{_num(offset)} kg"
    return f"{load} × {int(reps)} s" if is_timed else f"{load} × {int(reps)}"


_TARGET_RANGE_RE = re.compile(r"(\d+)\s*[–-]\s*(\d+)\s*(s)?", re.IGNORECASE)


def target_range(exercise_string: str, *, is_timed: bool = False) -> str:
    """The rep/time range from '[2, 8–12]' or '[30–60s]' as '8–12 reps' / '30–60 s'."""
    first_line = str(exercise_string or "").strip().splitlines()[0] if str(exercise_string or "").strip() else ""
    bracket = re.search(r"\[([^\]]*)\]", first_line)
    if not bracket:
        return ""
    match = _TARGET_RANGE_RE.search(bracket.group(1))
    if not match:
        return ""
    low, high, seconds = match.groups()
    unit = "s" if (seconds or is_timed) else "reps"
    return f"{low}–{high} {unit}"


def readable_workout_text(title: str, date_label: str, exercises: Sequence[Dict[str, Any]], footer: str = "") -> str:
    """
    Plain text a friend can read (or copy into notes). `exercises`:
    [{"name": str, "target": str, "sets": [str, ...]}] with sets in the order they were done.
    """
    lines = [title, date_label, ""]
    for exercise in exercises:
        header = exercise["name"]
        if exercise.get("target"):
            header += f" ({exercise['target']})"
        lines.append(header)
        lines.append(", ".join(exercise.get("sets") or []) or "—")
        lines.append("")
    if footer:
        lines.append(footer)
    return "\n".join(lines).strip()


def _set_score(weight: float, reps: int, is_timed: bool) -> float:
    if is_timed:
        return float(timed_set_score(weight, reps) or 0.0)
    return float(WorkoutQualityScorer.estimate_1rm(weight, reps) or 0.0)


def ranked_sets(
    logged_sets: Optional[Dict],
    effective_sets: Optional[Dict],
    *,
    uses_bodyweight: bool,
    is_timed: bool,
) -> List[Dict[str, Any]]:
    """
    Sets ordered strongest first. Scored on effective loads (bodyweight added),
    labelled with what the user logged. Order-independent, like the medals.
    """
    logged = _pairs(logged_sets)
    effective = _pairs(effective_sets)
    rows = []
    for idx, (eff_w, eff_r) in enumerate(effective):
        score = _set_score(eff_w, eff_r, is_timed)
        if score <= 0:
            continue
        raw_w, raw_r = logged[idx] if idx < len(logged) else (eff_w, eff_r)
        rows.append({
            "score": score,
            "effective_weight": eff_w,
            "label": format_set(raw_w, raw_r, uses_bodyweight=uses_bodyweight, is_timed=is_timed),
        })
    rows.sort(key=lambda row: (row["score"], row["effective_weight"]), reverse=True)
    return rows


def session_score(effective_sets: Optional[Dict], *, top_n: int, is_timed: bool) -> float:
    """Whole-exercise score: strongest set + 25% credit for the next-best sets."""
    if is_timed:
        return float(best_workout_timed_score(effective_sets or {}, top_n=top_n).get("score") or 0.0)
    return float(best_workout_strength_score(effective_sets or {}, top_n=top_n).get("score") or 0.0)


def pct_change(current: Optional[float], reference: Optional[float]) -> Optional[float]:
    if not current or not reference or reference <= 0:
        return None
    return (float(current) - float(reference)) / float(reference) * 100.0


def _delta_cell(mine: Optional[Dict], theirs: Optional[Dict]) -> Optional[Dict[str, Any]]:
    if theirs is None:
        return None
    pct = pct_change(mine["score"], theirs["score"]) if mine else None
    if pct is None:
        text = ""
    elif abs(pct) < 0.05:
        text = "same"
    else:
        text = format_pct(pct)
    return {"label": theirs["label"], "pct": pct, "pct_text": text, "direction": _direction(pct, band=0.05)}


def compare_columns(
    today: Sequence[Dict],
    last: Optional[Sequence[Dict]] = None,
    best: Optional[Sequence[Dict]] = None,
) -> List[Dict[str, Any]]:
    """
    One table: today's sets strongest first, with last session and best side by side.
    Sets are matched by strength rank (strongest vs strongest), never by order done.
    """
    last = list(last or [])
    best = list(best or [])
    rows = []
    for idx in range(max(len(today), len(last), len(best))):
        mine = today[idx] if idx < len(today) else None
        rows.append({
            "rank": _ordinal(idx + 1),
            "today": mine["label"] if mine else "—",
            "last": _delta_cell(mine, last[idx]) if idx < len(last) else ({"label": "—"} if last else None),
            "best": _delta_cell(mine, best[idx]) if idx < len(best) else ({"label": "—"} if best else None),
        })
    return rows


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _direction(pct: Optional[float], *, band: float = STEADY_BAND_PCT) -> Optional[str]:
    if pct is None:
        return None
    if pct >= band:
        return "up"
    if pct <= -band:
        return "down"
    return "steady"


def trend(today_score: float, previous: Sequence[Dict]) -> Optional[Dict[str, Any]]:
    """
    Today's session score vs the average of the last TREND_WINDOW sessions.
    `previous` is newest first: [{"date": ..., "score": ...}, ...].
    """
    window = [row for row in previous if row.get("score")][:TREND_WINDOW]
    if not today_score or not window:
        return None
    average = sum(row["score"] for row in window) / len(window)
    pct = pct_change(today_score, average)
    direction = _direction(pct)
    if direction == "up":
        text = f"↑ {format_pct(pct, signed=False)}"
    elif direction == "down":
        text = f"↓ {format_pct(pct, signed=False)}"
    else:
        text = "→ Steady"
    return {
        "pct": pct,
        "direction": direction,
        "text": text,
        "average": average,
        "dates": [row["date"] for row in reversed(window)],
        "count": len(window),
    }


def _matched_prefix(idx: int) -> str:
    return "Your strongest set matched your best" if idx == 1 else "Your top two sets matched your best"


def best_summary(perf: Dict[str, Any], *, has_history: bool) -> Dict[str, str]:
    """
    Short "Vs best" cell for the row (the column header gives the context) plus a
    one-line explanation shown in the info popover.
    """
    key = perf.get("key") or ""
    pct = perf.get("diff_pct")
    idx = perf.get("diff_index") or 0
    rank = _RANK_NAMES[idx] if idx < len(_RANK_NAMES) else f"{_ordinal(idx + 1)} set"
    tier = key.split("_", 1)[0]

    if key in MEDAL_KEYS:
        emoji = _MEDAL_EMOJI.get(tier, "🥇")
        amount = format_pct(pct) if pct is not None else ""
        if key.endswith("_load"):
            explain = f"New best: same strength as your best, with {format_pct(pct, signed=False)} more weight on your {rank}."
        elif idx == 0:
            explain = f"New best: your strongest set beat your best by {format_pct(pct, signed=False)}."
        else:
            explain = f"New best: {_lower_first(_matched_prefix(idx))}, and your {rank} beat it by {format_pct(pct, signed=False)}."
        return {"kind": f"medal medal-{tier}", "emoji": emoji, "chip": amount, "explain": explain}

    if key in BELOW_BEST_KEYS:
        gap = format_pct(pct, signed=False) if pct is not None else ""
        if pct is not None and abs(pct) < 0.95:
            chip = "<1%"
            gap = "less than 1%"
        else:
            chip = f"−{gap}" if gap else "Below"
        if idx == 0:
            explain = f"Your strongest set was {gap} below your best."
        else:
            explain = f"{_matched_prefix(idx)}, but your {rank} was {gap} below it."
        return {"kind": "below", "emoji": "", "chip": chip, "explain": explain}

    if key == "consistent":
        return {"kind": "match", "emoji": "", "chip": "= Best", "explain": "You matched your best exactly."}

    if has_history:
        return {
            "kind": "first",
            "emoji": "",
            "chip": "✦ New",
            "explain": "No earlier session with the same number of sets to compare against yet.",
        }
    return {"kind": "first", "emoji": "", "chip": "✦ First", "explain": "First time logged, so this sets your baseline."}


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def sparkline(
    points: Sequence[Dict[str, Any]],
    *,
    average: Optional[float] = None,
    width: int = 600,
    height: int = 110,
    pad: int = 10,
) -> Optional[Dict[str, Any]]:
    """
    Coordinates for a tiny server-rendered SVG. `points` oldest first:
    [{"score": float, "date": date, "is_today": bool, "is_best": bool}].
    """
    usable = [p for p in points if p.get("score")]
    if len(usable) < 2:
        return None
    values = [p["score"] for p in usable]
    if average:
        values.append(average)
    low, high = min(values), max(values)
    span = (high - low) or max(high * 0.05, 1.0)
    low -= span * 0.1
    high += span * 0.1

    def y_of(value: float) -> float:
        return round(pad + (height - 2 * pad) * (1 - (value - low) / (high - low)), 1)

    step = (width - 2 * pad) / (len(usable) - 1)
    dots = []
    for idx, point in enumerate(usable):
        dots.append({
            "x": round(pad + idx * step, 1),
            "y": y_of(point["score"]),
            "is_today": bool(point.get("is_today")),
            "is_best": bool(point.get("is_best")),
            # Shown when the user slides across the chart.
            "date_text": point.get("date_text") or "",
            "sets": point.get("sets") or "",
            "url": point.get("url") or "",
        })
    return {
        "width": width,
        "height": height,
        "path": " ".join(f"{'M' if i == 0 else 'L'}{d['x']},{d['y']}" for i, d in enumerate(dots)),
        "dots": dots,
        "average_y": y_of(average) if average else None,
        "first_date": usable[0].get("date"),
        "last_date": usable[-1].get("date"),
    }


def long_date(value: Any) -> str:
    """'Sat, 12 Jul 2026'."""
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        return ""
    return f"{value.strftime('%a')}, {value.day} {value.strftime('%b %Y')}"


def short_date(value: Any, *, ref_year: Optional[int] = None) -> str:
    """'10 Aug', or '10 Aug 2025' when the year differs from the viewed workout."""
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        return ""
    text = f"{value.day} {value.strftime('%b')}"
    if ref_year is not None and value.year != ref_year:
        text += f" {value.year}"
    return text
