"""Base declarativa común a todos los modelos SQLAlchemy del proyecto."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
