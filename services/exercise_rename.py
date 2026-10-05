"""Renaming an exercise without losing its history.

An exercise is known only by its name, so a renamed exercise used to start from nothing. A rename
here moves everything stored under the old name to the new one: logs, best lifts, preferences,
and the names in the user's own plan and rep ranges. If the new name already has logs, the two
histories become one. Gym-tagged versions follow their base name and keep their tag:
"Lat Row (Wellness)" -> "Single-Arm Row (Wellness)".

The rename is also remembered, so anything still logged under the old name (habit, Shortcuts,
offline logs that sync later) is saved under the new name.
"""
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from models import (
    BodyweightExercisePreference,
    CustomRetrievalEvent,
    ExerciseGroupChoice,
    ExerciseRename,
    Lift,
    Plan,
    RepRange,
    StatsExerciseView,
    TimedExercisePreference,
    WorkoutLog,
)
from services.exercise_matching import normalize_exercise_name
from services.rep_ranges import _TAG_RE, parse_rep_entries, rep_entries_to_text


MAX_NAME_LENGTH = 100  # workout_logs.exercise


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip()


def _split_tag(name: str, old_key: str) -> Optional[str]:
    """The part of `name` after its base when the base is the old exercise: "" for the exercise
    itself, " (Wellness)" for a tagged version, None for anything else."""
    current = _clean(name)
    if normalize_exercise_name(current) == old_key:
        return ""
    full = current
    while True:
        m = _TAG_RE.match(current)
        if not m:
            return None
        current = m.group("base").rstrip(" -–—").strip()
        if normalize_exercise_name(current) == old_key:
            return full[len(current):]


def _renamed(name: str, old_key: str, new_name: str) -> Optional[str]:
    suffix = _split_tag(name, old_key)
    return None if suffix is None else new_name + suffix


# --- Remembered renames ---

def _redirects(db_session, user_id: int) -> Dict[str, str]:
    rows = (
        db_session.query(ExerciseRename.old_key, ExerciseRename.new_name)
        .filter(ExerciseRename.user_id == user_id)
        .all()
    )
    return {old_key: new_name for old_key, new_name in rows}


def resolve_renamed_exercise(db_session, user_id: int, name: str, redirects: Optional[Dict[str, str]] = None) -> str:
    """The name to save a log under: the new name if this one (or its base, for a gym-tagged
    name) was renamed, otherwise the name as given."""
    redirects = _redirects(db_session, user_id) if redirects is None else redirects
    if not redirects or not name:
        return name
    current = _clean(name)
    full = current
    while current:
        new_name = redirects.get(normalize_exercise_name(current))
        if new_name:
            return new_name + full[len(current):]
        m = _TAG_RE.match(current)
        if not m:
            break
        current = m.group("base").rstrip(" -–—").strip()
    return name


def apply_renames_to_parsed(db_session, user, parsed_data: Dict) -> None:
    """Point parsed exercises logged under an old name at the new one, before they are saved."""
    exercises = (parsed_data or {}).get("exercises") or []
    if not exercises:
        return
    redirects = _redirects(db_session, user.id)
    if not redirects:
        return
    for item in exercises:
        old = item.get("name") or ""
        new = resolve_renamed_exercise(db_session, user.id, old, redirects)
        if new != old:
            item["name"] = new
            item["exercise_string"] = _rename_in_exercise_string(item.get("exercise_string") or "", old, new)


def _remember(db_session, user_id: int, old_name: str, new_name: str, log_ids: List[int]) -> None:
    old_key = normalize_exercise_name(old_name)
    new_key = normalize_exercise_name(new_name)
    rows = db_session.query(ExerciseRename).filter(ExerciseRename.user_id == user_id).all()
    for row in rows:
        # Earlier renames to the old name now lead to the new one (A -> B, then B -> C).
        if normalize_exercise_name(row.new_name) == old_key:
            row.new_name = new_name
        # The new name is in use again, so nothing should send it elsewhere (A -> B, then B -> A).
        if row.old_key == new_key or normalize_exercise_name(row.new_name) == row.old_key:
            db_session.delete(row)
    if old_key == new_key:
        return
    existing = next((r for r in rows if r.old_key == old_key and r not in db_session.deleted), None)
    if existing is None:
        db_session.add(ExerciseRename(
            user_id=user_id, old_key=old_key, old_name=old_name, new_name=new_name,
            log_ids=log_ids, created_at=datetime.now(),
        ))
    else:
        existing.new_name = new_name
        existing.log_ids = sorted(set(existing.log_ids or []) | set(log_ids))
        existing.created_at = datetime.now()


# --- The rename itself ---

