from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from list_of_exercise import BW_EXERCISES, list_of_exercises
from models import BodyweightExercisePreference, WorkoutLog
from services.exercise_matching import normalize_exercise_name
from services.helpers import get_set_stats, get_timed_set_stats


BODYWEIGHT_TOKEN_RE = re.compile(
    r"(?:\bbw(?:\s*/\s*\d+(?:\.\d+)?)?(?:\s*[+-]\s*\d+(?:\.\d+)?)?\b|\bbody\s*weight\b|\bbodyweight\b)",
    re.IGNORECASE,
)


def bodyweight_exercise_key(exercise_name: str) -> str:
    normalized = normalize_exercise_name(exercise_name or "")
    return (normalized or "").replace("'", "").strip()


def has_bodyweight_token(text: str) -> bool:
    return bool(BODYWEIGHT_TOKEN_RE.search(str(text or "")))


def _default_bodyweight_keys() -> set[str]:
    return {bodyweight_exercise_key(name) for name in BW_EXERCISES if bodyweight_exercise_key(name)}


def is_default_bodyweight_exercise(exercise_name: str) -> bool:
    key = bodyweight_exercise_key(exercise_name)
    return bool(key and key in _default_bodyweight_keys())


def get_bodyweight_preference(db_session, user_id: int, exercise_name: str) -> Optional[BodyweightExercisePreference]:
    key = bodyweight_exercise_key(exercise_name)
    if not key:
        return None
    query = (
        db_session.query(BodyweightExercisePreference)
        .filter(BodyweightExercisePreference.user_id == user_id)
        .filter(BodyweightExercisePreference.exercise_key == key)
    )
    if not hasattr(query, "first"):
        return None
    return query.first()


def set_bodyweight_preference(
    db_session,
    user_id: int,
    exercise_name: str,
    is_bodyweight: bool,
    *,
    source: str = "manual",
) -> Optional[BodyweightExercisePreference]:
    key = bodyweight_exercise_key(exercise_name)
    clean_name = str(exercise_name or "").strip()
    if not key or not clean_name:
        return None

    row = get_bodyweight_preference(db_session, user_id, clean_name)
    if row:
        row.exercise_name = clean_name
        row.is_bodyweight = bool(is_bodyweight)
        if source:
            row.source = source
        row.updated_at = datetime.now()
    else:
        row = BodyweightExercisePreference(
            user_id=user_id,
            exercise_key=key,
            exercise_name=clean_name,
            is_bodyweight=bool(is_bodyweight),
            source=source or "manual",
        )
        db_session.add(row)
    return row


def is_bodyweight_enabled(
    db_session,
    user_id: int,
    exercise_name: str,
    *,
    exercise_text: str = "",
) -> bool:
    if has_bodyweight_token(exercise_text):
        return True

    pref = get_bodyweight_preference(db_session, user_id, exercise_name)
    if pref is not None:
        return bool(pref.is_bodyweight)

    return is_default_bodyweight_exercise(exercise_name)


def infer_log_uses_bodyweight(db_session, log: WorkoutLog) -> bool:
    explicit = getattr(log, "uses_bodyweight", None)
    if explicit is not None:
        return bool(explicit)

    text = getattr(log, "exercise_string", "") or ""
    if has_bodyweight_token(text):
        return True

    exercise = getattr(log, "exercise", "") or ""
    if db_session is None:
        enabled = is_default_bodyweight_exercise(exercise)
    else:
        enabled = is_bodyweight_enabled(db_session, getattr(log, "user_id", 0), exercise, exercise_text="")
    if not enabled:
        return False

    # Legacy compatibility: old default-BW logs used 1 as a bodyweight placeholder.
    sets_json = getattr(log, "sets_json", None)
    weights = list((sets_json or {}).get("weights") or []) if isinstance(sets_json, dict) else []
    if not weights:
        return True
    try:
        return all(float(w) <= 1 for w in weights if w is not None)
    except (TypeError, ValueError):
        return False


