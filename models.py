import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Float, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session, relationship
from sqlalchemy_utils import JSONType
from datetime import datetime
from list_of_exercise import list_of_exercises, DEFAULT_REP_RANGES, HARSH_DEFAULT_PLAN, APURVA_DEFAULT_PLAN

# --- DATABASE CONNECTION ---
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

if not database_url:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(BASE_DIR, "workout.db")
    database_url = f"sqlite:///{DB_PATH}"

engine = create_engine(database_url)
session_factory = sessionmaker(bind=engine)
Session = scoped_session(session_factory)
Base = declarative_base()


# --- 1. USERS TABLE ---
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)

    lifts = relationship("Lift", back_populates="user")
    plan = relationship("Plan", uselist=False, back_populates="user")
    rep_ranges = relationship("RepRange", uselist=False, back_populates="user")
    logs = relationship("WorkoutLog", back_populates="user")


# --- 2. LIFTS TABLE (PR Tracker) ---
class Lift(Base):
    __tablename__ = 'lifts'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)

    exercise = Column(String, index=True)
    best_string = Column(String)
    sets_json = Column(JSONType)
    updated_at = Column(DateTime, default=datetime.now)

    user = relationship("User", back_populates="lifts")


# --- 3. PLANS TABLE ---
class Plan(Base):
    __tablename__ = 'plans'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), unique=True, nullable=False)
    text_content = Column(Text)
    updated_at = Column(DateTime, default=datetime.now)

    user = relationship("User", back_populates="plan")


# --- 4. REP RANGES TABLE ---
class RepRange(Base):
    __tablename__ = 'rep_ranges'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), unique=True, nullable=False)
    text_content = Column(Text)
    updated_at = Column(DateTime, default=datetime.now)

    user = relationship("User", back_populates="rep_ranges")


# --- 5. HISTORY TABLE (New) ---
class WorkoutLog(Base):
    __tablename__ = 'workout_logs'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)

    date = Column(DateTime, default=datetime.now)
    exercise = Column(String, index=True)
    top_weight = Column(Float)  # Heaviest weight moved that day
    top_reps = Column(Integer)  # Reps at that top weight
    estimated_1rm = Column(Float)  # Calculated strength metric

    user = relationship("User", back_populates="logs")


# --- INITIALIZATION ---
def initialize_database():
    Base.metadata.create_all(engine)
    session = Session()
    try:
        for name in ['harsh', 'apurva']:
            user = session.query(User).filter_by(username=name).first()
            if not user:
                user = User(username=name)
                session.add(user)
                session.commit()
                _seed_user_data(session, user)
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"Init Error: {e}")
    finally:
        session.close()


def _seed_user_data(session, user):
    for ex in list_of_exercises:
        session.add(Lift(user_id=user.id, exercise=ex, best_string="", sets_json={"weights": [], "reps": []}))

    default_plan = HARSH_DEFAULT_PLAN if user.username == 'harsh' else APURVA_DEFAULT_PLAN
    session.add(Plan(user_id=user.id, text_content=default_plan))

    default_rep_text = ""
    for ex, rng in DEFAULT_REP_RANGES.items():
        default_rep_text += f"{ex}: {rng}\n"
    session.add(RepRange(user_id=user.id, text_content=default_rep_text))