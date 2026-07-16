from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dsxlineage.core.config import settings

# pool_pre_ping: managed Postgres (and network paths in front of it) can
# silently drop idle connections; without this, the first query on a dead
# pooled connection fails/stalls instead of transparently reconnecting.
# pool_recycle: recycle connections before Azure's idle-connection timeout
# can kill them out from under us.
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, pool_recycle=1800)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
