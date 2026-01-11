import os
from sqlalchemy import create_engine, Column, String, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from sqlalchemy_utils import JSONType
from datetime import datetime

# Import data for seeding
from list_of_exercise import list_of_exercises, DEFAULT_REP_RANGES, HARSH_DEFAULT_PLAN, APURVA_DEFAULT_PLAN

# --- DATABASE SETUP ---
database_url = os.environ.get('DATABASE_URL')

if database_url:
    # Railway-specific fix
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    engine = create_engine(database_url)
else:
    # Local fallback
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(BASE_DIR, "workout.db")
    engine = create_engine(f"sqlite:///{DB_PATH}")

session_factory = sessionmaker(bind=engine)
Session = scoped_session(session_factory)
Base = declarative_base()


# --- MODELS ---

class LiftMixin:
    exercise = Column(String, primary_key=True)
    best_string = Column(String)
    sets_json = Column(JSONType)
    updated_at = Column(DateTime, default=datetime.now)


class HarshLift(Base, LiftMixin):
    __tablename__ = "harsh_lifts"


class ApurvaLift(Base, LiftMixin):
    __tablename__ = "apurva_lifts"


class UserPlan(Base):
    __tablename__ = "user_plans"
    username = Column(String, primary_key=True)
    plan_text = Column(Text)
    updated_at = Column(DateTime, default=datetime.now)


class UserRepRange(Base):
    __tablename__ = "user_rep_ranges"
    username = Column(String, primary_key=True)
    rep_text = Column(Text)
    updated_at = Column(DateTime, default=datetime.now)


# --- FUNCTIONS ---

def get_user_model(user_name):
    """Returns the correct SQL Table Class based on the user name."""
    if user_name == 'apurva':
        return ApurvaLift
    return HarshLift


def initialize_database():
    """Seeds DB with default exercises, plans, and rep ranges if empty."""
    Base.metadata.create_all(engine)
    db_session = Session()
    try:
        # Seed Exercises
        if db_session.query(HarshLift).first() is None:
            for ex in list_of_exercises:
                if not db_session.get(HarshLift, ex):
                    db_session.add(HarshLift(exercise=ex, best_string="", sets_json={"weights": [], "reps": []}))

        if db_session.query(ApurvaLift).first() is None:
            for ex in list_of_exercises:
                if not db_session.get(ApurvaLift, ex):
                    db_session.add(ApurvaLift(exercise=ex, best_string="", sets_json={"weights": [], "reps": []}))

        # Seed Default Plans
        if not db_session.get(UserPlan, 'harsh'):
            db_session.add(UserPlan(username='harsh', plan_text=HARSH_DEFAULT_PLAN))
        if not db_session.get(UserPlan, 'apurva'):
            db_session.add(UserPlan(username='apurva', plan_text=APURVA_DEFAULT_PLAN))

        # Seed Default Rep Ranges
        default_rep_text = ""
        for ex, rng in DEFAULT_REP_RANGES.items():
            default_rep_text += f"{ex}: {rng}\n"

        if not db_session.get(UserRepRange, 'harsh'):
            db_session.add(UserRepRange(username='harsh', rep_text=default_rep_text))
        if not db_session.get(UserRepRange, 'apurva'):
            db_session.add(UserRepRange(username='apurva', rep_text=default_rep_text))

        db_session.commit()
        print("--- Database Initialized ---")
    except Exception as e:
        print(f"Init Error: {e}")
    finally:
        db_session.close()