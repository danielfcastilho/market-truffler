import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.types import CHAR, DateTime, TypeDecorator


class GUID(TypeDecorator):
    """Platform-independent UUID type.

    Uses PostgreSQL's native UUID type in production, and a CHAR(32) hex
    representation elsewhere (e.g. SQLite in tests) so the same models work
    against both backends without duplicating schema definitions.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PostgresUUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return str(value)
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(value)
        return value.hex

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


class UTCDateTime(TypeDecorator):
    """A timezone-aware, always-UTC datetime.

    PostgreSQL's `timestamptz` round-trips timezone info natively, but
    SQLite has no native tz-aware type: plain `DateTime(timezone=True)`
    silently returns a *naive* datetime on a real read from SQLite (it only
    appears to preserve tzinfo when a query happens to be served from the
    same session's identity map instead of actually deserializing a row).
    A naive datetime fed into `.timestamp()`-based arithmetic gets
    interpreted in the host's local timezone, silently shifting candle
    boundaries — exactly the kind of bug M3's window-alignment math must
    not have. This type makes both dialects behave identically: always
    bind and return an explicit UTC-aware datetime.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
