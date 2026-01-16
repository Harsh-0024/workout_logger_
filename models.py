"""
Database models for the Workout Tracker application.
"""
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Float, ForeignKey, Index
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session, relationship
from sqlalchemy_utils import JSONType
from datetime import datetime
from config import Config
from list_of_exercise import list_of_exercises, DEFAULT_REP_RANGES, HARSH_DEFAULT_PLAN, APURVA_DEFAULT_PLAN

# --- DATABASE CONNECTION ---
database_url = Config.get_database_url()

# Enable connection pooling and better error handling
engine = create_engine(
    database_url,
    pool_pre_ping=True,  # Verify connections before using
    pool_recycle=3600,   # Recycle connections after 1 hour
    echo=False
)
session_factory = sessionmaker(bind=engine)
Session = scoped_session(session_factory)
Base = declarative_base()


# --- 1. USERS TABLE ---
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=True)

    lifts = relationship("Lift", back_populates="user", cascade="all, delete-orphan")
    plan = relationship("Plan", uselist=False, back_populates="user", cascade="all, delete-orphan")
    rep_ranges = relationship("RepRange", uselist=False, back_populates="user", cascade="all, delete-orphan")
    logs = relationship("WorkoutLog", back_populates="user", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}')>"


# --- 2. LIFTS TABLE (PR Tracker) ---
class Lift(Base):
    __tablename__ = 'lifts'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)

    exercise = Column(String(100), nullable=False, index=True)
    best_string = Column(Text)
    sets_json = Column(JSONType)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    user = relationship("User", back_populates="lifts")
    
    # Composite index for faster lookups
    __table_args__ = (
        Index('idx_user_exercise', 'user_id', 'exercise'),
    )
    
    def __repr__(self):
        return f"<Lift(id={self.id}, user_id={self.user_id}, exercise='{self.exercise}')>"


# --- 3. PLANS TABLE ---
class Plan(Base):
    __tablename__ = 'plans'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), unique=True, nullable=False, index=True)
    text_content = Column(Text)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    user = relationship("User", back_populates="plan")
    
    def __repr__(self):
        return f"<Plan(id={self.id}, user_id={self.user_id})>"


# --- 4. REP RANGES TABLE ---
class RepRange(Base):
    __tablename__ = 'rep_ranges'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), unique=True, nullable=False, index=True)
    text_content = Column(Text)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    user = relationship("User", back_populates="rep_ranges")
    
    def __repr__(self):
        return f"<RepRange(id={self.id}, user_id={self.user_id})>"


# --- 5. HISTORY TABLE (New) ---
class WorkoutLog(Base):
    __tablename__ = 'workout_logs'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)

    date = Column(DateTime, default=datetime.now, nullable=False, index=True)
    exercise = Column(String(100), nullable=False, index=True)
    top_weight = Column(Float)  # Heaviest weight moved that day
    top_reps = Column(Integer)  # Reps at that top weight
    estimated_1rm = Column(Float, index=True)  # Calculated strength metric

    user = relationship("User", back_populates="logs")
    
    # Composite indexes for common queries
    __table_args__ = (
        Index('idx_user_date', 'user_id', 'date'),
        Index('idx_user_exercise_date', 'user_id', 'exercise', 'date'),
    )
    
    def __repr__(self):
        return f"<WorkoutLog(id={self.id}, user_id={self.user_id}, exercise='{self.exercise}', date={self.date})>"


# --- MIGRATION HELPERS ---
def migrate_schema():
    """Add missing columns to existing database tables."""
    from sqlalchemy import inspect, text
    from utils.logger import logger
    
    try:
        inspector = inspect(engine)
        
        # Check if users table exists
        if 'users' not in inspector.get_table_names():
            return
        
        # Check and add created_at/updated_at to users table
        users_columns = [col['name'] for col in inspector.get_columns('users')]
        
        with engine.begin() as conn:  # Use begin() for transaction management
            dialect = engine.dialect.name
            # Choose appropriate timestamp type per dialect
            if dialect == 'mysql':
                ts_type = 'DATETIME'
                now_func = 'CURRENT_TIMESTAMP'
            else:
                # PostgreSQL, SQLite (and others) accept TIMESTAMP
                ts_type = 'TIMESTAMP'
                now_func = 'CURRENT_TIMESTAMP'
            
            if 'created_at' not in users_columns:
                logger.info("Adding created_at column to users table")
                conn.execute(text(f"ALTER TABLE users ADD COLUMN created_at {ts_type}"))
                conn.execute(text(f"UPDATE users SET created_at = {now_func} WHERE created_at IS NULL"))
            
            if 'updated_at' not in users_columns:
                logger.info("Adding updated_at column to users table")
                conn.execute(text(f"ALTER TABLE users ADD COLUMN updated_at {ts_type}"))
                conn.execute(text(f"UPDATE users SET updated_at = {now_func} WHERE updated_at IS NULL"))
                
    except Exception as e:
        logger.warning(f"Migration warning (may be expected): {e}")


# --- INITIALIZATION ---
def initialize_database():
    """Initialize database tables and seed default data."""
    # Create all tables (only creates new tables, not new columns)
    Base.metadata.create_all(engine)
    
    # Migrate existing schema if needed
    try:
        migrate_schema()
    except Exception as e:
        from utils.logger import logger
        logger.warning(f"Schema migration skipped: {e}")
    
    session = Session()
    try:
        # Default users (can be made configurable)
        default_users = ['harsh', 'apurva']
        for name in default_users:
            user = session.query(User).filter_by(username=name).first()
            if not user:
                user = User(username=name, created_at=datetime.now(), updated_at=datetime.now())
                session.add(user)
                session.flush()  # Get user.id
                _seed_user_data(session, user)
            else:
                # Update timestamps if they're None
                if user.created_at is None:
                    user.created_at = datetime.now()
                if user.updated_at is None:
                    user.updated_at = datetime.now()
        session.commit()
    except Exception as e:
        session.rollback()
        from utils.logger import logger
        logger.error(f"Database initialization error: {e}", exc_info=True)
        raise
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
