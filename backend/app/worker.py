import io
import time
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import HTTPException
from sqlalchemy import or_, select

from . import domain
from .config import settings
from .db import session_factory
from .documents import read_document
from .models import DayClaim, Outbox, Seat, Upload, User, Waiting
from .security import cipher, encrypt, fail


def utcnow():
    return datetime.now(UTC)


def finish_offer(db, actor, entry_id, action):
    entry = db.get(Waiting, entry_id)
    if not entry or entry.owner_id != actor.id:
        fail("NOT_FOUND", 404)
    document = domain.lock_doc(db, entry.document_id)
    day = domain.lock_day(db, entry.day_id)
    db.refresh(entry, with_for_update=True)
    if entry.owner_id != actor.id:
        fail("NOT_FOUND", 404)
    if action == "cancel" and entry.state == "WAITING":
        entry.state = "DECLINED"
        return {"id": entry.id, "state": entry.state}
    if entry.state != "OFFERED":
        fail("OFFER_NOT_ACTIVE")
    seat = db.scalar(select(Seat).where(Seat.offer_id == entry.id).with_for_update())
    if action == "accept":
        if not entry.expires_at or entry.expires_at <= utcnow():
            fail("OFFER_EXPIRED")
        domain.validate_date(day.date)
        domain.check_claims(db, document.id, day.date, ignore_offer=entry.id)
        ticket = domain.allocate(db, actor, document, day, entry.companion, seat=seat, offer=entry)
        return domain.ticket_view(db, ticket)
    if action not in ("cancel", "decline", "expire"):
        fail("UNKNOWN_ACTION", 422)
    if action == "expire" and entry.expires_at > utcnow():
        fail("OFFER_NOT_EXPIRED")
    if seat:
        domain.free_seat(seat)
    claim = db.get(DayClaim, (entry.document_id, day.date))
    if claim and claim.offer_id == entry.id:
        db.delete(claim)
    entry.state = "EXPIRED" if action == "expire" else "DECLINED"
    domain.audit(db, actor, "WAITLIST_" + entry.state, entry.id)
    return {"id": entry.id, "state": entry.state}


def expire_offers():
    with session_factory()() as db:
        ids = list(
            db.scalars(select(Waiting.id).where(Waiting.state == "OFFERED", Waiting.expires_at <= utcnow()))
        )
    for entry_id in ids:
        with session_factory()() as db:
            entry = db.get(Waiting, entry_id)
            actor = db.get(User, entry.owner_id)
            try:
                finish_offer(db, actor, entry_id, "expire")
                db.commit()
            except Exception:
                db.rollback()


def offer_places():
    with session_factory()() as db:
        ids = list(
            db.scalars(
                select(Waiting.id).where(Waiting.state == "WAITING").order_by(Waiting.created_at).limit(200)
            )
        )
    for entry_id in ids:
        with session_factory()() as db:
            entry = db.get(Waiting, entry_id)
            try:
                document = domain.lock_doc(db, entry.document_id)
                day = domain.lock_day(db, entry.day_id)
                db.refresh(entry, with_for_update=True)
                if entry.state != "WAITING" or day.closed:
                    continue
                domain.validate_date(day.date)
                domain.check_claims(db, document.id, day.date)
                seat = db.scalar(
                    select(Seat)
                    .where(Seat.day_id == day.id, Seat.state == "FREE")
                    .order_by(Seat.ordinal)
                    .with_for_update()
                    .limit(1)
                )
                if not seat:
                    continue
                entry.state, entry.expires_at = "OFFERED", utcnow() + timedelta(minutes=10)
                seat.state, seat.offer_id, seat.ticket_id = "HELD", entry.id, None
                db.add(DayClaim(document_id=document.id, date=day.date, state="HELD", offer_id=entry.id))
                domain.notify(
                    db,
                    entry.owner_id,
                    "WAITLIST_OFFER",
                    document_id=document.id,
                    date=str(day.date),
                    entry_id=entry.id,
                )
                db.commit()
            except HTTPException as exc:
                db.rollback()
                if exc.detail in (
                    "DOCUMENT_DAY_LIMIT",
                    "FUTURE_SERVICE_LIMIT",
                    "DATE_UNAVAILABLE",
                    "BOOKING_CLOSED",
                ):
                    entry = db.get(Waiting, entry_id, with_for_update=True)
                    if entry and entry.state == "WAITING":
                        entry.state = "INELIGIBLE"
                        db.commit()
            except Exception:
                db.rollback()


