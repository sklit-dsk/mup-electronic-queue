from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, timedelta
import threading

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from app import domain, episodes
from app.config import settings
from app.db import session_factory
from app.main import app
from app.models import Audit, DayClaim, Seat, ServiceEpisode, Ticket, User
from conftest import new_document
from test_queue import complete_ticket, login
from test_workday import at, called_ticket


@pytest.fixture
def clock(monkeypatch):
    value = [domain.local_now().replace(hour=9, minute=0, second=0, microsecond=0).astimezone(UTC)]
    monkeypatch.setattr(domain, "utc_now", lambda: value[0])
    return value


def primary_snapshot(ticket):
    return (
        ticket.state,
        ticket.started_at,
        ticket.completed_at,
        ticket.window,
        ticket.original_match,
        ticket.calls,
    )


def test_repeat_preserves_primary_history_seat_and_claim(db):
    user, staff, admin, days, doc, ticket = complete_ticket(db)
    first = primary_snapshot(ticket)
    repeat = episodes.create(db, admin, ticket.id, ticket.version, 2, "Additional document review")
    db.commit()
    assert repeat.number == 1 and not repeat.original_match
    with pytest.raises(HTTPException, match="ORIGINAL_REQUIRED"):
        episodes.transition(db, staff, repeat.id, "start", repeat.version)
    for action in ("verify", "start", "complete"):
        episodes.transition(db, staff, repeat.id, action, repeat.version)
        db.commit()
    assert primary_snapshot(ticket) == first
    assert repeat.state == "COMPLETED" and repeat.verified_by == staff.id and repeat.window == 2
    assert domain.seat_for(db, ticket).state == "CONSUMED"
    assert db.get(DayClaim, (doc.id, days[0].date)).state == "SPENT"
    assert db.scalar(select(func.count()).select_from(Ticket)) == 1
    assert db.scalar(select(func.count()).select_from(Seat).where(Seat.state != "FREE")) == 1
    with pytest.raises(HTTPException, match="DOCUMENT_DAY_LIMIT"):
        domain.book(db, user, doc.id, days[0].id, 0)
    assert {a.action for a in db.scalars(select(Audit).where(Audit.target_id == repeat.id))} == {
        "EPISODE_CREATE",
        "EPISODE_VERIFY",
        "EPISODE_START",
        "EPISODE_COMPLETE",
    }


def test_only_admin_can_open_repeat_with_reason(db):
    _, _, _, _, _, ticket = complete_ticket(db)
    with TestClient(app) as client:
        for role in ("applicant", "staff"):
            login(client, role)
            response = client.post(
                f"/api/v1/staff/tickets/{ticket.id}/episodes",
                json={"version": ticket.version, "window": 1, "reason": "Repeat review"},
            )
            assert response.status_code == 403
        login(client, "admin")
        response = client.post(
            f"/api/v1/staff/tickets/{ticket.id}/episodes",
            json={"version": ticket.version, "window": 1, "reason": "   "},
        )
        assert response.status_code == 422


@pytest.mark.parametrize("state", ["BOOKED", "CALLED", "IN_SERVICE", "NO_SHOW", "REJECTED", "CANCELLED"])
def test_repeat_requires_completed_ticket(db, state):
    _, _, admin, _, _, ticket = complete_ticket(db)
    ticket.state = state
    db.commit()
    with pytest.raises(HTTPException, match="REPEAT_NOT_ALLOWED"):
        episodes.create(db, admin, ticket.id, ticket.version, 2, "Invalid original state")


def test_repeat_creation_is_idempotent_and_keeps_original_owner(db):
    user, _, _, _, _, ticket = complete_ticket(db)
    with TestClient(app) as client:
        login(client, "admin")
        body = {"version": ticket.version, "window": 1, "reason": "Repeat review"}
        first = client.post(f"/api/v1/staff/tickets/{ticket.id}/episodes", json=body)
        assert first.status_code == 200, first.text
        assert client.post(f"/api/v1/staff/tickets/{ticket.id}/episodes", json=body).json() == first.json()
        history = client.get(f"/api/v1/staff/tickets/{ticket.id}/episodes").json()
        assert history["episodes"][0]["reason"] == "Repeat review"
        assert history["ticket"]["state"] == "COMPLETED"
        login(client, "applicant")
        mine = client.get("/api/v1/tickets/mine").json()
        assert len(mine) == 1 and mine[0]["episodes"][0]["id"] == first.json()["id"]
        assert "reason" not in mine[0]["episodes"][0]
    db.expire_all()
    assert db.get(Ticket, ticket.id).owner_id == user.id
    assert db.scalar(select(func.count()).select_from(ServiceEpisode)) == 1


def test_active_repeat_cannot_be_duplicated(db):
    _, _, admin, _, _, ticket = complete_ticket(db)
    episodes.create(db, admin, ticket.id, ticket.version, 1, "Repeat review")
    db.commit()
    with pytest.raises(HTTPException, match="REPEAT_ALREADY_ACTIVE"):
        episodes.create(db, admin, ticket.id, ticket.version, 2, "Duplicate repeat")


