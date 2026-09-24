"""Infer a readable title for a custom-retrieved workout.

How it works
------------
1. Every plan session has a title ("Chest & Biceps", "Legs", ...). We split each
   title into parts and, for every selected exercise, look at the sessions it
   appears in. The parts common to *all* those sessions are its candidates
   (an exercise in "Chest & Biceps" and "Chest & Triceps" -> Chest).
2. A title like "Chest & Biceps" alone cannot say whether an exercise is chest
   or biceps, so a small keyword classifier on the exercise name breaks the tie
   ("Barbell Curl" -> Biceps).
3. If the plan and the name disagree (an abs exercise sitting inside a Legs
   session), the name wins. Abs is always included, even when the user's
   session titles never mention it; other muscles the user never names in
   their titles are only used when nothing else is found.
4. Parts are joined with " & " in the order the exercises were selected,
   with Abs placed last ("Chest & Triceps & Abs").

This module is pure (no DB access) so it is easy to test.
"""

import re
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple


# Canonical muscle-group names and the words that map to them in titles.
_PART_ALIASES = {
    "chest": "Chest", "pec": "Chest", "pecs": "Chest",
    "back": "Back", "lat": "Back", "lats": "Back",
    "shoulder": "Shoulders", "shoulders": "Shoulders", "delt": "Shoulders", "delts": "Shoulders",
    "bicep": "Biceps", "biceps": "Biceps",
    "tricep": "Triceps", "triceps": "Triceps",
    "forearm": "Forearms", "forearms": "Forearms",
    "leg": "Legs", "legs": "Legs", "lower body": "Legs",
    "abs": "Abs", "ab": "Abs", "core": "Abs", "abdominals": "Abs",
}

# Ordered, first match wins. Specific patterns come before generic ones
# ("leg curl" before "curl", "rear delt" before "fly", "leg raise" before "back").
_KEYWORD_RULES: Sequence[Tuple[str, Set[str]]] = (
    (r"crunch|leg raise|knee raise|v ?tuck|\babs?\b|plank|pallof|oblique|sit ?up|ab wheel|ab roller"
     r"|hollow|dead ?bug|russian twist|toe touch|flutter kick|woodchop|mountain climber", {"Abs"}),
    (r"wrist|forearm|farmer|reverse (?:\w+ )?curl|ulnar|radial|dead hang|gripper", {"Forearms"}),
    (r"squat|leg press|lunge|leg curl|leg extension|calf|hip thrust|adduct|abduct|romanian|\brdl\b"
     r"|glute|hack|step ?up|good morning|split squat|hamstring|quad", {"Legs"}),
    (r"rear delt|lateral raise|face pull|overhead press|shoulder press|\boh press|military press"
     r"|upright row|front raise|arnold|\bdelt|shrug", {"Shoulders"}),
    (r"\bdips?\b", {"Chest", "Triceps"}),
    (r"tricep|pushdown|push down|skull ?crusher|overhead extension|\boh extension|kickback|close ?grip (?:bench|press)"
     r"|french press|jm press", {"Triceps"}),
    (r"curl", {"Biceps"}),
    (r"\brows?\b|pull ?ups?|chin ?ups?|pulldown|pull down|pullover|deadlift|hyper ?extension"
     r"|back extension|\blats?\b|\bt ?bar\b", {"Back"}),
    (r"bench|chest press|\bpress\b|\bfly\b|\bflyes?\b|flies|push ?ups?|\bpec\b|crossover", {"Chest"}),
)

_ALWAYS_SHOWN = {"Abs"}

_TITLE_SPLIT = re.compile(r"\s*(?:&|\+|/|,|\band\b)\s*", re.IGNORECASE)
_SESSION_PREFIX = re.compile(r"^session\s+\d+\s*[-:–—]\s*", re.IGNORECASE)
_TRAILING_DAY_NUMBER = re.compile(r"\s+\d+$")


