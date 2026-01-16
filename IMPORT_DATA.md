# Data Import Guide

This document explains how to import workout data from CSV files into the database.

## Quick Import

To import all data from the CSV files in `/Users/harsh24/Documents/lifts/`:

```bash
python3 import_lifts.py
```

## What Gets Imported

The import script processes the following CSV files:

1. **lifts.csv** - Best lifts for both users (has `user_id` column)
2. **harsh_lifts.csv** - Harsh's specific lift data
3. **apurva_lifts.csv** - Apurva's specific lift data
4. **best_lifts.csv** - Best performance data (imported for Harsh)
5. **workout_logs.csv** - Historical workout data
6. **plans.csv** - Workout plans for both users
7. **rep_ranges.csv** - Exercise rep ranges for both users

## Import Logic

- **Lifts**: The script prioritizes the best performance data based on estimated 1RM
- **Workout Logs**: Duplicate entries (same user, exercise, date) are automatically filtered
- **Plans & Rep Ranges**: Existing data is updated with new data from CSV

## Data Verification

After importing, verify the data:

```python
from models import Session, User, Lift, WorkoutLog

session = Session()
harsh = session.query(User).filter_by(username='harsh').first()
apurva = session.query(User).filter_by(username='apurva').first()

print(f"Harsh lifts: {session.query(Lift).filter_by(user_id=harsh.id).count()}")
print(f"Apurva lifts: {session.query(Lift).filter_by(user_id=apurva.id).count()}")
print(f"Harsh logs: {session.query(WorkoutLog).filter_by(user_id=harsh.id).count()}")
print(f"Apurva logs: {session.query(WorkoutLog).filter_by(user_id=apurva.id).count()}")
```

## Notes

- The import script is idempotent - you can run it multiple times safely
- It will update existing records if the new data is better (higher 1RM) or more recent
- Duplicate workout logs are automatically prevented
