from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select

from .config import settings
from .models import Audit, DayClaim, Document, OfficeDay, Outbox, Seat, ServiceEpisode, Ticket, Waiting, uid
from .security import fail

ACTIVE = ("BOOKED", "CALLED", "SKIPPED", "IN_SERVICE")


def utc_now():
    return datetime.now(UTC)


def local_now():
    return utc_now().astimezone(settings().tz)


def iso_utc(value):
    return value.astimezone(UTC).isoformat() if value is not None else None


def service_status(day):
    current = local_now()
    if day.closed:
        return "CLOSED"
    if day.date < current.date():
        return "FINISHED"
    if not settings().enforce_hours:
        return "DEMO_OPEN"
    if day.date > current.date() or current.time() < time(8):
        return "BEFORE_OPEN"
    return "OPEN" if current.time() < time(14) else "FINISHED"


def require_service_open(day):
    status = service_status(day)
    if status == "CLOSED":
        fail("DAY_CLOSED")
    if status not in ("OPEN", "DEMO_OPEN"):
        fail("SERVICE_CLOSED")


def call_wait_seconds(ticket):
    if not ticket.last_called_at:
        return settings().recall_interval_seconds
    remaining = ticket.last_called_at + timedelta(seconds=settings().recall_interval_seconds) - utc_now()
    return max(0, int(remaining.total_seconds() + 0.999))


def validate_date(value):
    today = local_now().date()
    if value < today or value > today + timedelta(days=settings().booking_horizon_days):
        fail("DATE_UNAVAILABLE")
    if settings().enforce_hours and value == today and local_now().time() >= time(13):
        fail("BOOKING_CLOSED")


def audit(db, actor, action, target, reason=""):
    db.add(Audit(actor_id=actor.id, action=action, target_id=target, reason=reason[:300]))


def notify(db, owner, kind, document_id=None, **payload):
    db.add(Outbox(owner_id=owner, kind=kind, document_id=document_id, payload=payload))