def resolve_bodyweight_sets(
    sets_json: Optional[Dict],
    bodyweight: Optional[float],
    uses_bodyweight: bool,
) -> Dict:
    if not sets_json or not isinstance(sets_json, dict):
        return {}
    weights = list(sets_json.get("weights") or [])
    reps = list(sets_json.get("reps") or [])
    if not weights or not reps:
        return {"weights": weights, "reps": reps}
    if not uses_bodyweight or bodyweight is None:
        return {"weights": weights, "reps": reps}

    resolved = []
    for weight in weights:
        try:
            resolved.append(float(bodyweight) + float(weight))
        except (TypeError, ValueError):
            resolved.append(weight)
    return {"weights": resolved, "reps": reps}


def _resolve_legacy_bodyweight_sets(log: WorkoutLog) -> Dict:
    sets_json = getattr(log, "sets_json", None)
    if not sets_json or not isinstance(sets_json, dict):
        return {}
    weights = list(sets_json.get("weights") or [])
    reps = list(sets_json.get("reps") or [])
    if not weights or not reps:
        return {"weights": weights, "reps": reps}

    bodyweight = getattr(log, "bodyweight", None)
    if bodyweight is None:
        return {"weights": weights, "reps": reps}

    text = getattr(log, "exercise_string", "") or ""
    if has_bodyweight_token(text):
        # Historical parser calls already expanded explicit bw tokens when bodyweight
        # was available, so leave those stored loads alone unless the log was
        # explicitly migrated to the new offset rule.
        return {"weights": weights, "reps": reps}

    try:
        if all(float(w) <= 1 for w in weights if w is not None):
            return {"weights": [float(bodyweight)] * len(weights), "reps": reps}
    except (TypeError, ValueError):
        pass
    return {"weights": weights, "reps": reps}


def effective_sets_for_log(db_session, log: WorkoutLog) -> Dict:
    explicit = getattr(log, "uses_bodyweight", None)
    if explicit is None:
        if infer_log_uses_bodyweight(db_session, log):
            return _resolve_legacy_bodyweight_sets(log)
        sets_json = getattr(log, "sets_json", None)
        return sets_json if isinstance(sets_json, dict) else {}

    return resolve_bodyweight_sets(
        getattr(log, "sets_json", None),
        getattr(log, "bodyweight", None),
        bool(explicit),
    )


def effective_sets_for_current(
    sets_json: Optional[Dict],
    bodyweight: Optional[float],
    uses_bodyweight: bool,
) -> Dict:
    return resolve_bodyweight_sets(sets_json, bodyweight, uses_bodyweight)


def recalculate_log_metrics(db_session, log: WorkoutLog, *, is_timed: bool = False) -> None:
    effective_sets = effective_sets_for_log(db_session, log)
    if is_timed:
        peak, _score_sum, _volume = get_timed_set_stats(effective_sets)
    else:
        peak, _score_sum, _volume = get_set_stats(effective_sets)

    weights = list((effective_sets or {}).get("weights") or [])
    reps = list((effective_sets or {}).get("reps") or [])
    top_weight = None
    top_reps = None
    if weights and reps:
        try:
            top_weight = max(float(w) for w in weights if w is not None)
            idx = [float(w) for w in weights].index(top_weight)
            top_reps = int(reps[idx]) if idx < len(reps) else None
        except (TypeError, ValueError):
            top_weight = None
            top_reps = None

    log.top_weight = top_weight
    log.top_reps = top_reps
    log.estimated_1rm = peak if peak and peak > 0 else None


def get_bodyweight_conflict_info(db_session, user_id: int, exercise_name: str) -> Dict:
    key = bodyweight_exercise_key(exercise_name)
    if not key:
        return {"has_conflict": False, "total": 0, "bodyweight_logs": 0, "explicit_logs": 0}

    logs = db_session.query(WorkoutLog).filter(WorkoutLog.user_id == user_id).all()
    total = 0
    bodyweight_logs = 0
    explicit_logs = 0
    for log in logs:
        if bodyweight_exercise_key(getattr(log, "exercise", "") or "") != key:
            continue
        total += 1
        text = getattr(log, "exercise_string", "") or ""
        explicit_token = has_bodyweight_token(text)
        if explicit_token:
            explicit_logs += 1
        if explicit_token or getattr(log, "uses_bodyweight", None) is True:
            bodyweight_logs += 1

    pref = get_bodyweight_preference(db_session, user_id, exercise_name)
    was_selected = bool(pref and pref.is_bodyweight)
    return {
        "has_conflict": bool(bodyweight_logs or explicit_logs or was_selected),
        "total": total,
        "bodyweight_logs": bodyweight_logs,
        "explicit_logs": explicit_logs,
        "was_selected": was_selected,
    }


