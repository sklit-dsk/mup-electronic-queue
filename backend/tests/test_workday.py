from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
import threading

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from app import domain
from app.config import settings
from app.db import session_factory
from app.main import app
from app.models import Audit, DayClaim, OfficeDay, Outbox, Seat, Ticket, User, Waiting
from conftest import new_document
from test_queue import login, setup_case


@pytest.fixture
def clock(monkeypatch):
    current = [domain.local_now().replace(hour=9, minute=0, second=0, microsecond=0).astimezone(UTC)]
    monkeypatch.setattr(domain, "utc_now", lambda: current[0])
    return current


def at(day, hour, minute=0):
    return datetime.combine(day.date, time(hour, minute), settings().tz).astimezone(UTC)


def called_ticket(db):
    user, staff, admin, days, doc = setup_case(db)
    ticket = domain.book(db, user, doc.id, days[0].id, 0)
    db.commit()
    domain.next_ticket(db, staff, days[0].id, 1)
    db.commit()
    return user, staff, admin, days, doc, ticket


def test_two_calls_and_two_grace_periods_required(db, clock):
    _, staff, _, _, _, ticket = called_ticket(db)
    with pytest.raises(HTTPException, match="REPEAT_CALL_REQUIRED"):
        domain.transition(db, staff, ticket.id, "skip", ticket.version)
    with pytest.raises(HTTPException, match="CALL_WAIT_REQUIRED"):
        domain.transition(db, staff, ticket.id, "recall", ticket.version)
    clock[0] += timedelta(seconds=119)
    with pytest.raises(HTTPException, match="CALL_WAIT_REQUIRED"):
        domain.transition(db, staff, ticket.id, "recall", ticket.version)
    clock[0] += timedelta(seconds=1)
    domain.transition(db, staff, ticket.id, "recall", ticket.version)
    db.commit()
    with pytest.raises(HTTPException, match="CALL_WAIT_REQUIRED"):
        domain.transition(db, staff, ticket.id, "skip", ticket.version)
    clock[0] += timedelta(seconds=120)
    with pytest.raises(HTTPException, match="CALL_LIMIT_REACHED"):
        domain.transition(db, staff, ticket.id, "recall", ticket.version)
    domain.transition(db, staff, ticket.id, "skip", ticket.version)
    db.commit()
    assert ticket.state == "SKIPPED"
    assert domain.seat_for(db, ticket).state == "ALLOCATED"
    assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.kind == "CALLED")) == 2


def test_return_requires_fresh_calls_and_original_check(db, clock):
    _, staff, _, days, _, ticket = called_ticket(db)
    domain.transition(db, staff, ticket.id, "verify", ticket.version)
    clock[0] += timedelta(seconds=120)
    domain.transition(db, staff, ticket.id, "recall", ticket.version)
    clock[0] += timedelta(seconds=120)
    domain.transition(db, staff, ticket.id, "skip", ticket.version)
    domain.transition(db, staff, ticket.id, "return", ticket.version, "Applicant returned")
    db.commit()
    domain.next_ticket(db, staff, days[0].id, 2)
    db.commit()
    assert ticket.call_attempts == 1 and ticket.calls == 3 and not ticket.original_match
    with pytest.raises(HTTPException, match="ORIGINAL_REQUIRED"):
        domain.transition(db, staff, ticket.id, "start", ticket.version)
    with pytest.raises(HTTPException, match="REPEAT_CALL_REQUIRED"):
        domain.transition(db, staff, ticket.id, "skip", ticket.version)


@pytest.mark.parametrize(
    "hour,minute,allowed", [(7, 59, False), (8, 0, True), (13, 59, True), (14, 0, False)]
)
def test_service_hours_in_podgorica(db, clock, monkeypatch, hour, minute, allowed):
    user, staff, _, days, doc = setup_case(db)
    domain.book(db, user, doc.id, days[0].id, 0)
    db.commit()
    monkeypatch.setattr(settings(), "enforce_hours", True)
    clock[0] = at(days[0], hour, minute)
    if allowed:
        assert domain.next_ticket(db, staff, days[0].id, 1).state == "CALLED"
    else:
        with pytest.raises(HTTPException, match="SERVICE_CLOSED"):
            domain.next_ticket(db, staff, days[0].id, 1)


