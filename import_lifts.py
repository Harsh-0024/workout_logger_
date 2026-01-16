"""
Script to import best lifts from CSV files into the database.
"""
import csv
import json
from datetime import datetime
from models import Session, User, Lift
from utils.logger import logger


def parse_sets_json(sets_json_str):
    """Parse sets_json string from CSV."""
    if not sets_json_str or sets_json_str.strip() == '':
        return {"weights": [], "reps": []}
    
    try:
        # Handle both string and already-parsed JSON
        if isinstance(sets_json_str, str):
            return json.loads(sets_json_str)
        return sets_json_str
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"Could not parse sets_json: {sets_json_str}")
        return {"weights": [], "reps": []}


def parse_date(date_str):
    """Parse date string from CSV."""
    if not date_str:
        return datetime.now()
    
    if isinstance(date_str, datetime):
        return date_str
    
    # Try various date formats
    formats = [
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y"
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(str(date_str), fmt)
        except ValueError:
            continue
    
    logger.warning(f"Could not parse date: {date_str}, using current date")
    return datetime.now()


def import_lifts_from_csv(csv_path, user_id):
    """Import lifts from a CSV file for a specific user."""
    session = Session()
    imported_count = 0
    updated_count = 0
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                exercise_name = row.get('exercise', '').strip()
                if not exercise_name:
                    continue
                
                best_string = row.get('best_string', '').strip()
                sets_json_str = row.get('sets_json', '')
                updated_at_str = row.get('updated_at', '')
                
                # Parse data
                sets_json = parse_sets_json(sets_json_str)
                updated_at = parse_date(updated_at_str)
                
                # Find or create lift record
                lift = session.query(Lift).filter_by(
                    user_id=user_id,
                    exercise=exercise_name
                ).first()
                
                if lift:
                    # Update existing record if this one is better or more recent
                    should_update = False
                    
                    # Handle sets_json - might be dict or string
                    current_sets_json = lift.sets_json
                    if isinstance(current_sets_json, str):
                        try:
                            current_sets_json = json.loads(current_sets_json)
                        except:
                            current_sets_json = {}
                    
                    current_weights = current_sets_json.get('weights', []) if isinstance(current_sets_json, dict) else []
                    
                    # If current record has no data, update it
                    if not lift.best_string or not current_weights:
                        should_update = True
                    # If new record has data and current doesn't, update
                    elif best_string and sets_json.get('weights'):
                        # If new date is more recent, update
                        if updated_at > lift.updated_at:
                            should_update = True
                        # If dates are same but new has better data, update
                        elif updated_at == lift.updated_at and best_string:
                            should_update = True
                    
                    if should_update:
                        lift.best_string = best_string
                        lift.sets_json = sets_json
                        lift.updated_at = updated_at
                        updated_count += 1
                        logger.info(f"Updated lift: {exercise_name} for user {user_id}")
                else:
                    # Create new record
                    lift = Lift(
                        user_id=user_id,
                        exercise=exercise_name,
                        best_string=best_string,
                        sets_json=sets_json,
                        updated_at=updated_at
                    )
                    session.add(lift)
                    imported_count += 1
                    logger.info(f"Imported new lift: {exercise_name} for user {user_id}")
        
        session.commit()
        logger.info(f"Import complete: {imported_count} new, {updated_count} updated")
        return imported_count, updated_count
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error importing from {csv_path}: {e}", exc_info=True)
        raise
    finally:
        session.close()


def import_workout_logs_from_csv(csv_path):
    """Import workout logs from CSV."""
    from models import WorkoutLog
    
    session = Session()
    imported_count = 0
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                user_id = int(row.get('user_id', 0))
                if not user_id:
                    continue
                
                exercise_name = row.get('exercise', '').strip()
                if not exercise_name:
                    continue
                
                date_str = row.get('date', '')
                top_weight = row.get('top_weight', '')
                top_reps = row.get('top_reps', '')
                estimated_1rm = row.get('estimated_1rm', '')
                
                # Parse data
                workout_date = parse_date(date_str)
                
                try:
                    top_weight = float(top_weight) if top_weight else None
                except (ValueError, TypeError):
                    top_weight = None
                
                try:
                    top_reps = int(float(top_reps)) if top_reps else None
                except (ValueError, TypeError):
                    top_reps = None
                
                try:
                    estimated_1rm = float(estimated_1rm) if estimated_1rm else None
                except (ValueError, TypeError):
                    estimated_1rm = None
                
                # Check if log already exists (more lenient check - same user, exercise, date, and similar weight)
                existing = session.query(WorkoutLog).filter(
                    WorkoutLog.user_id == user_id,
                    WorkoutLog.exercise == exercise_name,
                    WorkoutLog.date == workout_date
                ).first()
                
                if not existing:
                    log = WorkoutLog(
                        user_id=user_id,
                        date=workout_date,
                        exercise=exercise_name,
                        top_weight=top_weight,
                        top_reps=top_reps,
                        estimated_1rm=estimated_1rm
                    )
                    session.add(log)
                    imported_count += 1
        
        session.commit()
        logger.info(f"Imported {imported_count} workout logs")
        return imported_count
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error importing workout logs: {e}", exc_info=True)
        raise
    finally:
        session.close()


def import_plans_from_csv(csv_path):
    """Import workout plans from CSV."""
    from models import Plan
    
    session = Session()
    updated_count = 0
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                user_id = int(row.get('user_id', 0))
                if not user_id:
                    continue
                
                text_content = row.get('text_content', '').strip()
                updated_at_str = row.get('updated_at', '')
                
                plan = session.query(Plan).filter_by(user_id=user_id).first()
                
                if plan:
                    plan.text_content = text_content
                    plan.updated_at = parse_date(updated_at_str)
                    updated_count += 1
                else:
                    plan = Plan(
                        user_id=user_id,
                        text_content=text_content,
                        updated_at=parse_date(updated_at_str)
                    )
                    session.add(plan)
                    updated_count += 1
        
        session.commit()
        logger.info(f"Imported/updated {updated_count} plans")
        return updated_count
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error importing plans: {e}", exc_info=True)
        raise
    finally:
        session.close()


def import_rep_ranges_from_csv(csv_path):
    """Import rep ranges from CSV."""
    from models import RepRange
    
    session = Session()
    updated_count = 0
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                user_id = int(row.get('user_id', 0))
                if not user_id:
                    continue
                
                text_content = row.get('text_content', '').strip()
                updated_at_str = row.get('updated_at', '')
                
                rep_range = session.query(RepRange).filter_by(user_id=user_id).first()
                
                if rep_range:
                    rep_range.text_content = text_content
                    rep_range.updated_at = parse_date(updated_at_str)
                    updated_count += 1
                else:
                    rep_range = RepRange(
                        user_id=user_id,
                        text_content=text_content,
                        updated_at=parse_date(updated_at_str)
                    )
                    session.add(rep_range)
                    updated_count += 1
        
        session.commit()
        logger.info(f"Imported/updated {updated_count} rep ranges")
        return updated_count
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error importing rep ranges: {e}", exc_info=True)
        raise
    finally:
        session.close()


def main():
    """Main import function."""
    import os
    
    # Path to CSV files
    csv_dir = '/Users/harsh24/Documents/lifts'
    
    logger.info("Starting data import...")
    
    # Get user IDs
    session = Session()
    harsh = session.query(User).filter_by(username='harsh').first()
    apurva = session.query(User).filter_by(username='apurva').first()
    session.close()
    
    if not harsh or not apurva:
        logger.error("Users not found. Please initialize database first.")
        return
    
    try:
        # Import lifts from main lifts.csv (has both users with user_id column)
        lifts_csv = os.path.join(csv_dir, 'lifts.csv')
        if os.path.exists(lifts_csv):
            logger.info("Importing from lifts.csv...")
            session = Session()
            with open(lifts_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        user_id = int(row.get('user_id', 0))
                        if user_id not in [harsh.id, apurva.id]:
                            continue
                        
                        exercise_name = row.get('exercise', '').strip()
                        if not exercise_name:
                            continue
                        
                        best_string = row.get('best_string', '').strip()
                        sets_json_str = row.get('sets_json', '')
                        updated_at_str = row.get('updated_at', '')
                        
                        sets_json = parse_sets_json(sets_json_str)
                        updated_at = parse_date(updated_at_str)
                        
                        lift = session.query(Lift).filter_by(
                            user_id=user_id,
                            exercise=exercise_name
                        ).first()
                        
                        if lift:
                            if best_string and sets_json.get('weights'):
                                if not lift.best_string or updated_at >= lift.updated_at:
                                    lift.best_string = best_string
                                    lift.sets_json = sets_json
                                    lift.updated_at = updated_at
                        else:
                            lift = Lift(
                                user_id=user_id,
                                exercise=exercise_name,
                                best_string=best_string,
                                sets_json=sets_json,
                                updated_at=updated_at
                            )
                            session.add(lift)
                    except Exception as e:
                        logger.warning(f"Error processing row: {e}")
                        continue
            
            session.commit()
            session.close()
            logger.info("Completed importing from lifts.csv")
        
        # Import from user-specific files
        harsh_lifts_csv = os.path.join(csv_dir, 'harsh_lifts.csv')
        if os.path.exists(harsh_lifts_csv):
            logger.info("Importing Harsh's lifts...")
            import_lifts_from_csv(harsh_lifts_csv, harsh.id)
        
        apurva_lifts_csv = os.path.join(csv_dir, 'apurva_lifts.csv')
        if os.path.exists(apurva_lifts_csv):
            logger.info("Importing Apurva's lifts...")
            import_lifts_from_csv(apurva_lifts_csv, apurva.id)
        
        # Import best_lifts.csv (appears to be Harsh's best lifts based on data analysis)
        # Only import for harsh to avoid cross-contamination
        best_lifts_csv = os.path.join(csv_dir, 'best_lifts.csv')
        if os.path.exists(best_lifts_csv):
            logger.info("Importing best lifts for Harsh (prioritizing best performance)...")
            session = Session()
            updated_count = 0
            from services.helpers import get_set_stats
            
            with open(best_lifts_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    exercise_name = row.get('exercise', '').strip()
                    if not exercise_name:
                        continue
                    
                    best_string = row.get('best_string', '').strip()
                    sets_json_str = row.get('sets_json', '')
                    updated_at_str = row.get('updated_at', '')
                    
                    sets_json = parse_sets_json(sets_json_str)
                    updated_at = parse_date(updated_at_str)
                    
                    # Only process if we have valid data
                    if not best_string or not sets_json.get('weights'):
                        continue
                    
                    # Calculate 1RM from sets_json to compare
                    new_peak, _, _ = get_set_stats(sets_json)
                    
                    # Only update Harsh's lifts (best_lifts.csv appears to be his data)
                    lift = session.query(Lift).filter_by(
                        user_id=harsh.id,
                        exercise=exercise_name
                    ).first()
                    
                    if lift:
                        # Handle existing sets_json
                        current_sets_json = lift.sets_json
                        if isinstance(current_sets_json, str):
                            try:
                                current_sets_json = json.loads(current_sets_json)
                            except:
                                current_sets_json = {}
                        
                        # Calculate current 1RM
                        current_peak, _, _ = get_set_stats(current_sets_json)
                        
                        # Update if new data is better (higher 1RM) or if current has no data
                        if not lift.best_string or new_peak > current_peak or (new_peak == current_peak and updated_at >= lift.updated_at):
                            lift.best_string = best_string
                            lift.sets_json = sets_json
                            lift.updated_at = updated_at
                            updated_count += 1
                    else:
                        # Create new record for harsh
                        lift = Lift(
                            user_id=harsh.id,
                            exercise=exercise_name,
                            best_string=best_string,
                            sets_json=sets_json,
                            updated_at=updated_at
                        )
                        session.add(lift)
                        updated_count += 1
            
            session.commit()
            session.close()
            logger.info(f"Updated {updated_count} lifts from best_lifts.csv for Harsh")
        
        # Import workout logs
        workout_logs_csv = os.path.join(csv_dir, 'workout_logs.csv')
        if os.path.exists(workout_logs_csv):
            logger.info("Importing workout logs...")
            import_workout_logs_from_csv(workout_logs_csv)
        
        # Import plans
        plans_csv = os.path.join(csv_dir, 'plans.csv')
        if os.path.exists(plans_csv):
            logger.info("Importing plans...")
            import_plans_from_csv(plans_csv)
        
        # Import rep ranges
        rep_ranges_csv = os.path.join(csv_dir, 'rep_ranges.csv')
        if os.path.exists(rep_ranges_csv):
            logger.info("Importing rep ranges...")
            import_rep_ranges_from_csv(rep_ranges_csv)
        
        logger.info("Data import completed successfully!")
        
    except Exception as e:
        logger.error(f"Import failed: {e}", exc_info=True)
        raise


if __name__ == '__main__':
    from models import initialize_database
    initialize_database()
    main()