def _rename_in_exercise_string(text: str, old: str, new: str) -> str:
    """The logged text starts with the exercise's name; give it the new one."""
    stripped = text.lstrip()
    if old and stripped.lower().startswith(old.lower()):
        rest = stripped[len(old):]
        if not rest or not rest[0].isalnum():
            return text[:len(text) - len(stripped)] + new + rest
    return text


def _logged_names(db_session, user_id: int) -> List[str]:
    rows = db_session.query(WorkoutLog.exercise).filter(WorkoutLog.user_id == user_id).distinct().all()
    return [r[0] for r in rows if r[0]]


def _name_pairs(db_session, user_id: int, old_name: str, new_name: str) -> List[Tuple[str, str]]:
    """(old, new) for the exercise and each gym-tagged version of it the user has logged."""
    old_key = normalize_exercise_name(old_name)
    pairs = {old_name: new_name}
    for name in _logged_names(db_session, user_id):
        renamed = _renamed(name, old_key, new_name)
        if renamed is not None:
            pairs[name] = renamed
    return list(pairs.items())


def rename_preview(db_session, user, old_name: str, new_name: str) -> Dict:
    """What a rename would do, for the confirm step."""
    old_name, new_name = _clean(old_name), _clean(new_name)
    old_key, new_key = normalize_exercise_name(old_name), normalize_exercise_name(new_name)
    logged = set(_logged_names(db_session, user.id))
    pairs = _name_pairs(db_session, user.id, old_name, new_name)
    moving = (
        db_session.query(WorkoutLog.id)
        .filter(WorkoutLog.user_id == user.id, WorkoutLog.exercise.in_([o for o, _ in pairs]))
        .count()
    )
    already = 0
    if new_key != old_key:
        new_names = [n for n in logged if _split_tag(n, new_key) is not None]
        if new_names:
            already = (
                db_session.query(WorkoutLog.id)
                .filter(WorkoutLog.user_id == user.id, WorkoutLog.exercise.in_(new_names))
                .count()
            )
    followers = sum(1 for f in _followers(db_session, user) if _has_history(db_session, f.id, old_key))
    return {
        "old_name": old_name,
        "new_name": new_name,
        "moving": moving,
        "followers": followers,
        "from_followed_plan": _from_followed_plan(db_session, user, old_key),
        "already": already,
        "tagged": sorted(o for o, _ in pairs if o != old_name and o in logged),
        "same": old_name == new_name,
    }


def _from_followed_plan(db_session, user, old_key: str) -> bool:
    """Whether this name comes from a plan or rep ranges the user follows, where renaming it only
    for themselves would cut their history off from it."""
    from services.retrieve import get_effective_plan_text, get_effective_rep_ranges_text, is_plan_owner

    if is_plan_owner(db_session, user):
        return False
    names = []
    if getattr(user, "follow_admin_plan", False):
        names += plan_exercise_names(get_effective_plan_text(db_session, user))
    if getattr(user, "follow_admin_exercises", False):
        names += rep_exercise_names(get_effective_rep_ranges_text(db_session, user))
    return any(normalize_exercise_name(n) == old_key for n in names)


def validate_rename(old_name: str, new_name: str) -> Optional[str]:
    """An error to show, or None."""
    old_name, new_name = _clean(old_name), _clean(new_name)
    if not old_name or not normalize_exercise_name(old_name):
        return "Choose the exercise to rename."
    if not new_name or not normalize_exercise_name(new_name):
        return "Type the new name."
    if len(new_name) > MAX_NAME_LENGTH:
        return f"Keep the name under {MAX_NAME_LENGTH} characters."
    if old_name == new_name:
        return "That's already its name."
    return None


def _merge_unique_rows(db_session, model, user_id: int, old_key: str, new_key: str, merge) -> None:
    """Move a preference row from old_key to new_key; when both exist, `merge(keep, drop)` decides
    what the kept row holds and the other is removed."""
    if old_key == new_key or not old_key or not new_key:
        return
    old_row = db_session.query(model).filter(model.user_id == user_id, model.exercise_key == old_key).first()
    if old_row is None:
        return
    new_row = db_session.query(model).filter(model.user_id == user_id, model.exercise_key == new_key).first()
    if new_row is None:
        old_row.exercise_key = new_key
        return
    merge(new_row, old_row)
    db_session.delete(old_row)
    db_session.flush()


def _newer_wins(keep, drop, *fields):
    if (getattr(drop, "updated_at", None) or datetime.min) > (getattr(keep, "updated_at", None) or datetime.min):
        for field in fields:
            setattr(keep, field, getattr(drop, field))


