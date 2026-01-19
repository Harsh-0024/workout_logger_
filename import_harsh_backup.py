import argparse
import json
import os
from datetime import datetime
from typing import Optional

from models import Lift, Plan, RepRange, Session, User, UserRole, WorkoutLog
from services.auth import AuthService


def _parse_datetime(value):
    if not value:
        return None

    if isinstance(value, datetime):
        return value

    formats = [
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            pass

    return None


def _parse_sets_json(value):
    if value is None:
        return {"weights": [], "reps": []}

    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return {"weights": [], "reps": []}
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return {"weights": [], "reps": []}

    return {"weights": [], "reps": []}


def _extract_json_array_from_dump(dump_text: str, file_path_marker: str):
    marker = f"FILE PATH: {file_path_marker}"
    start = dump_text.find(marker)
    if start == -1:
        raise RuntimeError(f"Could not find section marker: {marker}")

    array_start = dump_text.find("[", start)
    if array_start == -1:
        raise RuntimeError(f"Could not find JSON array start after: {marker}")

    next_section = dump_text.find("==============================", array_start)
    if next_section == -1:
        chunk = dump_text[array_start:].strip()
    else:
        chunk = dump_text[array_start:next_section].strip()

    array_end = chunk.rfind("]")
    if array_end == -1:
        raise RuntimeError(f"Could not find JSON array end for: {marker}")

    json_str = chunk[: array_end + 1]
    return json.loads(json_str)


def _get_or_create_target_user(session, *, username: str, email: str, password: Optional[str], dry_run: bool):
    user = (
        session.query(User)
        .filter((User.email == email.lower()) | (User.username == username.lower()))
        .first()
    )

    created = False
    if not user:
        created = True
        user = User(username=username.lower())
        session.add(user)

    user.username = username.lower()
    user.email = email.lower()
    user.role = UserRole.USER
    user.is_verified = True
    user.verification_token = None
    user.verification_token_expires = None
    user.updated_at = datetime.now()
    if not user.created_at:
        user.created_at = datetime.now()

    if password:
        user.password_hash = AuthService.hash_password(password)

    if not dry_run:
        session.flush()

    return user, created


def _upsert_lift(session, *, user_id: int, exercise: str, best_string: str, sets_json: dict, updated_at: datetime | None, dry_run: bool):
    lift = session.query(Lift).filter_by(user_id=user_id, exercise=exercise).first()

    if not lift:
        lift = Lift(user_id=user_id, exercise=exercise)
        session.add(lift)

    current_dt = getattr(lift, "updated_at", None)

    should_update = False
    if not lift.best_string:
        should_update = True
    elif updated_at and current_dt and updated_at > current_dt:
        should_update = True
    elif updated_at and not current_dt:
        should_update = True

    if should_update:
        lift.best_string = best_string or ""
        lift.sets_json = sets_json or {"weights": [], "reps": []}
        lift.updated_at = updated_at or datetime.now()


def _upsert_plan(session, *, user_id: int, text_content: str, updated_at: datetime | None, dry_run: bool):
    plan = session.query(Plan).filter_by(user_id=user_id).first()
    if not plan:
        plan = Plan(user_id=user_id)
        session.add(plan)

    plan.text_content = text_content or ""
    plan.updated_at = updated_at or datetime.now()


def _upsert_rep_ranges(session, *, user_id: int, text_content: str, updated_at: datetime | None, dry_run: bool):
    rr = session.query(RepRange).filter_by(user_id=user_id).first()
    if not rr:
        rr = RepRange(user_id=user_id)
        session.add(rr)

    rr.text_content = text_content or ""
    rr.updated_at = updated_at or datetime.now()


def _upsert_workout_log(
    session,
    *,
    user_id: int,
    date: Optional[datetime],
    exercise: str,
    top_weight: Optional[float],
    top_reps: Optional[int],
    estimated_1rm: Optional[float],
    dry_run: bool,
):
    if not date:
        return

    existing = (
        session.query(WorkoutLog)
        .filter(
            WorkoutLog.user_id == user_id,
            WorkoutLog.date == date,
            WorkoutLog.exercise == exercise,
            WorkoutLog.top_weight == top_weight,
            WorkoutLog.top_reps == top_reps,
        )
        .first()
    )

    if existing:
        return

    session.add(
        WorkoutLog(
            user_id=user_id,
            date=date,
            exercise=exercise,
            top_weight=top_weight,
            top_reps=top_reps,
            estimated_1rm=estimated_1rm,
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", default="database_data.txt")
    parser.add_argument("--username", default="harsh_24")
    parser.add_argument("--email", default="harsh242042004@gmail.com")
    parser.add_argument("--password", default=os.environ.get("IMPORT_PASSWORD"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(args.dump, "r", encoding="utf-8") as f:
        dump_text = f.read()

    users = _extract_json_array_from_dump(dump_text, "the_lifts/users.json")
    harsh_user = next((u for u in users if str(u.get("username", "")).lower() == "harsh"), None)
    if not harsh_user:
        raise RuntimeError("Could not find user 'harsh' in the backup users.json")

    old_user_id = int(harsh_user["id"])

    lifts = _extract_json_array_from_dump(dump_text, "the_lifts/lifts.json")
    workout_logs = _extract_json_array_from_dump(dump_text, "the_lifts/workout_logs.json")

    user_plans = _extract_json_array_from_dump(dump_text, "the_lifts/user_plans.json")
    harsh_plan = next((p for p in user_plans if str(p.get("username", "")).lower() == "harsh"), None)

    user_rep_ranges = _extract_json_array_from_dump(dump_text, "the_lifts/user_rep_ranges.json")
    harsh_rep = next((r for r in user_rep_ranges if str(r.get("username", "")).lower() == "harsh"), None)

    harsh_lifts = []
    try:
        harsh_lifts = _extract_json_array_from_dump(dump_text, "the_lifts/harsh_lifts.json")
    except Exception:
        harsh_lifts = []

    session = Session()
    try:
        target_user, created = _get_or_create_target_user(
            session,
            username=args.username,
            email=args.email,
            password=args.password,
            dry_run=args.dry_run,
        )

        new_user_id = target_user.id if target_user.id else None
        if args.dry_run:
            print(f"[DRY RUN] Would {'create' if created else 'update'} user: username={args.username} email={args.email}")
        else:
            session.flush()
            new_user_id = target_user.id
            print(f"User ready: id={new_user_id} username={target_user.username} email={target_user.email}")

        if not new_user_id:
            raise RuntimeError("Could not determine target user id")

        lifts_for_harsh = [row for row in lifts if int(row.get("user_id", -1)) == old_user_id]

        imported_lifts = 0
        for row in lifts_for_harsh:
            exercise = str(row.get("exercise", "")).strip()
            if not exercise:
                continue
            best_string = str(row.get("best_string", "") or "")
            sets_json = _parse_sets_json(row.get("sets_json"))
            updated_at = _parse_datetime(row.get("updated_at"))
            _upsert_lift(
                session,
                user_id=new_user_id,
                exercise=exercise,
                best_string=best_string,
                sets_json=sets_json,
                updated_at=updated_at,
                dry_run=args.dry_run,
            )
            imported_lifts += 1

        for row in harsh_lifts:
            exercise = str(row.get("exercise", "")).strip()
            if not exercise:
                continue
            best_string = str(row.get("best_string", "") or "")
            sets_json = _parse_sets_json(row.get("sets_json"))
            updated_at = _parse_datetime(row.get("updated_at"))
            _upsert_lift(
                session,
                user_id=new_user_id,
                exercise=exercise,
                best_string=best_string,
                sets_json=sets_json,
                updated_at=updated_at,
                dry_run=args.dry_run,
            )

        if harsh_plan:
            _upsert_plan(
                session,
                user_id=new_user_id,
                text_content=str(harsh_plan.get("plan_text", "") or ""),
                updated_at=_parse_datetime(harsh_plan.get("updated_at")),
                dry_run=args.dry_run,
            )

        if harsh_rep:
            _upsert_rep_ranges(
                session,
                user_id=new_user_id,
                text_content=str(harsh_rep.get("rep_text", "") or ""),
                updated_at=_parse_datetime(harsh_rep.get("updated_at")),
                dry_run=args.dry_run,
            )

        logs_for_harsh = [row for row in workout_logs if int(row.get("user_id", -1)) == old_user_id]
        imported_logs = 0
        for row in logs_for_harsh:
            exercise = str(row.get("exercise", "")).strip()
            if not exercise:
                continue
            date = _parse_datetime(row.get("date"))
            top_weight = row.get("top_weight")
            top_reps = row.get("top_reps")
            estimated_1rm = row.get("estimated_1rm")

            try:
                top_weight = float(top_weight) if top_weight is not None else None
            except Exception:
                top_weight = None

            try:
                top_reps = int(float(top_reps)) if top_reps is not None else None
            except Exception:
                top_reps = None

            try:
                estimated_1rm = float(estimated_1rm) if estimated_1rm is not None else None
            except Exception:
                estimated_1rm = None

            _upsert_workout_log(
                session,
                user_id=new_user_id,
                date=date,
                exercise=exercise,
                top_weight=top_weight,
                top_reps=top_reps,
                estimated_1rm=estimated_1rm,
                dry_run=args.dry_run,
            )
            imported_logs += 1

        if args.dry_run:
            session.rollback()
            print(f"[DRY RUN] lifts rows processed: {imported_lifts}")
            print(f"[DRY RUN] workout_logs rows processed: {imported_logs}")
            if harsh_plan:
                print("[DRY RUN] plan would be upserted")
            if harsh_rep:
                print("[DRY RUN] rep_ranges would be upserted")
        else:
            session.commit()
            print(f"Imported lifts rows processed: {imported_lifts}")
            print(f"Imported workout_logs rows processed: {imported_logs}")
            print("Done.")

    finally:
        session.close()


if __name__ == "__main__":
    main()