@pytest.mark.parametrize("action", ["verify", "start", "recall"])
def test_called_but_not_started_visit_stays_unresolved_at_14(db, clock, monkeypatch, action):
    _, staff, _, days, _, ticket = called_ticket(db)
    domain.transition(db, staff, ticket.id, "verify", ticket.version)
    db.commit()
    monkeypatch.setattr(settings(), "enforce_hours", True)
    clock[0] = at(days[0], 14)
    with pytest.raises(HTTPException, match="SERVICE_CLOSED"):
        domain.transition(db, staff, ticket.id, action, ticket.version)
    assert ticket.state == "CALLED"
    assert domain.seat_for(db, ticket).state == "ALLOCATED"


def test_started_visit_can_finish_after_14_or_manual_closure(db, clock, monkeypatch):
    _, staff, _, days, doc, ticket = called_ticket(db)
    domain.transition(db, staff, ticket.id, "verify", ticket.version)
    domain.transition(db, staff, ticket.id, "start", ticket.version)
    db.commit()
    days[0].closed = True
    db.commit()
    monkeypatch.setattr(settings(), "enforce_hours", True)
    clock[0] = at(days[0], 14, 10)
    domain.transition(db, staff, ticket.id, "complete", ticket.version)
    db.commit()
    assert ticket.state == "COMPLETED"
    assert domain.seat_for(db, ticket).state == "CONSUMED"
    assert db.get(DayClaim, (doc.id, days[0].date)).state == "SPENT"


def test_staff_can_find_yesterdays_unresolved_tickets(db, clock):
    _, _, _, days, _, ticket = called_ticket(db)
    clock[0] = at(days[1], 9)
    with TestClient(app) as client:
        login(client, "staff")
        assert days[0].id in {d["id"] for d in client.get("/api/v1/staff/days").json()}
        assert days[0].id not in {d["id"] for d in client.get("/api/v1/days").json()}
        report = client.get(f"/api/v1/staff/days/{days[0].id}/summary").json()
        assert report["service_status"] == "FINISHED"
        assert [t["id"] for t in report["unresolved"]] == [ticket.id]
        assert report["allocated"] == 1 and report["counts"]["NO_SHOW"] == 0
    with pytest.raises(HTTPException, match="SERVICE_CLOSED"):
        staff = db.scalar(select(User).where(User.role == "staff"))
        domain.next_ticket(db, staff, days[0].id, 2)


@pytest.mark.parametrize("source_state", ["BOOKED", "CALLED", "SKIPPED"])
def test_staff_transfer_preserves_applicant_ownership_and_is_idempotent(db, clock, source_state):
    user, _, _, days, doc, ticket = called_ticket(db)
    ticket.state = source_state
    if source_state != "CALLED":
        ticket.window = None
    db.commit()
    clock[0] = at(days[1], 9)
    with TestClient(app) as client:
        login(client, "staff")
        payload = {"day_id": days[1].id, "reason": "Office did not finish the queue"}
        url = f"/api/v1/tickets/{ticket.id}/transfer"
        first = client.post(url, json=payload)
        assert first.status_code == 200, first.text
        assert client.post(url, json=payload).json() == first.json()
    db.expire_all()
    new = db.get(Ticket, first.json()["id"])
    assert new.owner_id == user.id
    assert db.get(Ticket, ticket.id).state == "TRANSFERRED"
    assert domain.seat_for(db, new).state == "ALLOCATED"
    assert db.get(DayClaim, (doc.id, days[0].date)) is None
    assert db.get(DayClaim, (doc.id, days[1].date)).ticket_id == new.id
    event = db.scalar(select(Audit).where(Audit.action == "STAFF_TRANSFER"))
    assert event.reason == payload["reason"]
    assert db.scalar(select(Outbox).where(Outbox.kind == "TRANSFERRED")).owner_id == user.id


