"""Rep-range settings: one "Exercise: range" line per exercise.

The stored text stays the single source every reader parses ("Bench Press: 6–10",
"Dips: 3, 6–12" for a set count, "Farmer's Walk: 20–60s" for time, "Dips:" for an exercise
whose range isn't set yet). This module reads whatever someone types or pastes and writes it
back in that one clean form.
"""
import re
from typing import List, Optional, Tuple

from parsers.workout import _title_case
from services.exercise_matching import normalize_exercise_name

# "6-10", "6 to 10", "12", "30-60s", with an optional set count first: "3, 6-8", "3x6-8".
_VALUE = (
    r"(?:(?P<sets>\d+)\s*(?:,|x|×|\*)\s*)?"
    r"(?P<lo>\d+)(?:\s*(?:-|–|—|to)\s*(?P<hi>\d+))?"
    r"\s*(?P<unit>s|secs?|seconds)?"
)
_VALUE_RE = re.compile(rf"^\s*{_VALUE}\s*$", re.IGNORECASE)
_TRAILING_VALUE_RE = re.compile(rf"^(?P<name>.*?[^\W\d_].*?)\s+(?P<value>{_VALUE})\s*$", re.IGNORECASE)


# Written in place of a range to say there isn't one yet: "(blank)", "n/a", "-", "TBD"...
_NO_RANGE_WORDS = {"blank", "empty", "none", "nil", "null", "na", "n/a", "tbd", "tba", "?", "-", "–", "—"}


def is_no_range(value: str) -> bool:
    """True for an empty range, or a word that stands in for one ("(blank)", "n/a", "-")."""
    text = re.sub(r"\s+", " ", (value or "").strip()).strip("()[]<>{}").strip().lower()
    return not text or text in _NO_RANGE_WORDS


def format_rep_value(value: str) -> str:
    """ "6 - 10" -> "6–10", "3,6-8" -> "3, 6–8", "10-6" -> "6–10", "30-60 sec" -> "30–60s".
    No range ("", "(blank)", "n/a") -> "". Anything else (say "AMRAP") is kept as typed."""
    if is_no_range(value):
        return ""
    text = re.sub(r"\s+", " ", (value or "").strip())
    m = _VALUE_RE.match(text)
    if not m:
        return text
    lo, hi = int(m.group("lo")), int(m.group("hi") or m.group("lo"))
    lo, hi = min(lo, hi), max(lo, hi)
    reps = f"{lo}–{hi}" if hi != lo else f"{lo}"
    if m.group("unit"):
        reps += "s"
    return f"{int(m.group('sets'))}, {reps}" if m.group("sets") else reps


def _clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", (name or "").strip()).strip(" :=-–—\t")
    return _title_case(name)


def _is_value(text: str) -> bool:
    return bool(_VALUE_RE.match(text or ""))


def parse_rep_entries(text: str) -> List[Tuple[str, str]]:
    """Read rep ranges however they are written, one exercise per line or name and range on
    alternate lines: "Bench: 6-10", "Bench = 6-10", "Bench<TAB>6-10", "Bench 6-10".
    Returns [(name, value)] in order; a name with no range gets "". """
    entries: List[Tuple[str, str]] = []
    pending: Optional[str] = None  # a name still waiting for its range on the next line

    def flush():
        nonlocal pending
        if pending:
            entries.append((pending, ""))
        pending = None

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = value = None
        for sep in (":", "=", "\t"):
            if sep in line:
                name, value = line.split(sep, 1) if sep != "\t" else line.rsplit(sep, 1)
                break
        if name is None:
            if _is_value(line):
                if pending:
                    entries.append((pending, format_rep_value(line)))
                    pending = None
                continue
            m = _TRAILING_VALUE_RE.match(line)
            if m:
                name, value = m.group("name"), m.group("value")
        flush()
        if name is None:
            pending = _clean_name(line) or None
            continue
        name = _clean_name(name)
        if not name:
            continue
        value = format_rep_value(value)
        if value:
            entries.append((name, value))
        else:
            pending = name
    flush()
    return entries


def merge_rep_entries(entries: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """One line per exercise: a repeated name keeps its first place and spelling and takes the
    last range given. Names without a range stay, with an empty range."""
    order: List[str] = []
    merged = {}
    for name, value in entries:
        key = normalize_exercise_name(name)
        if not key:
            continue
        if key not in merged:
            order.append(key)
            merged[key] = (name, value or "")
        elif value:
            merged[key] = (merged[key][0], value)
    return [merged[k] for k in order]


def rep_entries_to_text(entries: List[Tuple[str, str]]) -> str:
    return "\n".join(f"{name}: {value}".rstrip() for name, value in entries)


def canonical_rep_text(text: str) -> str:
    """Whatever was typed, stored the same clean way."""
    return rep_entries_to_text(merge_rep_entries(parse_rep_entries(text)))
