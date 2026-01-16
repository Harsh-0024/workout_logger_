"""
Workout logging service for processing and saving workout data.
"""
from typing import List, Dict
from datetime import datetime
from models import Lift, WorkoutLog
from services.helpers import find_best_match, get_set_stats
from utils.logger import logger


def handle_workout_log(db_session, user, parsed_data: Dict) -> List[Dict]:
    """
    Process and save a workout log.
    
    Args:
        db_session: Database session
        user: User object
        parsed_data: Parsed workout data dictionary
        
    Returns:
        List of summary dictionaries for each exercise
    """
    summary = []
    workout_date = parsed_data.get('date', datetime.now())
    
    if 'exercises' not in parsed_data or not parsed_data['exercises']:
        logger.warning(f"No exercises found in workout data for user {user.username}")
        return summary

    for item in parsed_data["exercises"]:
        ex_name = item['name']
        new_sets = {"weights": item["weights"], "reps": item["reps"]}
        new_str = item['exercise_string']
        is_valid = item.get('valid', True)

        # 1. Calculate Stats for Today
        p_peak, _, p_vol = get_set_stats(new_sets)

        # Find the heaviest weight used today (for history)
        daily_max_weight = 0
        daily_max_reps = 0
        if is_valid and new_sets['weights']:
            daily_max_weight = max(new_sets['weights'])
            # Find reps corresponding to that max weight
            idx = new_sets['weights'].index(daily_max_weight)
            daily_max_reps = new_sets['reps'][idx]

        record = find_best_match(db_session, user.id, ex_name)

        row = {
            'name': ex_name, 'old': '-', 'new': new_str,
            'status': '-', 'class': 'neutral', 'valid': is_valid
        }

        if is_valid:
            # --- SAVE TO HISTORY ---
            try:
                history_log = WorkoutLog(
                    user_id=user.id,
                    date=workout_date,
                    exercise=ex_name,
                    top_weight=daily_max_weight if daily_max_weight > 0 else None,
                    top_reps=daily_max_reps if daily_max_reps > 0 else None,
                    estimated_1rm=p_peak if p_peak > 0 else None
                )
                db_session.add(history_log)
            except Exception as e:
                logger.error(f"Error creating workout log for {ex_name}: {e}", exc_info=True)
                # Continue processing other exercises even if one fails

            if record:
                row['old'] = record.best_string
                r_peak, r_sum, r_vol = get_set_stats(record.sets_json)

                improvement = None
                if p_peak > r_peak:
                    diff = p_peak - r_peak
                    improvement = f"PEAK (+{diff:.1f})"
                elif p_peak == r_peak:
                    _, p_sum, _ = get_set_stats(new_sets)
                    if p_sum > r_sum:
                        improvement = "CONSISTENCY"
                    elif p_vol > r_vol:
                        improvement = "VOLUME"

                if improvement:
                    record.sets_json = new_sets
                    record.best_string = new_str
                    record.updated_at = workout_date
                    row['status'] = improvement
                    row['class'] = 'improved'
            else:
                new_rec = Lift(
                    user_id=user.id, exercise=ex_name, best_string=new_str,
                    sets_json=new_sets, updated_at=workout_date
                )
                db_session.add(new_rec)
                row['old'] = 'First Log'
                row['status'] = "NEW"
                row['class'] = 'new'
        else:
            row['status'] = "ERROR"

        summary.append(row)

    return summary