def matching_logs_for_exercise(db_session, user_id: int, exercise_name: str) -> List[WorkoutLog]:
    key = bodyweight_exercise_key(exercise_name)
    if not key:
        return []
    logs = db_session.query(WorkoutLog).filter(WorkoutLog.user_id == user_id).all()
    return [
        log for log in logs
        if bodyweight_exercise_key(getattr(log, "exercise", "") or "") == key
    ]


def _stored_weights_look_already_effective(log: WorkoutLog) -> bool:
    bodyweight = getattr(log, "bodyweight", None)
    sets_json = getattr(log, "sets_json", None)
    if bodyweight is None or not isinstance(sets_json, dict):
        return False
    weights = list(sets_json.get("weights") or [])
    if not weights:
        return False
    try:
        bw = float(bodyweight)
        return any(float(w) >= bw * 0.5 for w in weights if w is not None)
    except (TypeError, ValueError):
        return False


def backfill_bodyweight_log_flags(db_session, user_id: int) -> int:
    """
    Give legacy logs a stable bodyweight interpretation.

    New logs store bodyweight exercise weights as offsets. Older logs do not have
    a flag, so we infer once from explicit BW text and the user's current/default
    bodyweight exercise settings. Explicit BW logs that already look expanded are
    left as literal/effective loads to avoid adding bodyweight twice.
    """
    logs = (
        db_session.query(WorkoutLog)
        .filter(WorkoutLog.user_id == user_id)
        .filter(WorkoutLog.uses_bodyweight.is_(None))
        .all()
    )
    updated = 0
    for log in logs:
        exercise = getattr(log, "exercise", "") or ""
        text = getattr(log, "exercise_string", "") or ""
        explicit = has_bodyweight_token(text)
        if explicit and _stored_weights_look_already_effective(log):
            uses_offsets = False
        elif explicit:
            uses_offsets = True
        else:
            uses_offsets = is_bodyweight_enabled(db_session, user_id, exercise, exercise_text="")

        log.uses_bodyweight = bool(uses_offsets)
        recalculate_log_metrics(db_session, log)
        updated += 1
    return updated


def build_bodyweight_settings_rows(db_session, user_id: int) -> List[Dict]:
    logged_names = [
        row[0]
        for row in (
            db_session.query(WorkoutLog.exercise)
            .filter(WorkoutLog.user_id == user_id)
            .distinct()
            .all()
        )
        if row and row[0]
    ]
    pref_rows = (
        db_session.query(BodyweightExercisePreference)
        .filter(BodyweightExercisePreference.user_id == user_id)
        .all()
    )

    names_by_key: Dict[str, str] = {}
    for name in list(list_of_exercises) + list(BW_EXERCISES) + logged_names:
        key = bodyweight_exercise_key(name)
        if key and key not in names_by_key:
            names_by_key[key] = str(name)
    prefs_by_key = {row.exercise_key: row for row in pref_rows}
    for row in pref_rows:
        names_by_key[row.exercise_key] = row.exercise_name

    rows = []
    default_keys = _default_bodyweight_keys()
    for key, name in sorted(names_by_key.items(), key=lambda item: item[1].lower()):
        pref = prefs_by_key.get(key)
        enabled = bool(pref.is_bodyweight) if pref is not None else key in default_keys
        conflict = get_bodyweight_conflict_info(db_session, user_id, name)
        if pref is not None:
            source = pref.source
        elif key in default_keys:
            source = "default"
        else:
            source = "manual"
        rows.append({
            "key": key,
            "name": name,
            "enabled": enabled,
            "source": source,
            "is_default": key in default_keys,
            "conflict": conflict,
        })
    return rows
