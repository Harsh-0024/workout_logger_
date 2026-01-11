from sqlalchemy import text
from models import User, Lift, Plan, RepRange, initialize_database, Session
from datetime import datetime


def parse_date(date_val):
    """
    Helper: Converts SQLite string dates back to Python Datetime objects.
    Handles formats with and without microseconds.
    """
    if not date_val:
        return datetime.now()

    if isinstance(date_val, datetime):
        return date_val

    # If it's a string, try to parse it
    try:
        # Try format with microseconds: "2026-01-11 01:12:49.408119"
        return datetime.strptime(str(date_val), "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        try:
            # Try format without microseconds: "2026-01-11 01:12:49"
            return datetime.strptime(str(date_val), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            # Fallback if format is weird
            return datetime.now()


def run_migration():
    session = Session()
    log = []

    try:
        log.append("--- STARTING MIGRATION ---")

        # 1. Initialize New Schema
        initialize_database()

        # 2. Get Users
        harsh = session.query(User).filter_by(username='harsh').first()
        apurva = session.query(User).filter_by(username='apurva').first()

        # 3. Migrate Harsh
        try:
            old_lifts = session.execute(text("SELECT * FROM harsh_lifts")).fetchall()
            count = 0
            for row in old_lifts:
                # Check duplication based on user_id + exercise
                exists = session.query(Lift).filter_by(user_id=harsh.id, exercise=row.exercise).first()
                if not exists:
                    new_lift = Lift(
                        user_id=harsh.id,
                        exercise=row.exercise,
                        best_string=row.best_string,
                        sets_json=row.sets_json,
                        # FIX: Parse the date string into an object
                        updated_at=parse_date(row.updated_at)
                    )
                    session.add(new_lift)
                    count += 1
            log.append(f"Migrated {count} lifts for Harsh")

            # Migrate Plan
            old_plan = session.execute(text("SELECT plan_text FROM user_plans WHERE username='harsh'")).fetchone()
            if old_plan:
                current_plan = session.query(Plan).filter_by(user_id=harsh.id).first()
                if current_plan:
                    current_plan.text_content = old_plan[0]
                    log.append("Migrated Plan for Harsh")

            # Migrate Reps
            old_reps = session.execute(text("SELECT rep_text FROM user_rep_ranges WHERE username='harsh'")).fetchone()
            if old_reps:
                current_reps = session.query(RepRange).filter_by(user_id=harsh.id).first()
                if current_reps:
                    current_reps.text_content = old_reps[0]
                    log.append("Migrated Reps for Harsh")

        except Exception as e:
            log.append(f"Skipping Harsh table (maybe empty): {e}")

        # 4. Migrate Apurva
        try:
            old_lifts = session.execute(text("SELECT * FROM apurva_lifts")).fetchall()
            count = 0
            for row in old_lifts:
                exists = session.query(Lift).filter_by(user_id=apurva.id, exercise=row.exercise).first()
                if not exists:
                    new_lift = Lift(
                        user_id=apurva.id,
                        exercise=row.exercise,
                        best_string=row.best_string,
                        sets_json=row.sets_json,
                        # FIX: Parse the date string into an object
                        updated_at=parse_date(row.updated_at)
                    )
                    session.add(new_lift)
                    count += 1
            log.append(f"Migrated {count} lifts for Apurva")

            old_plan = session.execute(text("SELECT plan_text FROM user_plans WHERE username='apurva'")).fetchone()
            if old_plan:
                current_plan = session.query(Plan).filter_by(user_id=apurva.id).first()
                if current_plan:
                    current_plan.text_content = old_plan[0]
                    log.append("Migrated Plan for Apurva")

            old_reps = session.execute(text("SELECT rep_text FROM user_rep_ranges WHERE username='apurva'")).fetchone()
            if old_reps:
                current_reps = session.query(RepRange).filter_by(user_id=apurva.id).first()
                if current_reps:
                    current_reps.text_content = old_reps[0]
                    log.append("Migrated Reps for Apurva")

        except Exception as e:
            log.append(f"Skipping Apurva table: {e}")

        session.commit()
        log.append("--- MIGRATION COMPLETE ---")

    except Exception as e:
        session.rollback()
        log.append(f"CRITICAL FAILURE: {e}")
    finally:
        session.close()

    return "\n".join(log)