def test_repeat_and_ordinary_visit_share_window_capacity(db):
    user, staff, admin, days, _, ticket = complete_ticket(db)
    doc2 = new_document(db, user, "REPEAT002")
    domain.book(db, user, doc2.id, days[0].id, 0)
    repeat = episodes.create(db, admin, ticket.id, ticket.version, 1, "Repeat review")
    db.commit()
    with pytest.raises(HTTPException, match="WINDOW_BUSY"):
        domain.next_ticket(db, staff, days[0].id, 1)
    episodes.transition(db, staff, repeat.id, "cancel", repeat.version, "Use another window later")
    db.commit()
    domain.next_ticket(db, staff, days[0].id, 1)
    db.commit()
    with pytest.raises(HTTPException, match="WINDOW_BUSY"):
        episodes.create(db, admin, ticket.id, ticket.version, 1, "Repeat review")


def test_repeat_skip_return_and_no_show_never_release_used_seat(db, clock):
    _, staff, admin, days, doc, ticket = complete_ticket(db)
    first = primary_snapshot(ticket)
    repeat = episodes.create(db, admin, ticket.id, ticket.version, 2, "Repeat review")
    db.commit()
    with pytest.raises(HTTPException, match="REPEAT_CALL_REQUIRED"):
        episodes.transition(db, staff, repeat.id, "skip", repeat.version)
    with pytest.raises(HTTPException, match="CALL_WAIT_REQUIRED"):
        episodes.transition(db, staff, repeat.id, "recall", repeat.version)
    for cycle in range(2):
        episodes.transition(db, staff, repeat.id, "verify", repeat.version)
        clock[0] += timedelta(seconds=120)
        episodes.transition(db, staff, repeat.id, "recall", repeat.version)
        clock[0] += timedelta(seconds=120)
        episodes.transition(db, staff, repeat.id, "skip", repeat.version)
        db.commit()
        if cycle == 0:
            episodes.transition(
                db, staff, repeat.id, "return", repeat.version, "Applicant returned", window=3
            )
            db.commit()
            assert not repeat.original_match and repeat.verified_at is None and repeat.window == 3
    episodes.transition(db, staff, repeat.id, "no-show", repeat.version, "Applicant absent after second call")
    db.commit()
    with pytest.raises(HTTPException, match="RELEASE_NOT_ALLOWED"):
        domain.transition(db, admin, ticket.id, "release", ticket.version, "Try to free completed seat")
    assert repeat.state == "NO_SHOW" and primary_snapshot(ticket) == first
    assert domain.seat_for(db, ticket).state == "CONSUMED"
    assert db.get(DayClaim, (doc.id, days[0].date)).state == "SPENT"


@pytest.mark.parametrize("terminal", ["cancel", "reject"])
def test_aborted_repeat_retains_original_completion_and_allows_later_repeat(db, terminal):
    _, staff, admin, _, _, ticket = complete_ticket(db)
    first = primary_snapshot(ticket)
    repeat = episodes.create(db, admin, ticket.id, ticket.version, 1, "Repeat review")
    db.commit()
    episodes.transition(db, staff, repeat.id, terminal, repeat.version, "Repeat no longer possible")
    db.commit()
    new = episodes.create(db, admin, ticket.id, ticket.version, 2, "Another supervised attempt")
    db.commit()
    assert new.number == 2 and primary_snapshot(ticket) == first
    assert domain.seat_for(db, ticket).state == "CONSUMED"


def test_repeat_respects_closing_and_same_day_rule(db, clock, monkeypatch):
    _, staff, admin, days, _, ticket = complete_ticket(db)
    clock[0] = at(days[0], 13, 55)
    monkeypatch.setattr(settings(), "enforce_hours", True)
    repeat = episodes.create(db, admin, ticket.id, ticket.version, 1, "Repeat review")
    for action in ("verify", "start"):
        episodes.transition(db, staff, repeat.id, action, repeat.version)
    db.commit()
    clock[0] = at(days[0], 14)
    episodes.transition(db, staff, repeat.id, "complete", repeat.version)
    db.commit()
    with pytest.raises(HTTPException, match="SERVICE_CLOSED"):
        episodes.create(db, admin, ticket.id, ticket.version, 1, "Too late")
    monkeypatch.setattr(settings(), "enforce_hours", False)
    clock[0] = at(days[0], 8) - timedelta(days=1)
    with pytest.raises(HTTPException, match="REPEAT_SAME_DAY_REQUIRED"):
        episodes.create(db, admin, ticket.id, ticket.version, 1, "Wrong day")


def test_historical_unfinished_repeat_is_visible_and_can_be_cancelled(db, clock):
    _, staff, admin, days, _, ticket = complete_ticket(db)
    repeat = episodes.create(db, admin, ticket.id, ticket.version, 1, "Repeat review")
    db.commit()
    clock[0] = at(days[1], 9)
    with TestClient(app) as client:
        login(client, "staff")
        assert days[0].id in {d["id"] for d in client.get("/api/v1/staff/days").json()}
        report = client.get(f"/api/v1/staff/days/{days[0].id}/summary").json()
        assert report["unresolved"] == [] and report["counts"]["COMPLETED"] == 1
        assert report["repeat_unresolved"][0]["id"] == repeat.id
    with pytest.raises(HTTPException, match="SERVICE_CLOSED"):
        episodes.transition(db, staff, repeat.id, "start", repeat.version)
    episodes.transition(db, staff, repeat.id, "cancel", repeat.version, "Closed without repeat service")
    db.commit()
    assert ticket.state == "COMPLETED"