def document_job():
    with session_factory()() as db:
        row = db.scalar(
            select(Upload)
            .where(
                or_(
                    Upload.status == "PENDING",
                    (Upload.status == "PROCESSING")
                    & (Upload.processing_at < utcnow() - timedelta(minutes=5)),
                )
            )
            .order_by(Upload.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not row:
            return
        upload_id, key = row.id, row.file_key
        row.status, row.processing_at = "PROCESSING", utcnow()
        db.commit()
    try:
        if not key:
            raise ValueError("missing image")
        raw = cipher().decrypt((settings().data_dir / key).read_bytes())
        result = read_document(io.BytesIO(raw))
        error = None
    except Exception as exc:
        result = {"status": "FAILED", "issues": ["OCR_TECHNICAL_ERROR"], "fields": {}}
        error = type(exc).__name__
    with session_factory()() as db:
        row = db.get(Upload, upload_id, with_for_update=True)
        if row and row.status == "PROCESSING":
            row.status, row.encrypted_result, row.error = result["status"], encrypt(result), error
            db.commit()


def deliver_one():
    with session_factory()() as db:
        candidate = db.scalar(
            select(Outbox)
            .where(
                Outbox.delivered_at.is_(None),
                Outbox.cancelled_at.is_(None),
                Outbox.next_attempt <= utcnow(),
                or_(Outbox.lease_until.is_(None), Outbox.lease_until <= utcnow()),
            )
            .order_by(Outbox.created_at)
            .limit(1)
        )
        if not candidate:
            return
        if candidate.document_id:
            domain.lock_doc(db, candidate.document_id)
        row = db.scalar(
            select(Outbox)
            .where(
                Outbox.id == candidate.id,
                Outbox.delivered_at.is_(None),
                Outbox.cancelled_at.is_(None),
                or_(Outbox.lease_until.is_(None), Outbox.lease_until <= utcnow()),
            )
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if not row:
            return
        event_id, owner = row.id, db.get(User, row.owner_id)
        payload, kind, phone = row.payload, row.kind, owner.phone
        row.lease_until = utcnow() + timedelta(seconds=60)
        lease = row.lease_until
        db.commit()
    ok = not settings().sms_webhook_url or phone.startswith("demo:")
    if not ok:
        try:
            response = httpx.post(
                settings().sms_webhook_url,
                json={
                    "to": phone,
                    "message": f"Red {kind}: " + str(payload.get("label", payload.get("date", ""))),
                },
                headers={
                    "Authorization": f"Bearer {settings().sms_webhook_token}",
                    "Idempotency-Key": event_id,
                },
                timeout=10,
            )
            response.raise_for_status()
            ok = True
        except httpx.HTTPError:
            pass
    with session_factory()() as db:
        row = db.get(Outbox, event_id, with_for_update=True)
        if row.lease_until != lease or row.cancelled_at is not None:
            return
        row.attempts += 1
        row.lease_until = None
        if ok:
            row.delivered_at = utcnow()
        else:
            row.next_attempt = utcnow() + timedelta(seconds=min(3600, 2 ** min(row.attempts, 12)))
        db.commit()


def cleanup():
    with session_factory()() as db:
        for row in db.scalars(
            select(Upload)
            .where(Upload.expires_at <= utcnow(), Upload.file_key.is_not(None))
            .with_for_update(skip_locked=True)
        ):
            (settings().data_dir / row.file_key).unlink(missing_ok=True)
            row.file_key, row.encrypted_result = None, None
            row.status = "DELETED"
        db.commit()


def tick():
    document_job()
    expire_offers()
    offer_places()
    deliver_one()
    cleanup()


def main():
    from .seed import seed

    seed()
    n = 0
    while True:
        try:
            tick()
            n += 1
            if n % 720 == 0:
                seed()
        except Exception as exc:
            print("worker retry:", type(exc).__name__, flush=True)
        time.sleep(5)


if __name__ == "__main__":
    main()
