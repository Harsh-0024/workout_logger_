import re
from typing import Dict, Iterable, List, Sequence, Tuple


_DASH_CHARS = "-‐‑‒–—−"
_APOSTROPHES = "’`´"


def _collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def normalize_exercise_name(name: str) -> str:
    """
    Minimal normalization for exercise-name matching.

    Goals:
    - Fix trivial punctuation/case differences (’ vs ', unicode dashes, extra spaces).
    - Fix hyphen vs space ("Pull-Ups" vs "Pull Ups").
    - Avoid overly-permissive fuzzy matching (no stemming, no substring matching).
    """
    s = (name or "").strip()
    if not s:
        return ""

    # Normalize apostrophes to a plain single quote.
    for ch in _APOSTROPHES:
        s = s.replace(ch, "'")

    # Normalize all dash-like characters to spaces (handles "Pull-Ups" vs "Pull Ups").
    for ch in _DASH_CHARS:
        s = s.replace(ch, " ")

    s = _collapse_ws(s).lower()
    return s


def token_signature(name: str) -> Tuple[str, ...]:
    """
    Order-insensitive signature used only as a conservative fallback when an exact
    normalized match is missing.

    Important: this intentionally keeps all tokens (including 'machine', 'barbell',
    etc.) to reduce accidental merging of distinct exercises.
    """
    norm = normalize_exercise_name(name)
    if not norm:
        return tuple()
    tokens = [t for t in norm.split(" ") if t]
    return tuple(sorted(tokens))


def build_name_index(names: Iterable[str]) -> Dict[str, Dict]:
    """
    Returns:
      {
        "by_norm": { normalized_name: [original1, original2, ...] },
        "by_sig": { token_signature: [original1, original2, ...] },
      }
    """
    by_norm: Dict[str, List[str]] = {}
    by_sig: Dict[Tuple[str, ...], List[str]] = {}

    for raw in names or []:
        if not raw:
            continue
        norm = normalize_exercise_name(raw)
        sig = token_signature(raw)
        if norm:
            by_norm.setdefault(norm, [])
            if raw not in by_norm[norm]:
                by_norm[norm].append(raw)
        if sig:
            by_sig.setdefault(sig, [])
            if raw not in by_sig[sig]:
                by_sig[sig].append(raw)

    return {"by_norm": by_norm, "by_sig": by_sig}


def resolve_equivalent_names(input_name: str, index: Dict[str, Dict]) -> List[str]:
    """
    Resolve input_name to the actual stored exercise names (original strings).

    Priority:
    1) Exact normalized match (can return multiple originals: e.g., different casing).
    2) Order-insensitive match ONLY if it's unambiguous (exactly one stored name for the signature).
    3) Otherwise, return [].
    """
    if not input_name or not index:
        return []

    by_norm = (index or {}).get("by_norm") or {}
    by_sig = (index or {}).get("by_sig") or {}

    norm = normalize_exercise_name(input_name)
    if norm and norm in by_norm:
        return list(by_norm[norm])

    sig = token_signature(input_name)
    if sig and sig in by_sig and len(by_sig[sig]) == 1:
        return [by_sig[sig][0]]

    return []