def _move_preferences(db_session, user_id: int, old: str, new: str) -> None:
    from services.bodyweight import bodyweight_exercise_key
    from services.logging import _timed_lookup_key
    from services.stats import _normalize_exercise_name as stats_key

    _merge_unique_rows(
        db_session, TimedExercisePreference, user_id, _timed_lookup_key(old), _timed_lookup_key(new),
        lambda keep, drop: _newer_wins(keep, drop, "is_timed"),
    )
    _merge_unique_rows(
        db_session, BodyweightExercisePreference, user_id, bodyweight_exercise_key(old), bodyweight_exercise_key(new),
        lambda keep, drop: _newer_wins(keep, drop, "is_bodyweight", "source"),
    )
    bw_new = db_session.query(BodyweightExercisePreference).filter_by(
        user_id=user_id, exercise_key=bodyweight_exercise_key(new)).first()
    if bw_new is not None:
        bw_new.exercise_name = new
    _merge_unique_rows(
        db_session, ExerciseGroupChoice, user_id, normalize_exercise_name(old), normalize_exercise_name(new),
        lambda keep, drop: _newer_wins(keep, drop, "group_name"),
    )

    def merge_views(keep, drop):
        keep.view_count = int(keep.view_count or 0) + int(drop.view_count or 0)
        keep.last_viewed_at = max(keep.last_viewed_at or datetime.min, drop.last_viewed_at or datetime.min)

    _merge_unique_rows(db_session, StatsExerciseView, user_id, stats_key(old), stats_key(new), merge_views)

    old_key, new_key = normalize_exercise_name(old), normalize_exercise_name(new)
    if old_key != new_key:
        (
            db_session.query(CustomRetrievalEvent)
            .filter(CustomRetrievalEvent.user_id == user_id, CustomRetrievalEvent.exercise_key == old_key)
            .update({CustomRetrievalEvent.exercise_key: new_key}, synchronize_session=False)
        )


def _rename_in_plan_text(text: str, old_key: str, new_name: str) -> str:
    from parsers.workout import _parse_plan_exercise_line

    lines = (text or "").split("\n")
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped:
            continue
        name = _parse_plan_exercise_line(stripped).get("name") or ""
        renamed = _renamed(name, old_key, new_name) if name else None
        if renamed is not None and stripped.startswith(name):
            lead = raw[:len(raw) - len(raw.lstrip())]
            lines[i] = lead + renamed + stripped[len(name):]
    return "\n".join(lines)


def _rename_in_rep_text(text: str, old_key: str, new_name: str) -> str:
    """Rename in rep ranges. If the new name already has a range there, that one is kept."""
    order: List[str] = []
    slots: Dict[str, Dict] = {}
    for name, value in parse_rep_entries(text):
        new = _renamed(name, old_key, new_name)
        key = normalize_exercise_name(new or name)
        if key not in slots:
            order.append(key)
            slots[key] = {"name": new or name, "own": None, "moved": None}
        slot = slots[key]
        if new is not None:
            if value:
                slot["moved"] = value
        else:
            slot["name"] = name
            if value or slot["own"] is None:
                slot["own"] = value
    return rep_entries_to_text([(slots[k]["name"], slots[k]["own"] or slots[k]["moved"] or "") for k in order])


def _rename_for_user(db_session, user, old_name: str, new_name: str) -> None:
    """The rename, for one user's data. Doesn't commit."""
    from services.logging import refresh_best_lift_pointers

    old_key = normalize_exercise_name(old_name)
    pairs = _name_pairs(db_session, user.id, old_name, new_name)

    moved_ids: List[int] = []
    for old, new in pairs:
        logs = db_session.query(WorkoutLog).filter(WorkoutLog.user_id == user.id, WorkoutLog.exercise == old).all()
        for log in logs:
            log.exercise = new
            log.exercise_string = _rename_in_exercise_string(log.exercise_string or "", old, new)
            moved_ids.append(log.id)

        # Best lifts are worked out again below, across both histories.
        new_lifts = db_session.query(Lift).filter(Lift.user_id == user.id, Lift.exercise == new).count()
        for lift in db_session.query(Lift).filter(Lift.user_id == user.id, Lift.exercise == old).all():
            if new_lifts:
                db_session.delete(lift)
            else:
                lift.exercise = new
                new_lifts = 1

        _move_preferences(db_session, user.id, old, new)

    plan = db_session.query(Plan).filter_by(user_id=user.id).first()
    if plan is not None and plan.text_content:
        plan.text_content = _rename_in_plan_text(plan.text_content, old_key, new_name)
    rep = db_session.query(RepRange).filter_by(user_id=user.id).first()
    if rep is not None and rep.text_content:
        rep.text_content = _rename_in_rep_text(rep.text_content, old_key, new_name)

    _remember(db_session, user.id, old_name, new_name, moved_ids)
    db_session.flush()
    refresh_best_lift_pointers(db_session, user, [new for _, new in pairs])