def test_failed_staff_transfer_leaves_old_ticket_and_capacity_intact(db):
    _, staff, _, days, _, ticket = called_ticket(db)
    for seat in db.scalars(select(Seat).where(Seat.day_id == days[1].id)):
        seat.state, seat.offer_id = "HELD", f"test-held-{seat.ordinal}"
    db.commit()
    with pytest.raises(HTTPException, match="DAY_FULL"):
        domain.transfer(db, staff, ticket.id, days[1].id, "End of day")
    db.rollback()
    assert db.get(Ticket, ticket.id).state == "CALLED"
    assert domain.seat_for(db, ticket).state == "ALLOCATED"
    assert db.scalar(select(func.count()).select_from(Ticket)) == 1


def test_staff_transfer_respects_reason_scope_and_waitlist(db):
    user, staff, _, days, _, ticket = called_ticket(db)
    with pytest.raises(HTTPException, match="REASON_REQUIRED"):
        domain.transfer(db, staff, ticket.id, days[1].id)
    doc = new_document(db, user, "WAIT00002")
    db.add(Waiting(owner_id=user.id, document_id=doc.id, day_id=days[1].id))
    db.commit()
    with pytest.raises(HTTPException, match="WAITLIST_PRIORITY"):
        domain.transfer(db, staff, ticket.id, days[1].id, "End of day")
    db.rollback()
    other = OfficeDay(id="other-office-test", office_id="other", date=days[1].date)
    db.add(other)
    db.commit()
    with pytest.raises(HTTPException, match="FORBIDDEN"):
        domain.transfer(db, staff, ticket.id, other.id, "End of day")


def test_workday_summary_denies_applicants_and_other_offices(db):
    _, _, _, days, _, _ = called_ticket(db)
    other = OfficeDay(id="private-office-test", office_id="other", date=days[0].date)
    db.add(other)
    db.commit()
    with TestClient(app) as client:
        login(client, "applicant")
        assert client.get(f"/api/v1/staff/days/{days[0].id}/summary").status_code == 403
        assert client.get("/api/v1/staff/days").status_code == 403
        login(client, "staff")
        assert client.get(f"/api/v1/staff/days/{other.id}/summary").status_code == 404


def test_manual_day_closure_is_idempotent_and_does_not_drop_queue(db):
    _, staff, _, days, _, ticket = called_ticket(db)
    with TestClient(app) as client:
        login(client, "admin")
        url = f"/api/v1/admin/days/{days[0].id}"
        assert client.post(url, json={"closed": True}).status_code == 422
        payload = {"closed": True, "reason": "Office closed early"}
        assert client.post(url, json=payload).status_code == 200
        assert client.post(url, json=payload).status_code == 200
        report = client.get(f"/api/v1/staff/days/{days[0].id}/summary").json()
        assert report["service_status"] == "CLOSED" and len(report["unresolved"]) == 1
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == "DAY_CLOSE")) == 1
    assert db.get(Ticket, ticket.id).state == "CALLED"
    with pytest.raises(HTTPException, match="DAY_CLOSED"):
        domain.next_ticket(db, staff, days[0].id, 2)


def test_close_racing_call_next_has_no_lost_tickets(db):
    user, _, _, days, doc = setup_case(db)
    ticket = domain.book(db, user, doc.id, days[0].id, 0)
    db.commit()
    start = threading.Barrier(2)

    def close():
        with TestClient(app) as client:
            login(client, "admin")
            start.wait(timeout=10)
            return client.post(
                f"/api/v1/admin/days/{days[0].id}", json={"closed": True, "reason": "Closing"}
            ).status_code

    def call():
        with TestClient(app) as client:
            login(client, "staff")
            start.wait(timeout=10)
            return client.post(
                "/api/v1/staff/call-next", json={"day_id": days[0].id, "window": 1}
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        closed, called = pool.submit(close), pool.submit(call)
        assert closed.result(timeout=20) == 200
        assert called.result(timeout=20) in (200, 409)
    with session_factory()() as session:
        assert session.get(OfficeDay, days[0].id).closed
        assert session.get(Ticket, ticket.id).state in ("BOOKED", "CALLED")
        assert domain.seat_for(session, ticket).state == "ALLOCATED"
