import re
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple


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


def compound_vocabulary(names: Iterable[str]) -> FrozenSet[str]:
    """Every single word used in these names. A word written as two ("Push Down") is joined only
    when the joined word is in here, i.e. some name already writes it as one ("Pushdown"): words
    are never invented, and the app needn't know what's an English word."""
    return frozenset(t for name in names or [] for t in normalize_exercise_name(name).split(" ") if t)


def join_known_compounds(tokens: Sequence[str], vocabulary: Optional[FrozenSet[str]]) -> List[str]:
    """Join two neighbouring words whose joined form is a word in `vocabulary`:
    ["rod", "push", "down"] with "pushdown" known -> ["rod", "pushdown"]. Only pairs, left first."""
    tokens = list(tokens)
    if not vocabulary or len(tokens) < 2:
        return tokens
    out: List[str] = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens):
            joined = _normalize_token(tokens[i] + tokens[i + 1])
            if joined in vocabulary:
                out.append(joined)
                i += 2
                continue
        out.append(tokens[i])
        i += 1
    return out


def token_signature(name: str, vocabulary: Optional[FrozenSet[str]] = None) -> Tuple[str, ...]:
    """
    Order-insensitive signature used only as a conservative fallback when an exact
    normalized match is missing.

    Important: signature is intentionally conservative:
    - It uses a normalized token stream (plural/oh normalized).
    - It keeps all remaining tokens (including 'machine', 'barbell', etc.) to reduce accidental merging.
    - Given a vocabulary, a word split in two is joined when the vocabulary writes it as one.
    """
    norm = normalize_exercise_name(name)
    if not norm:
        return tuple()
    tokens = join_known_compounds([t for t in norm.split(" ") if t], vocabulary)
    return tuple(sorted(tokens))


def same_exercise(a: str, b: str) -> bool:
    """Whether two names are the same exercise: the same words in any order, a word written as one
    or two counted alike ("Rod Push-Down" / "Pushdown Rod")."""
    vocabulary = compound_vocabulary([a, b])
    signature = token_signature(a, vocabulary)
    return bool(signature) and signature == token_signature(b, vocabulary)


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
    names_seen: List[str] = []

    for raw in names or []:
        if not raw:
            continue
        names_seen.append(raw)
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

    return {"by_norm": by_norm, "by_sig": by_sig, "names": list(dict.fromkeys(n for n in names_seen))}


def resolve_equivalent_names(input_name: str, index: Dict[str, Dict]) -> List[str]:
    """
    Resolve input_name to the actual stored exercise names (original strings).

    Priority:
    1) Exact normalized match, plus same-token aliases for already-saved reordered names.
    2) Order-insensitive match ONLY if it's unambiguous or only differs by normalized punctuation/casing.
    3) Otherwise, return [].
    """
    if not input_name or not index:
        return []

    by_norm = (index or {}).get("by_norm") or {}
    by_sig = (index or {}).get("by_sig") or {}

    sig = token_signature(input_name)
    signature_matches = list(by_sig.get(sig, [])) if sig else []

    norm = normalize_exercise_name(input_name)
    if norm and norm in by_norm:
        matches = list(by_norm[norm])
        for match in signature_matches:
            if match not in matches:
                matches.append(match)
        return matches

    if signature_matches:
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

    # 3) A word written as one or two: "Rod Push Down" is the logged "Rod Pushdown".
    joined_matches = [n for n in (index or {}).get("names") or [] if same_exercise(input_name, n)]
    if joined_matches:
        vocabulary = compound_vocabulary([input_name, *joined_matches])
        orders = {
            " ".join(join_known_compounds(normalize_exercise_name(n).split(" "), vocabulary))
            for n in joined_matches
        }
        # Same caution as above: different word orders among the matches stay ambiguous.
        if len(joined_matches) == 1 or len(orders) == 1:
            return joined_matches

    return []
