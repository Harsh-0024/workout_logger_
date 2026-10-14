"""Read-only report: which of each user's exercise names would start counting as one exercise once
a word written as one or two ("Push Down" / "Pushdown") is matched. Changes nothing; it only
writes lines to the log, so the list can be checked on real data before the matching is turned on.
"""
from collections import defaultdict
from typing import Dict, List

from models import Plan, RepRange, User, WorkoutLog
from services.rep_ranges import parse_rep_entries
from utils.logger import logger


def _key(name: str, vocabulary=None) -> str:
    # The key Stats groups under, with and without the new matching.
    from services.stats import _normalize_exercise_name
    return _normalize_exercise_name(name, vocabulary)


def new_merges_for_names(names) -> List[List[str]]:
    """Groups of names that are separate today and one exercise with the new matching."""
    from services.stats import stats_vocabulary

    names = sorted({n for n in names if n and _key(n)})
    vocabulary = stats_vocabulary(names)
    groups: Dict[str, List[str]] = defaultdict(list)
    for name in names:
        groups[_key(name, vocabulary)].append(name)
    merges = []
    for members in groups.values():
        if len({_key(n) for n in members}) > 1:
            merges.append(members)
    return merges


def report_new_merges(db_session) -> int:
    """Log every name that would newly merge, per user. Returns how many groups were found."""
    from services.exercise_rename import plan_exercise_names

    found = 0
    for user in db_session.query(User).order_by(User.id).all():
        names = {r[0] for r in db_session.query(WorkoutLog.exercise).filter(WorkoutLog.user_id == user.id).distinct()}
        plan = db_session.query(Plan).filter_by(user_id=user.id).first()
        names.update(plan_exercise_names(plan.text_content if plan else ""))
        rep = db_session.query(RepRange).filter_by(user_id=user.id).first()
        names.update(n for n, _ in parse_rep_entries(rep.text_content if rep else ""))
        for members in new_merges_for_names(names):
            found += 1
            logger.info("Compound-name report: user %s would group %s", user.username, " + ".join(repr(m) for m in members))
    logger.info("Compound-name report: %d group(s) would newly merge", found)
    return found