def _clean(text: str) -> str:
    text = str(text or "").replace("–", "-").replace("—", "-").replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def canonical_part(part: str) -> Optional[str]:
    """Return the display form of a title part (e.g. 'tricep' -> 'Triceps')."""
    cleaned = _clean(part)
    if not cleaned:
        return None
    return _PART_ALIASES.get(cleaned.lower(), cleaned[:1].upper() + cleaned[1:])


def split_title(title: str) -> List[str]:
    """'Session 3 - Back & Triceps' -> ['Back', 'Triceps']."""
    title = _SESSION_PREFIX.sub("", _clean(title))
    parts = []
    for raw in _TITLE_SPLIT.split(title):
        part = canonical_part(raw)
        if part and part not in parts:
            parts.append(part)
    return parts


def title_from_plan_day(category: str, day_name: str, session_title: Optional[str]) -> str:
    """Pick the human title for a plan day ('Session 3' -> its title, 'Legs 2' -> 'Legs')."""
    if str(category).strip().lower() == "session":
        return _clean(session_title or "")
    return _clean(_TRAILING_DAY_NUMBER.sub("", _clean(day_name)) or category)


def classify_by_name(exercise_name: str) -> Set[str]:
    name = _clean(exercise_name).lower().replace("-", " ")
    for pattern, muscles in _KEYWORD_RULES:
        if re.search(pattern, name):
            return set(muscles)
    return set()


def infer_workout_title(
    exercise_names: Iterable[str],
    sessions: Iterable[Tuple[str, Iterable[str]]],
    *,
    key_fn: Callable[[str], Iterable[str]],
) -> Optional[str]:
    """
    exercise_names: the selected exercises, in selection order.
    sessions:       (title, [exercise names]) for every plan day, plus optional
                    history rows (title, [exercise]) that belong to plan titles.
    key_fn:         returns match keys for a name (normalised name, token signature...).
    """
    # exercise key -> list of part-lists, one per session it appears in
    appearances: Dict[str, List[List[str]]] = {}
    plan_vocab: Set[str] = set()
    for title, names in sessions:
        parts = split_title(title)
        if not parts:
            continue
        plan_vocab.update(parts)
        for name in names or []:
            for key in key_fn(name):
                appearances.setdefault(key, []).append(parts)

    recognised = set(_PART_ALIASES.values())
    primary: List[str] = []
    secondary: List[str] = []

    def add(target: List[str], parts: Iterable[str], order_hint: Sequence[str]):
        # Keep the order in which parts appear in the plan title / hint.
        for part in sorted(parts, key=lambda p: order_hint.index(p) if p in order_hint else len(order_hint)):
            if part not in target:
                target.append(part)

    for name in exercise_names:
        seen_part_lists: List[List[str]] = []
        for key in key_fn(name):
            for parts in appearances.get(key, []):
                if parts not in seen_part_lists:
                    seen_part_lists.append(parts)

        order_hint: List[str] = seen_part_lists[0] if seen_part_lists else []
        candidates: Set[str] = set(order_hint)
        for parts in seen_part_lists[1:]:
            candidates &= set(parts)

        by_name = classify_by_name(name)
        order_hint = order_hint or sorted(by_name)

        if candidates & by_name:
            chosen = candidates & by_name
        elif by_name and (not candidates or candidates <= recognised):
            chosen = by_name
        else:
            chosen = candidates

        if not chosen:
            continue
        # Muscles the user never names in their titles are fillers, except
        # Abs, which is always shown.
        in_vocab = {p for p in chosen if p in plan_vocab or p in _ALWAYS_SHOWN or not plan_vocab}
        add(primary, in_vocab, order_hint)
        add(secondary, chosen - in_vocab, order_hint)

    parts = primary or secondary
    # Abs reads best at the end: "Chest & Triceps & Abs".
    parts = [p for p in parts if p not in _ALWAYS_SHOWN] + [p for p in parts if p in _ALWAYS_SHOWN]
    return " & ".join(parts) if parts else None
