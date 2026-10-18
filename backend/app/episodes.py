"""Repeat visits share the original ticket and never allocate seats or day claims."""

from sqlalchemy import func, select

from . import domain
from .models import DayClaim, Document, OfficeDay, ServiceEpisode
from .security import fail

ACTIVE = ("CALLED", "IN_SERVICE", "SKIPPED")


def require_staff(actor):
    if actor.role not in ("staff", "admin"):
        fail("FORBIDDEN", 403)


def require_free_window(db, day_id, window):
    if window not in (1, 2, 3):
        fail("WINDOW_REQUIRED", 422)
    if domain.window_busy(db, day_id, window):
        fail("WINDOW_BUSY")


def create(db, actor, ticket_id, expected_version, window, reason):
    if actor.role != "admin":
        fail("FORBIDDEN", 403)
    if not reason.strip():
        fail("REASON_REQUIRED", 422)
    ticket = domain.scoped_ticket(db, ticket_id, actor, lock=True)
    if ticket.version != expected_version:
        fail("VERSION_CONFLICT")
    day = db.get(OfficeDay, ticket.day_id)
    domain.require_service_open(day)
    if day.date != domain.local_now().date():
        fail("REPEAT_SAME_DAY_REQUIRED")
    seat = domain.seat_for(db, ticket)
    claim = db.get(DayClaim, (ticket.document_id, day.date))
    if (
        ticket.state != "COMPLETED"
        or not seat
        or seat.state != "CONSUMED"
        or not claim
        or claim.state != "SPENT"
    ):
        fail("REPEAT_NOT_ALLOWED")
    if db.scalar(
        select(ServiceEpisode.id)
        .where(
            ServiceEpisode.ticket_id == ticket.id,
            ServiceEpisode.state.in_(ACTIVE),
        )
        .limit(1)
    ):
        fail("REPEAT_ALREADY_ACTIVE")
    require_free_window(db, day.id, window)
    number = (
        db.scalar(select(func.max(ServiceEpisode.number)).where(ServiceEpisode.ticket_id == ticket.id)) or 0
    ) + 1
    episode = ServiceEpisode(
        ticket_id=ticket.id,
        day_id=day.id,
        number=number,
        window=window,
        reason=reason.strip(),
        created_by=actor.id,
        last_called_at=domain.utc_now(),
        created_at=domain.utc_now(),
    )
    db.add(episode)
    db.flush()
    ticket.version += 1
    domain.audit(db, actor, "EPISODE_CREATE", episode.id, reason)
    domain.notify(
        db,
        ticket.owner_id,
        "CALLED",
        document_id=ticket.document_id,
        label=f"A-{ticket.number:03d}",
        window=window,
        repeat=True,
    )
    return episode


def transition(db, actor, episode_id, action, expected_version, reason="", window=None):
    require_staff(actor)
    episode = db.get(ServiceEpisode, episode_id)
    if not episode:
        fail("NOT_FOUND", 404)
    # Match the ordinary transition order: document -> day -> parent ticket -> episode.
    ticket = domain.scoped_ticket(db, episode.ticket_id, actor, lock=True)
    db.refresh(episode, with_for_update=True)
    if episode.version != expected_version:
        fail("VERSION_CONFLICT")
    day = db.get(OfficeDay, episode.day_id)
    if action in ("verify", "start", "recall", "return"):
        domain.require_service_open(day)
    if action == "verify":
        if episode.state != "CALLED":
            fail("TICKET_STATE_CONFLICT")
        episode.original_match, episode.verified_at, episode.verified_by = True, domain.utc_now(), actor.id
        document = db.get(Document, ticket.document_id)
        document.verified_at, document.verified_by = domain.utc_now(), actor.id
    elif action == "start":
        if episode.state != "CALLED" or not episode.original_match:
            fail("ORIGINAL_REQUIRED")
        episode.state, episode.started_at = "IN_SERVICE", domain.utc_now()
    elif action == "complete":
        if episode.state != "IN_SERVICE":
            fail("TICKET_STATE_CONFLICT")
        episode.state, episode.completed_at = "COMPLETED", domain.utc_now()
    elif action == "recall":
        if episode.state != "CALLED":
            fail("TICKET_STATE_CONFLICT")
        if domain.call_wait_seconds(episode):
            fail("CALL_WAIT_REQUIRED")
        if episode.call_attempts >= 2:
            fail("CALL_LIMIT_REACHED")
        episode.calls += 1
        episode.call_attempts += 1
        episode.last_called_at = domain.utc_now()
        domain.notify(
            db,
            ticket.owner_id,
            "CALLED",
            document_id=ticket.document_id,
            label=f"A-{ticket.number:03d}",
            window=episode.window,
            repeat=True,
        )
    elif action == "skip":
        if episode.state != "CALLED":
            fail("TICKET_STATE_CONFLICT")
        if episode.call_attempts < 2:
            fail("REPEAT_CALL_REQUIRED")
        if domain.call_wait_seconds(episode):
            fail("CALL_WAIT_REQUIRED")
        episode.state, episode.window, episode.original_match = "SKIPPED", None, False
    elif action == "return":
        if episode.state != "SKIPPED" or not reason.strip():
            fail("REASON_OR_STATE_REQUIRED")
        require_free_window(db, day.id, window)
        episode.state, episode.window = "CALLED", window
        episode.call_attempts, episode.last_called_at = 1, domain.utc_now()
        episode.calls += 1
        episode.original_match, episode.verified_at, episode.verified_by = False, None, None
        domain.notify(
            db,
            ticket.owner_id,
            "CALLED",
            document_id=ticket.document_id,
            label=f"A-{ticket.number:03d}",
            window=window,
            repeat=True,
        )
    elif action in ("no-show", "reject", "cancel"):
        allowed = {"no-show": ("SKIPPED",), "reject": ("CALLED",), "cancel": ("CALLED", "SKIPPED")}
        if episode.state not in allowed[action] or not reason.strip():
            fail("REASON_OR_STATE_REQUIRED")
        episode.state = {"no-show": "NO_SHOW", "reject": "REJECTED", "cancel": "CANCELLED"}[action]
        episode.window, episode.completed_at = None, domain.utc_now()
    else:
        fail("UNKNOWN_ACTION", 422)
    episode.version += 1
    ticket.version += 1
    domain.audit(db, actor, "EPISODE_" + action.upper(), episode.id, reason)
    return episode