def test_episode_scope_and_public_queue_do_not_expose_private_history(db):
    _, _, admin, days, _, ticket = complete_ticket(db)
    repeat = episodes.create(db, admin, ticket.id, ticket.version, 1, "Internal synthetic reason")
    db.commit()
    with TestClient(app) as client:
        public = client.get(f"/api/v1/queues/{days[0].id}").json()
        assert public["completed"] == 1
        assert public["calling"][0] == {
            "number": ticket.number,
            "label": f"A-{ticket.number:03d}",
            "state": "CALLED",
            "window": 1,
            "repeat": True,
        }
        assert "Internal synthetic reason" not in str(public)
        login(client, "applicant")
        assert client.get(f"/api/v1/staff/tickets/{ticket.id}/episodes").status_code == 403
        assert (
            client.post(
                f"/api/v1/staff/episodes/{repeat.id}/verify", json={"version": repeat.version}
            ).status_code
            == 403
        )
        login(client, "staff")
        db.scalar(select(User).where(User.role == "staff")).office_id = "other"
        db.commit()
        assert client.get(f"/api/v1/staff/tickets/{ticket.id}/episodes").status_code == 404
        assert (
            client.post(
                f"/api/v1/staff/episodes/{repeat.id}/verify", json={"version": repeat.version}
            ).status_code
            == 404
        )


def test_repeat_transition_rejects_stale_version(db):
    _, staff, admin, _, _, ticket = complete_ticket(db)
    repeat = episodes.create(db, admin, ticket.id, ticket.version, 1, "Repeat review")
    db.commit()
    version = repeat.version
    episodes.transition(db, staff, repeat.id, "verify", version)
    db.commit()
    with pytest.raises(HTTPException, match="VERSION_CONFLICT"):
        episodes.transition(db, staff, repeat.id, "start", version)
    assert repeat.state == "CALLED"


def test_staff_cancellation_requires_reason_and_version(db):
    _, staff, _, _, _, ticket = called_ticket(db)
    with pytest.raises(HTTPException, match="REASON_REQUIRED"):
        domain.cancel(db, staff, ticket.id)
    with pytest.raises(HTTPException, match="VERSION_CONFLICT"):
        domain.cancel(db, staff, ticket.id, "Applicant requested cancellation", ticket.version - 1)
    domain.cancel(db, staff, ticket.id, "Applicant requested cancellation", ticket.version)
    db.commit()
    assert ticket.state == "CANCELLED"
    assert (
        db.scalar(select(Audit).where(Audit.action == "CANCEL", Audit.target_id == ticket.id)).reason
        == "Applicant requested cancellation"
    )


def test_concurrent_repeat_creation_has_one_winner(db):
    _, _, admin, _, _, ticket = complete_ticket(db)
    ticket_id, version, actor_id = ticket.id, ticket.version, admin.id
    start = threading.Barrier(2)

    def attempt(window):
        with session_factory()() as session:
            start.wait(timeout=10)
            try:
                result = episodes.create(
                    session, session.get(User, actor_id), ticket_id, version, window, "Concurrent repeat"
                )
                session.commit()
                return result.id
            except HTTPException as error:
                session.rollback()
                return error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (1, 2)))
    assert results.count("VERSION_CONFLICT") == 1
    assert db.scalar(select(func.count()).select_from(ServiceEpisode)) == 1


def test_repeat_racing_ordinary_call_cannot_share_window(db):
    user, staff, admin, days, _, ticket = complete_ticket(db)
    document = new_document(db, user, "RACE00002")
    domain.book(db, user, document.id, days[0].id, 0)
    db.commit()
    ticket_id, version, day_id, staff_id, admin_id = ticket.id, ticket.version, days[0].id, staff.id, admin.id
    start = threading.Barrier(2)

    def attempt(repeat):
        with session_factory()() as session:
            start.wait(timeout=10)
            try:
                if repeat:
                    episodes.create(
                        session, session.get(User, admin_id), ticket_id, version, 1, "Race with ordinary call"
                    )
                else:
                    domain.next_ticket(session, session.get(User, staff_id), day_id, 1)
                session.commit()
                return "ok"
            except HTTPException as error:
                session.rollback()
                return error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, (True, False))) == ["WINDOW_BUSY", "ok"]
    db.expire_all()
    active_primary = db.scalar(
        select(func.count()).select_from(Ticket).where(Ticket.state.in_(("CALLED", "IN_SERVICE")))
    )
    active_repeat = db.scalar(
        select(func.count())
        .select_from(ServiceEpisode)
        .where(ServiceEpisode.state.in_(("CALLED", "IN_SERVICE")))
    )
    assert active_primary + active_repeat == 1