def lock_doc(db, document_id, actor=None):
    doc = db.scalar(
        select(Document)
        .where(Document.id == document_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not doc or (actor and actor.role == "applicant" and doc.owner_id != actor.id):
        fail("NOT_FOUND", 404)
    return doc


def lock_day(db, day_id):
    day = db.scalar(
        select(OfficeDay)
        .where(OfficeDay.id == day_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not day:
        fail("DATE_UNAVAILABLE", 404)
    return day


def check_claims(db, doc_id, date_value, ignore_ticket=None, ignore_offer=None):
    claim = db.get(DayClaim, (doc_id, date_value))
    if (
        claim
        and not (ignore_ticket and claim.ticket_id == ignore_ticket)
        and not (ignore_offer and claim.offer_id == ignore_offer)
    ):
        fail("DOCUMENT_DAY_LIMIT")
    if settings().future_service_limit:
        active = db.scalar(
            select(Ticket).where(
                Ticket.document_id == doc_id, Ticket.state.in_(ACTIVE), Ticket.id != (ignore_ticket or "")
            )
        )
        offer = db.scalar(
            select(Waiting).where(
                Waiting.document_id == doc_id, Waiting.state == "OFFERED", Waiting.id != (ignore_offer or "")
            )
        )
        if active or offer:
            fail("FUTURE_SERVICE_LIMIT")


def ticket_view(db, ticket):
    day = db.get(OfficeDay, ticket.day_id)
    return {
        "id": ticket.id,
        "document_id": ticket.document_id,
        "document_revision": ticket.document_revision,
        "date": str(day.date),
        "number": ticket.number,
        "label": f"A-{ticket.number:03d}",
        "state": ticket.state,
        "companion": ticket.companion,
        "window": ticket.window,
        "original_match": ticket.original_match,
        "version": ticket.version,
        "calls": ticket.calls,
        "call_attempts": ticket.call_attempts,
        "last_called_at": iso_utc(ticket.last_called_at),
        "call_wait_seconds": call_wait_seconds(ticket) if ticket.state == "CALLED" else 0,
        "created_at": iso_utc(ticket.created_at),
        "started_at": iso_utc(ticket.started_at),
        "completed_at": iso_utc(ticket.completed_at),
        "episodes": [
            episode_view(e)
            for e in db.scalars(
                select(ServiceEpisode)
                .where(ServiceEpisode.ticket_id == ticket.id)
                .order_by(ServiceEpisode.number)
            )
        ],
        "office": day.office_id,
    }


def episode_view(episode):
    return {
        "id": episode.id,
        "ticket_id": episode.ticket_id,
        "number": episode.number,
        "state": episode.state,
        "window": episode.window,
        "version": episode.version,
        "original_match": episode.original_match,
        "calls": episode.calls,
        "call_attempts": episode.call_attempts,
        "last_called_at": iso_utc(episode.last_called_at),
        "call_wait_seconds": call_wait_seconds(episode) if episode.state == "CALLED" else 0,
        "created_at": iso_utc(episode.created_at),
        "started_at": iso_utc(episode.started_at),
        "completed_at": iso_utc(episode.completed_at),
    }


def window_busy(db, day_id, window):
    ordinary = db.scalar(
        select(Ticket.id)
        .where(
            Ticket.day_id == day_id,
            Ticket.window == window,
            Ticket.state.in_(("CALLED", "IN_SERVICE")),
        )
        .limit(1)
    )
    repeat = db.scalar(
        select(ServiceEpisode.id)
        .where(
            ServiceEpisode.day_id == day_id,
            ServiceEpisode.window == window,
            ServiceEpisode.state.in_(("CALLED", "IN_SERVICE")),
        )
        .limit(1)
    )
    return bool(ordinary or repeat)


def seat_for(db, ticket):
    return db.scalar(select(Seat).where(Seat.ticket_id == ticket.id).with_for_update())


def release_claim(db, ticket):
    day = db.get(OfficeDay, ticket.day_id)
    claim = db.get(DayClaim, (ticket.document_id, day.date))
    if claim and claim.ticket_id == ticket.id:
        db.delete(claim)


def free_seat(seat):
    seat.state, seat.ticket_id, seat.offer_id = "FREE", None, None


def allocate(db, actor, document, day, companion, seat=None, offer=None, owner_id=None):
    if day.closed:
        fail("DAY_CLOSED")
    if seat is None:
        seat = db.scalar(
            select(Seat)
            .where(Seat.day_id == day.id, Seat.state == "FREE")
            .order_by(Seat.ordinal)
            .with_for_update()
            .limit(1)
        )
    if seat is None:
        fail("DAY_FULL")
    ticket = Ticket(
        id=uid(),
        owner_id=owner_id or actor.id,
        document_id=document.id,
        document_revision=document.revision,
        day_id=day.id,
        number=day.next_number,
        companion=companion,
    )
    day.next_number += 1
    db.add(ticket)
    db.flush()
    seat.state, seat.ticket_id, seat.offer_id = "ALLOCATED", ticket.id, None
    if offer:
        claim = db.get(DayClaim, (document.id, day.date))
        claim.state, claim.ticket_id, claim.offer_id = "ACTIVE", ticket.id, None
        offer.state, offer.ticket_id = "ACCEPTED", ticket.id
    else:
        db.add(DayClaim(document_id=document.id, date=day.date, ticket_id=ticket.id, state="ACTIVE"))
    audit(db, actor, "BOOK", ticket.id)
    notify(
        db,
        ticket.owner_id,
        "BOOKED",
        document_id=document.id,
        label=f"A-{ticket.number:03d}",
        date=str(day.date),
    )
    db.flush()
    return ticket


def book(db, actor, document_id, day_id, companion):
    document = lock_doc(db, document_id, actor)
    day_info = db.get(OfficeDay, day_id)
    if not day_info:
        fail("DATE_UNAVAILABLE", 404)
    validate_date(day_info.date)
    check_claims(db, document.id, day_info.date)
    day = lock_day(db, day_id)
    waiting = db.scalar(
        select(Waiting.id).where(Waiting.day_id == day_id, Waiting.state == "WAITING").limit(1)
    )
    if waiting:
        fail("WAITLIST_PRIORITY")
    return allocate(db, actor, document, day, companion)


def scoped_ticket(db, ticket_id, actor, lock=False):
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        fail("NOT_FOUND", 404)
    day = db.get(OfficeDay, ticket.day_id)
    if actor.role == "applicant" and ticket.owner_id != actor.id:
        fail("NOT_FOUND", 404)
    if actor.role in ("staff", "admin") and day.office_id != actor.office_id:
        fail("NOT_FOUND", 404)
    if lock:
        lock_doc(db, ticket.document_id)
        lock_day(db, ticket.day_id)
        db.refresh(ticket, with_for_update=True)
        if actor.role == "applicant" and ticket.owner_id != actor.id:
            fail("NOT_FOUND", 404)
    return ticket


def cancel(db, actor, ticket_id, reason="", expected_version=None):
    ticket = scoped_ticket(db, ticket_id, actor, lock=True)
    permitted = ("BOOKED",) if actor.role == "applicant" else ("BOOKED", "CALLED", "SKIPPED")
    if ticket.state not in permitted:
        fail("TICKET_STATE_CONFLICT")
    if actor.role != "applicant":
        if not reason.strip():
            fail("REASON_REQUIRED", 422)
        if expected_version != ticket.version:
            fail("VERSION_CONFLICT")
    free_seat(seat_for(db, ticket))
    release_claim(db, ticket)
    ticket.state, ticket.window = "CANCELLED", None
    ticket.version += 1
    audit(db, actor, "CANCEL", ticket.id, reason)
    notify(db, ticket.owner_id, "CANCELLED", document_id=ticket.document_id, label=f"A-{ticket.number:03d}")
    return ticket


def transfer(db, actor, ticket_id, target_day, reason=""):
    old = scoped_ticket(db, ticket_id, actor)
    document = lock_doc(db, old.document_id, actor)
    info = db.get(OfficeDay, target_day)
    if not info:
        fail("DATE_UNAVAILABLE", 404)
    if old.day_id == target_day:
        fail("SAME_DATE")
    validate_date(info.date)
    check_claims(db, document.id, info.date, ignore_ticket=old.id)
    for day_id in sorted({old.day_id, target_day}):
        lock_day(db, day_id)
    db.refresh(old, with_for_update=True)
    permitted = ("BOOKED",) if actor.role == "applicant" else ("BOOKED", "CALLED", "SKIPPED")
    if old.state not in permitted:
        fail("TICKET_STATE_CONFLICT")
    if actor.role != "applicant" and not reason.strip():
        fail("REASON_REQUIRED")
    target = db.get(OfficeDay, target_day)
    if actor.role != "applicant" and target.office_id != actor.office_id:
        fail("FORBIDDEN", 403)
    if db.scalar(select(Waiting.id).where(Waiting.day_id == target.id, Waiting.state == "WAITING").limit(1)):
        fail("WAITLIST_PRIORITY")
    new = allocate(db, actor, document, target, old.companion, owner_id=old.owner_id)
    free_seat(seat_for(db, old))
    release_claim(db, old)
    old.state, old.window = "TRANSFERRED", None
    old.version += 1
    audit(db, actor, "TRANSFER", old.id, new.id)
    if actor.role != "applicant":
        audit(db, actor, "STAFF_TRANSFER", old.id, reason)
    notify(
        db,
        old.owner_id,
        "TRANSFERRED",
        document_id=old.document_id,
        label=f"A-{old.number:03d}",
        date=str(target.date),
    )
    return new


def next_ticket(db, actor, day_id, window):
    day = lock_day(db, day_id)
    if day.office_id != actor.office_id:
        fail("FORBIDDEN", 403)
    require_service_open(day)
    if window_busy(db, day_id, window):
        fail("WINDOW_BUSY")
    ticket = db.scalar(
        select(Ticket)
        .where(Ticket.day_id == day_id, Ticket.state == "BOOKED")
        .order_by(Ticket.number)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if ticket is None:
        return None
    ticket.state, ticket.window = "CALLED", window
    ticket.calls += 1
    ticket.call_attempts, ticket.last_called_at = 1, utc_now()
    ticket.original_match = False
    ticket.version += 1
    audit(db, actor, "CALL", ticket.id)
    notify(
        db,
        ticket.owner_id,
        "CALLED",
        document_id=ticket.document_id,
        label=f"A-{ticket.number:03d}",
        window=window,
    )
    return ticket


def transition(db, actor, ticket_id, action, expected_version, reason=""):
    ticket = scoped_ticket(db, ticket_id, actor, lock=True)
    if ticket.version != expected_version:
        fail("VERSION_CONFLICT")
    day = db.get(OfficeDay, ticket.day_id)
    claim = db.get(DayClaim, (ticket.document_id, day.date))
    if action in ("verify", "start", "recall", "return"):
        require_service_open(day)
    if action == "verify":
        if ticket.state != "CALLED":
            fail("TICKET_STATE_CONFLICT")
        ticket.original_match = True
        document = db.get(Document, ticket.document_id)
        document.verified_at, document.verified_by = utc_now(), actor.id
    elif action == "start":
        if ticket.state != "CALLED" or not ticket.original_match:
            fail("ORIGINAL_REQUIRED")
        ticket.state, ticket.started_at = "IN_SERVICE", utc_now()
        claim.state = "SPENT"
    elif action == "complete":
        if ticket.state != "IN_SERVICE":
            fail("TICKET_STATE_CONFLICT")
        ticket.state, ticket.completed_at = "COMPLETED", utc_now()
        seat_for(db, ticket).state = "CONSUMED"
    elif action == "skip":
        if ticket.state != "CALLED":
            fail("TICKET_STATE_CONFLICT")
        if ticket.call_attempts < 2:
            fail("REPEAT_CALL_REQUIRED")
        if call_wait_seconds(ticket):
            fail("CALL_WAIT_REQUIRED")
        ticket.state, ticket.window = "SKIPPED", None
        ticket.original_match = False
    elif action == "recall":
        if ticket.state != "CALLED":
            fail("TICKET_STATE_CONFLICT")
        if call_wait_seconds(ticket):
            fail("CALL_WAIT_REQUIRED")
        if ticket.call_attempts >= 2:
            fail("CALL_LIMIT_REACHED")
        ticket.calls += 1
        ticket.call_attempts += 1
        ticket.last_called_at = utc_now()
        notify(
            db,
            ticket.owner_id,
            "CALLED",
            document_id=ticket.document_id,
            label=f"A-{ticket.number:03d}",
            window=ticket.window,
        )
    elif action in ("no-show", "reject"):
        allowed = ("SKIPPED",) if action == "no-show" else ("CALLED",)
        if ticket.state not in allowed or not reason.strip():
            fail("REASON_OR_STATE_REQUIRED")
        ticket.state, ticket.window = ("NO_SHOW" if action == "no-show" else "REJECTED"), None
        claim.state = "SPENT"
        seat_for(db, ticket).state = "CONSUMED"
    elif action == "return":
        if ticket.state != "SKIPPED" or not reason.strip():
            fail("REASON_OR_STATE_REQUIRED")
        ticket.state = "BOOKED"
        ticket.call_attempts, ticket.last_called_at, ticket.original_match = 0, None, False
    elif action == "release":
        if actor.role != "admin" or ticket.state not in ("NO_SHOW", "REJECTED") or not reason.strip():
            fail("RELEASE_NOT_ALLOWED")
        seat = seat_for(db, ticket)
        if seat:
            free_seat(seat)
    else:
        fail("UNKNOWN_ACTION", 422)
    ticket.version += 1
    audit(db, actor, action.upper(), ticket.id, reason)
    return ticket
