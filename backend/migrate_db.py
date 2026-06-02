"""
Database initialization and migration script.
Run this to create/update database tables.
"""
from app.db.database import engine, Base
from app.db.models import Job, Result, Stage, Link, Annotation
from sqlalchemy import text

def init_db():
    """Create all tables"""
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully!")

def migrate_db():
    """Run migrations"""
    print("Checking for schema updates...")
    # Add any specific column migrations here if needed
    # For now, create_all handles new tables
    pass

if __name__ == "__main__":
    init_db()
    migrate_db()
