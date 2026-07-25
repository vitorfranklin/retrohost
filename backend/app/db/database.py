from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base

from app.core.config import settings

settings.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
)

Base = declarative_base()


def init_db() -> None:
    from app.models import game  # noqa: F401  (ensures the model is registered)

    Base.metadata.create_all(bind=engine)
