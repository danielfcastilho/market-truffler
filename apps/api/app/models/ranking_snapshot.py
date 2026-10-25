from datetime import datetime

from sqlalchemy import BigInteger, Index, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class RankingSnapshot(Base):
    """One published Long/Short ranking, exactly as Sniffer made it
    available (`app.services.ranking_snapshots`). Immutable: written once,
    never updated.

    Identity of a publication: the frame, the instant its analysis was
    stored (`analyzed_at`) and the four spec hashes — a re-analysis or a
    spec change is a different publication of the same frame, never an
    overwrite. `payload` is the published response (gzip-compressed JSON,
    full float precision), `payload_sha256` its digest."""

    __tablename__ = "ranking_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "frame_time", "analyzed_at", "spec_key", name="uq_ranking_snapshot_publication"
        ),
        Index("ix_ranking_snapshots_published", "published_at", "id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    analyzed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    analysis: Mapped[str | None] = mapped_column(String(16))
    published_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    temporal_contract_version: Mapped[int | None] = mapped_column(Integer)
    spec_key: Mapped[str] = mapped_column(String(64), nullable=False)
    long_candidate: Mapped[str | None] = mapped_column(String(80))
    long_candidate_hash: Mapped[str | None] = mapped_column(String(64))
    short_candidate: Mapped[str | None] = mapped_column(String(80))
    short_candidate_hash: Mapped[str | None] = mapped_column(String(64))
    long_qualification: Mapped[str | None] = mapped_column(String(80))
    long_qualification_hash: Mapped[str | None] = mapped_column(String(64))
    short_qualification: Mapped[str | None] = mapped_column(String(80))
    short_qualification_hash: Mapped[str | None] = mapped_column(String(64))
    long_qualification_status: Mapped[str | None] = mapped_column(String(16))
    short_qualification_status: Mapped[str | None] = mapped_column(String(16))
    long_truffles: Mapped[int | None] = mapped_column(Integer)
    short_truffles: Mapped[int | None] = mapped_column(Integer)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
