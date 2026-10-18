from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def uid():
    return str(uuid4())


def now():
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    phone: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(150), default="")
    role: Mapped[str] = mapped_column(String(20), default="applicant")
    office_id: Mapped[str] = mapped_column(String(30), default="podgorica")
    password_hash: Mapped[str | None] = mapped_column(Text)


class Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    csrf: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Otp(Base):
    __tablename__ = "otp_challenges"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    phone: Mapped[str] = mapped_column(String(30))
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Throttle(Base):
    __tablename__ = "auth_throttles"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    resets_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    encrypted_fields: Mapped[str] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    version: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class DocumentRevision(Base):
    __tablename__ = "document_revisions"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    encrypted_fields: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class DocumentRequest(Base):
    __tablename__ = "document_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    office_id: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(20))
    upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id"))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    base_version: Mapped[int | None] = mapped_column(Integer)
    previous_owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="PENDING")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(300), default="")
    __table_args__ = (
        CheckConstraint("kind IN ('RECOVER','REPLACE')"),
        CheckConstraint("state IN ('PENDING','APPROVED','REJECTED','CANCELLED')"),
        Index(
            "one_pending_document_request",
            "requester_id",
            "kind",
            "upload_id",
            unique=True,
            postgresql_where=text("state = 'PENDING'"),
        ),
    )


class Upload(Base):
    __tablename__ = "uploads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    file_key: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    encrypted_result: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(200))
    processing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OfficeDay(Base):
    __tablename__ = "office_days"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    office_id: Mapped[str] = mapped_column(String(30), default="podgorica")
    date: Mapped[datetime] = mapped_column(Date)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    next_number: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint("office_id", "date"),)


class Seat(Base):
    __tablename__ = "seats"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    day_id: Mapped[str] = mapped_column(ForeignKey("office_days.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default="FREE")
    ticket_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    offer_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    __table_args__ = (
        UniqueConstraint("day_id", "ordinal"),
        CheckConstraint("ordinal BETWEEN 1 AND 100"),
        CheckConstraint(
            "(state = 'FREE' AND ticket_id IS NULL AND offer_id IS NULL) OR (state = 'HELD' AND offer_id IS NOT NULL AND ticket_id IS NULL) OR (state IN ('ALLOCATED','CONSUMED') AND ticket_id IS NOT NULL AND offer_id IS NULL)"
        ),
    )


class DayClaim(Base):
    __tablename__ = "day_claims"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    date: Mapped[datetime] = mapped_column(Date, primary_key=True)
    ticket_id: Mapped[str | None] = mapped_column(String(36))
    offer_id: Mapped[str | None] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    document_revision: Mapped[int] = mapped_column(Integer, default=1)
    day_id: Mapped[str] = mapped_column(ForeignKey("office_days.id"), index=True)
    service: Mapped[str] = mapped_column(String(30), default="residence")
    number: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default="BOOKED")
    companion: Mapped[int] = mapped_column(Integer, default=0)
    window: Mapped[int | None] = mapped_column(Integer)
    original_match: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    call_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_called_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("day_id", "number"),
        UniqueConstraint("id", "day_id", name="ticket_id_day_unique"),
        CheckConstraint("companion IN (0,1)"),
        CheckConstraint("state NOT IN ('CALLED','IN_SERVICE') OR \"window\" IS NOT NULL"),
        Index(
            "one_ticket_per_window",
            "day_id",
            "window",
            unique=True,
            postgresql_where=text("state IN ('CALLED','IN_SERVICE')"),
        ),
    )


class ServiceEpisode(Base):
    """A repeat visit; the original ticket and its consumed seat remain unchanged."""

    __tablename__ = "service_episodes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    ticket_id: Mapped[str] = mapped_column(String(36), index=True)
    day_id: Mapped[str] = mapped_column(String(50), index=True)
    number: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default="CALLED")
    window: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(300))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    original_match: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calls: Mapped[int] = mapped_column(Integer, default=1)
    call_attempts: Mapped[int] = mapped_column(Integer, default=1)
    last_called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        ForeignKeyConstraint(["ticket_id", "day_id"], ["tickets.id", "tickets.day_id"]),
        UniqueConstraint("ticket_id", "number"),
        CheckConstraint("number >= 1"),
        CheckConstraint("length(trim(reason)) > 0"),
        CheckConstraint(
            "state IN ('CALLED','IN_SERVICE','SKIPPED','COMPLETED','NO_SHOW','REJECTED','CANCELLED')"
        ),
        CheckConstraint("state NOT IN ('CALLED','IN_SERVICE') OR \"window\" IS NOT NULL"),
        CheckConstraint('"window" IS NULL OR "window" BETWEEN 1 AND 3'),
        Index(
            "one_active_repeat_per_ticket",
            "ticket_id",
            unique=True,
            postgresql_where=text("state IN ('CALLED','IN_SERVICE','SKIPPED')"),
        ),
        Index(
            "one_repeat_per_window",
            "day_id",
            "window",
            unique=True,
            postgresql_where=text("state IN ('CALLED','IN_SERVICE')"),
        ),
    )


class Waiting(Base):
    __tablename__ = "waiting"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    day_id: Mapped[str] = mapped_column(ForeignKey("office_days.id"))
    state: Mapped[str] = mapped_column(String(20), default="WAITING")
    companion: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ticket_id: Mapped[str | None] = mapped_column(ForeignKey("tickets.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        Index(
            "one_waiting_per_document_day",
            "document_id",
            "day_id",
            unique=True,
            postgresql_where=text("state IN ('WAITING','OFFERED')"),
        ),
        CheckConstraint("companion IN (0,1)"),
    )


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    actor_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Idempotency(Base):
    __tablename__ = "idempotency"
    key: Mapped[str] = mapped_column(String(180), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"), index=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSON)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
