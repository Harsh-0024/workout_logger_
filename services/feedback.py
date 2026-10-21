"""
Feedback from users to the developer: what they like, what they don't, what could be better,
and features they'd like. Users see what they sent and whether it was read; admins read it all.
"""
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func

from models import Feedback, Session, User

# kind -> (label, short label, Bootstrap icon)
FEEDBACK_KINDS = {
    'like': ('Something I like', 'Like', 'bi-heart'),
    'dislike': ("Something I don't like", "Don't like", 'bi-hand-thumbs-down'),
    'improve': ('Could be better', 'Improve', 'bi-tools'),
    'idea': ('A feature idea', 'Idea', 'bi-lightbulb'),
}
FEEDBACK_STATUSES = ('new', 'seen', 'done')
MAX_MESSAGE_LENGTH = 2000


class FeedbackError(Exception):
    pass


def _when(value: Optional[datetime]) -> str:
    if not value:
        return ''
    now = datetime.now()
    if value.date() == now.date():
        return 'Today, ' + value.strftime('%H:%M')
    if value.year == now.year:
        return f"{value.day} {value.strftime('%b')}"
    return f"{value.day} {value.strftime('%b %Y')}"


def _as_dict(item: Feedback, user: Optional[User] = None) -> Dict:
    label, short, icon = FEEDBACK_KINDS.get(item.kind, FEEDBACK_KINDS['idea'])
    data = {
        'id': item.id,
        'kind': item.kind,
        'kind_label': label,
        'kind_short': short,
        'kind_icon': icon,
        'message': item.message,
        'page': item.page or '',
        'status': item.status,
        'created_at': item.created_at,
        'when': _when(item.created_at),
    }
    if user is not None:
        data.update({
            'user_id': user.id,
            'username': user.username,
            'full_name': user.full_name or '',
            'email': user.email or '',
        })
    return data


class FeedbackService:

    @staticmethod
    def submit(user_id: int, kind: str, message: str, page: str = '') -> Dict:
        kind = (kind or '').strip().lower()
        message = (message or '').strip()
        if kind not in FEEDBACK_KINDS:
            raise FeedbackError("Choose what kind of feedback this is.")
        if not message:
            raise FeedbackError("Write a few words first.")
        if len(message) > MAX_MESSAGE_LENGTH:
            raise FeedbackError(f"Please keep it under {MAX_MESSAGE_LENGTH} characters.")
        page = (page or '').strip()
        # Only a path inside the app, never a full address someone pasted in.
        if not page.startswith('/') or page.startswith('//'):
            page = ''

        session = Session()
        try:
            item = Feedback(user_id=user_id, kind=kind, message=message, page=page[:200] or None, status='new')
            session.add(item)
            session.commit()
            return _as_dict(item)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def for_user(user_id: int, limit: int = 20) -> List[Dict]:
        session = Session()
        try:
            rows = (
                session.query(Feedback)
                .filter(Feedback.user_id == user_id)
                .order_by(Feedback.created_at.desc(), Feedback.id.desc())
                .limit(limit)
                .all()
            )
            return [_as_dict(row) for row in rows]
        finally:
            session.close()

    @staticmethod
    def all_with_users() -> List[Dict]:
        session = Session()
        try:
            rows = (
                session.query(Feedback, User)
                .join(User, User.id == Feedback.user_id)
                .order_by(Feedback.created_at.desc(), Feedback.id.desc())
                .all()
            )
            return [_as_dict(item, user) for item, user in rows]
        finally:
            session.close()

    @staticmethod
    def counts() -> Dict[str, int]:
        session = Session()
        try:
            by_status = dict(
                session.query(Feedback.status, func.count(Feedback.id)).group_by(Feedback.status).all()
            )
            by_kind = dict(
                session.query(Feedback.kind, func.count(Feedback.id)).group_by(Feedback.kind).all()
            )
            counts = {'total': sum(by_status.values())}
            counts.update({status: by_status.get(status, 0) for status in FEEDBACK_STATUSES})
            counts.update({kind: by_kind.get(kind, 0) for kind in FEEDBACK_KINDS})
            return counts
        finally:
            session.close()

    @staticmethod
    def mark_seen(ids: List[int]) -> None:
        """Opening the admin list marks what was new as seen, so the user knows it was read."""
        if not ids:
            return
        session = Session()
        try:
            (
                session.query(Feedback)
                .filter(Feedback.id.in_(ids), Feedback.status == 'new')
                .update({Feedback.status: 'seen'}, synchronize_session=False)
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def set_status(feedback_id: int, status: str) -> None:
        if status not in FEEDBACK_STATUSES:
            raise FeedbackError("Unknown status.")
        session = Session()
        try:
            item = session.get(Feedback, feedback_id)
            if not item:
                raise FeedbackError("That feedback no longer exists.")
            item.status = status
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def delete(feedback_id: int) -> None:
        session = Session()
        try:
            deleted = session.query(Feedback).filter(Feedback.id == feedback_id).delete(synchronize_session=False)
            if not deleted:
                raise FeedbackError("That feedback no longer exists.")
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