def _has_history(db_session, user_id: int, old_key: str) -> bool:
    return any(_split_tag(n, old_key) is not None for n in _logged_names(db_session, user_id))


def _followers(db_session, user) -> List:
    """Who takes their exercise names from this user's plan or rep ranges."""
    from models import User
    from services.retrieve import is_plan_owner

    if not is_plan_owner(db_session, user):
        return []
    return (
        db_session.query(User)
        .filter(User.id != user.id)
        .filter((User.follow_admin_plan.is_(True)) | (User.follow_admin_exercises.is_(True)))
        .all()
    )


def rename_exercise(db_session, user, old_name: str, new_name: str) -> Dict:
    """Rename an exercise everywhere in this user's data, and for everyone following their plan,
    since their names come from it. Commits. Returns rename_preview's numbers from before."""
    error = validate_rename(old_name, new_name)
    if error:
        raise ValueError(error)
    old_name, new_name = _clean(old_name), _clean(new_name)
    summary = rename_preview(db_session, user, old_name, new_name)
    _rename_for_user(db_session, user, old_name, new_name)
    old_key = normalize_exercise_name(old_name)
    for follower in _followers(db_session, user):
        # A follower who already renamed it their own way has nothing under the old name.
        if _has_history(db_session, follower.id, old_key):
            _rename_for_user(db_session, follower, old_name, new_name)
    db_session.commit()
    return summary


def catch_up_with_followed_renames(db_session, user) -> bool:
    """Someone starting to follow gets the renames the plan owner made before: their history
    under an old name moves to the name the plan uses now. Doesn't commit."""
    from services.retrieve import _get_admin_user

    owner = _get_admin_user(db_session)
    if owner is None or owner.id == user.id:
        return False
    renames = (
        db_session.query(ExerciseRename)
        .filter(ExerciseRename.user_id == owner.id)
        .order_by(ExerciseRename.created_at.asc())
        .all()
    )
    changed = False
    for r in renames:
        if _has_history(db_session, user.id, r.old_key) and not validate_rename(r.old_name, r.new_name):
            _rename_for_user(db_session, user, r.old_name, r.new_name)
            changed = True
    return changed


def exercise_names_for_rename(db_session, user) -> List[str]:
    """Every name the user has logged, for picking the one to rename."""
    return sorted(set(_logged_names(db_session, user.id)), key=str.lower)


# --- Noticing a rename when the plan or rep ranges are saved ---

def plan_exercise_names(text: str) -> List[str]:
    """The exercises in a plan, in order (a name once per place it appears)."""
    from list_of_exercise import get_workout_days
    from parsers.workout import _parse_plan_exercise_line

    names = []
    for days in (get_workout_days(text or "").get("workout") or {}).values():
        for lines in days.values():
            for line in lines:
                name = _parse_plan_exercise_line(line).get("name") or ""
                if name:
                    names.append(name)
    return names


def rep_exercise_names(text: str) -> List[str]:
    return [name for name, _ in parse_rep_entries(text)]


def detect_renames(db_session, user, old_names: List[str], new_names: List[str]) -> List[Tuple[str, str]]:
    """Names that look renamed by an edit: gone from the text, with history of their own, and
    replaced by a new name in the same spot (or the only name gone, for the only name added).
    Only a guess; the user is asked before anything moves."""
    old_keys = [normalize_exercise_name(n) for n in old_names]
    new_keys = [normalize_exercise_name(n) for n in new_names]
    old_set, new_set = set(old_keys), set(new_keys)
    logged = {normalize_exercise_name(n) for n in _logged_names(db_session, user.id)}
    gone = {k for k in old_set - new_set if k in logged}
    added = {k for k in new_set - old_set if k}
    display_old = dict(zip(old_keys, old_names))
    display_new = dict(zip(new_keys, new_names))

    pairs: List[Tuple[str, str]] = []
    matcher = SequenceMatcher(a=old_keys, b=new_keys, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace" or i2 - i1 != j2 - j1:
            continue
        for old_key, new_key in zip(old_keys[i1:i2], new_keys[j1:j2]):
            if old_key in gone and new_key in added:
                pairs.append((display_old[old_key], display_new[new_key]))
                gone.discard(old_key)
                added.discard(new_key)
    # One name out and one in across the whole save, even if it moved (rep ranges group by muscle).
    if not pairs and len(old_set - new_set) == 1 and len(added) == 1 and gone:
        pairs.append((display_old[gone.pop()], display_new[added.pop()]))
    return pairs


def rename_questions(db_session, user, pairs) -> List[Dict]:
    """Pending rename questions still worth asking, with how much history each would keep."""
    questions = []
    for old, new in pairs or []:
        if validate_rename(old, new):
            continue
        preview = rename_preview(db_session, user, old, new)
        if preview["moving"]:
            questions.append(preview)
    return questions
