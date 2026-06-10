import re
from typing import Dict, Iterable, List, Sequence, Tuple


_DASH_CHARS = "-‐‑‒–—−"
_APOSTROPHES = "’`´"

_TOKEN_PLURAL_EXCEPTIONS = {
    # Keep compound/abbreviation-like tokens intact.
    "oh",  # normalized to overhead below
    "ui",  # hypothetical; harmless
}


def _normalize_token(tok: str) -> str:
    t = (tok or "").strip().lower()
    if not t:
        return ""

    # Common abbreviations / shorthand.
    if t in {"oh", "overhead"}:
        return "overhead"

    # Common singular/plural swap.
    if t in {"tricep", "triceps"}:
        return "triceps"

    # Generic plural normalization (keeps 'ss' words like 'press').
    if (
        t not in _TOKEN_PLURAL_EXCEPTIONS
        and len(t) > 3
        and t.endswith("s")
        and not t.endswith("ss")
    ):
        t = t[:-1]

    # Re-apply the singular/plural mapping after stripping.
    if t in {"tricep", "triceps"}:
        t = "triceps"
    if t in {"oh", "overhead"}:
        t = "overhead"

    return t


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

    # Treat underscores like spaces (common in data exports).
    s = s.replace("_", " ")

    # Normalize all dash-like characters to spaces (handles "Pull-Ups" vs "Pull Ups").
    for ch in _DASH_CHARS:
        s = s.replace(ch, " ")

    s = _collapse_ws(s).lower()

    tokens = [t for t in s.split(" ") if t]
    norm_tokens = [_normalize_token(t) for t in tokens]
    norm_tokens = [t for t in norm_tokens if t]
    return " ".join(norm_tokens)


def token_signature(name: str) -> Tuple[str, ...]:
    """
    Order-insensitive signature used only as a conservative fallback when an exact
    normalized match is missing.

    Important: signature is intentionally conservative:
    - It uses a normalized token stream (plural/oh normalized).
    - It keeps all remaining tokens (including 'machine', 'barbell', etc.) to reduce accidental merging.
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
    if sig and sig in by_sig:
        signature_matches = list(by_sig[sig])
        if len(signature_matches) == 1:
            return [signature_matches[0]]

        # Multiple stored originals can be the same exercise after punctuation /
        # dash / casing normalization, e.g. "Wrist Flexion - Dumbbell" and
        # "Wrist Flexion – Dumbbell". Treat those as safe aliases, but keep
        # genuinely different word orders blocked as ambiguous.
        normalized_matches = {
            normalize_exercise_name(match)
            for match in signature_matches
            if normalize_exercise_name(match)
        }
        if len(normalized_matches) == 1:
            return signature_matches

    return []
