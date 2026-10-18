from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import secrets
import threading

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from app import document_access as access, domain
from app.db import session_factory
from app.main import app
from app.models import (
    DayClaim,
    Document,
    DocumentRequest,
    DocumentRevision,
    Outbox,
    Session,
    Ticket,
    Upload,
    User,
    Waiting,
)
from app.security import decrypt, digest, encrypt
from app.worker import finish_offer, offer_places
from conftest import new_document
from test_queue import complete_ticket, setup_case


def photo(db, user, fields, status="PASSED"):
    row = Upload(
        owner_id=user.id,
        status=status,
        demo=True,
        encrypted_result=encrypt({"fields": fields, "issues": []}),
        expires_at=domain.utc_now() + timedelta(hours=1),
    )
    db.add(row)
    db.flush()
    return row


def second_user(db):
    user = User(phone="+38269000001", name="SYNTHETIC ACCOUNT", role="applicant")
    db.add(user)
    db.flush()
    return user


def auth(db, client, user):
    token, csrf = secrets.token_hex(24), secrets.token_hex(24)
    db.add(
        Session(
            token_hash=digest(token),
            user_id=user.id,
            csrf=csrf,
            expires_at=domain.utc_now() + timedelta(hours=1),
        )
    )
    db.commit()
    client.cookies.set("session", token)
    client.headers["X-CSRF-Token"] = csrf
    client.headers["Idempotency-Key"] = "document-access-test-0001"


def approve(db, admin, row):
    result = access.resolve(
        db, admin, row.id, "approve", row.version, "Original and account checked in person", True, True, True
    )
    db.commit()
    return result


def recovery(db, user, doc):
    upload = photo(db, user, decrypt(doc.encrypted_fields))
    row = access.create_request(db, user, "RECOVER", upload.id)
    db.commit()
    return row


def replacement(db, user, doc, number="NEW000001"):
    fields = {**decrypt(doc.encrypted_fields), "number": number}
    upload = photo(db, user, fields)
    row = access.create_request(db, user, "REPLACE", upload.id, doc.id)
    db.commit()
    return row, fields


def test_photo_never_automatically_transfers_foreign_document(db):
    user, _, _, _, doc = setup_case(db)
    other = second_user(db)
    upload = photo(db, other, decrypt(doc.encrypted_fields))
    db.commit()
    with pytest.raises(HTTPException, match="DOCUMENT_ACCESS_REVIEW_REQUIRED"):
        access.confirm(db, other, upload.id, decrypt(doc.encrypted_fields))
    request = access.create_request(db, other, "RECOVER", upload.id)
    db.commit()
    assert request.state == "PENDING" and doc.owner_id == user.id
    assert set(access.request_view(request)) == {"id", "kind", "state", "version", "created_at", "decided_at"}


def test_recovery_moves_tickets_waitlist_and_notifications_without_changing_capacity(db):
    user, _, admin, days, doc = setup_case(db)
    ticket = domain.book(db, user, doc.id, days[0].id, 1)
    db.add(Waiting(owner_id=user.id, document_id=doc.id, day_id=days[1].id))
    other = second_user(db)
    db.commit()
    row = recovery(db, other, doc)
    approve(db, admin, row)
    db.expire_all()
    assert db.get(Document, doc.id).owner_id == other.id
    moved = db.get(Ticket, ticket.id)
    assert moved.owner_id == other.id and moved.number == ticket.number and moved.companion == 1
    assert db.scalar(select(Waiting)).owner_id == other.id
    assert db.scalar(select(Outbox).where(Outbox.kind == "BOOKED")).owner_id == other.id
    assert domain.seat_for(db, moved).state == "ALLOCATED"
    assert db.get(DayClaim, (doc.id, days[0].date)).ticket_id == ticket.id
    with pytest.raises(HTTPException, match="DOCUMENT_DAY_LIMIT"):
        domain.book(db, other, doc.id, days[0].id, 0)


def test_recovery_revokes_old_sessions_and_hides_records_from_previous_owner(db):
    user, _, admin, days, doc = setup_case(db)
    ticket = domain.book(db, user, doc.id, days[0].id, 0)
    other = second_user(db)
    db.commit()
    with TestClient(app) as old, TestClient(app) as new:
        auth(db, old, user)
        auth(db, new, other)
        row = recovery(db, other, doc)
        approve(db, admin, row)
        assert old.get("/api/v1/documents/mine").status_code == 401
        assert len(new.get("/api/v1/tickets/mine").json()) == 1
        auth(db, old, user)  # Even a fresh login cannot reclaim the recovered record.
        assert old.get("/api/v1/documents/mine").json() == []
        assert old.post(f"/api/v1/tickets/{ticket.id}/cancel").status_code == 404


def test_replacement_keeps_identity_claims_and_immutable_booked_revision(db):
    user, staff, admin, days, doc = setup_case(db)
    old_fields = decrypt(doc.encrypted_fields)
    ticket = domain.book(db, user, doc.id, days[0].id, 0)
    db.commit()
    row, fields = replacement(db, user, doc)
    approve(db, admin, row)
    db.expire_all()
    current = db.get(Document, doc.id)
    assert current.revision == 2 and decrypt(current.encrypted_fields) == fields
    assert db.get(Ticket, ticket.id).document_id == doc.id
    assert db.get(Ticket, ticket.id).document_revision == 1
    assert decrypt(db.get(DocumentRevision, (doc.id, 1)).encrypted_fields) == old_fields
    assert db.get(DayClaim, (doc.id, days[0].date)).ticket_id == ticket.id
    new_upload = photo(db, user, fields)
    assert access.confirm(db, user, new_upload.id, fields).id == doc.id
    with pytest.raises(HTTPException, match="DOCUMENT_DAY_LIMIT"):
        domain.book(db, user, doc.id, days[0].id, 0)
    with TestClient(app) as client:
        auth(db, client, staff)
        response = client.get(f"/api/v1/staff/tickets/{ticket.id}/document").json()
        assert (
            response["changed_since_booking"]
            and response["fields"] == fields
            and response["booked_fields"] == old_fields
        )


def test_old_passport_stays_reserved_after_replacement(db):
    user, _, admin, _, doc = setup_case(db)
    old_fields = decrypt(doc.encrypted_fields)
    old_upload = photo(db, user, old_fields)
    other = second_user(db)
    foreign_upload = photo(db, other, old_fields)
    db.commit()
    row, fields = replacement(db, user, doc)
    approve(db, admin, row)
    with pytest.raises(HTTPException, match="DOCUMENT_SUPERSEDED"):
        access.confirm(db, user, old_upload.id, old_fields)
    with pytest.raises(HTTPException, match="DOCUMENT_ACCESS_REVIEW_REQUIRED"):
        access.confirm(db, other, foreign_upload.id, old_fields)
    assert access.find_document(db, doc.fingerprint).id == doc.id
    assert db.scalar(select(func.count()).select_from(Document)) == 1
    assert db.scalar(select(func.count()).select_from(DocumentRevision)) == 2


def test_replacement_never_clears_spent_claim_or_consumed_seat(db):
    user, _, admin, days, doc, ticket = complete_ticket(db)
    first_end = ticket.completed_at
    row, _ = replacement(db, user, doc)
    approve(db, admin, row)
    db.expire_all()
    assert db.get(Ticket, ticket.id).completed_at == first_end
    assert domain.seat_for(db, ticket).state == "CONSUMED"
    assert db.get(DayClaim, (doc.id, days[0].date)).state == "SPENT"
    with pytest.raises(HTTPException, match="DOCUMENT_DAY_LIMIT"):
        domain.book(db, user, doc.id, days[0].id, 0)


def test_existing_different_document_is_not_silently_merged(db):
    user, _, admin, _, doc = setup_case(db)
    other_doc = new_document(db, user, "NEW000001")
    db.commit()
    row, _ = replacement(db, user, doc)
    with pytest.raises(HTTPException, match="DOCUMENT_REPLACEMENT_CONFLICT"):
        approve(db, admin, row)
    db.rollback()
    assert db.get(Document, doc.id).revision == 1 and db.get(Document, other_doc.id)


@pytest.mark.parametrize("flags", [(False, True, True), (True, False, True), (True, True, False)])
def test_approval_requires_all_in_person_checks(db, flags):
    user, _, admin, _, doc = setup_case(db)
    row, _ = replacement(db, user, doc)
    with pytest.raises(HTTPException, match="IDENTITY_CHECK_REQUIRED"):
        access.resolve(db, admin, row.id, "approve", row.version, "Manual decision", *flags)


def test_request_access_and_staff_scope_are_enforced(db):
    user, staff, admin, _, doc = setup_case(db)
    other = second_user(db)
    row = recovery(db, other, doc)
    with pytest.raises(HTTPException, match="FORBIDDEN"):
        access.resolve(db, staff, row.id, "approve", row.version, "Not an admin", True, True, True)
    admin.office_id = "another-office"
    db.commit()
    with pytest.raises(HTTPException, match="NOT_FOUND"):
        access.resolve(db, admin, row.id, "reject", row.version, "Wrong office")
    with TestClient(app) as client:
        auth(db, client, user)
        assert client.get("/api/v1/document-requests/mine").json() == []
        assert client.get("/api/v1/admin/document-requests").status_code == 403
        assert (
            client.post(
                f"/api/v1/document-requests/{row.id}/cancel", json={"version": row.version}
            ).status_code
            == 404
        )
        auth(db, client, other)
        assert client.get(f"/api/v1/documents/{doc.id}/history").status_code == 404


def test_expired_photo_and_stale_request_cannot_change_identity(db):
    user, _, admin, _, doc = setup_case(db)
    row, _ = replacement(db, user, doc)
    upload = db.get(Upload, row.upload_id)
    upload.expires_at = domain.utc_now() - timedelta(seconds=1)
    db.commit()
    with pytest.raises(HTTPException, match="DOCUMENT_UPLOAD_EXPIRED"):
        approve(db, admin, row)
    db.rollback()
    access.resolve(db, admin, row.id, "reject", row.version, "Upload expired, request a new photo")
    db.commit()
    row1, _ = replacement(db, user, doc, "NEXT00001")
    row2, _ = replacement(db, user, doc, "NEXT00002")
    approve(db, admin, row1)
    with pytest.raises(HTTPException, match="DOCUMENT_CHANGED"):
        approve(db, admin, row2)


def test_new_account_cannot_approve_recovery_with_another_users_photo(db):
    user, _, _, _, doc = setup_case(db)
    upload = photo(db, user, decrypt(doc.encrypted_fields))
    other = second_user(db)
    db.commit()
    with pytest.raises(HTTPException, match="NOT_FOUND"):
        access.create_request(db, other, "RECOVER", upload.id)
    with pytest.raises(HTTPException, match="NOT_FOUND"):
        access.create_request(
            db, other, "REPLACE", photo(db, other, decrypt(doc.encrypted_fields)).id, doc.id
        )


def test_in_service_document_cannot_change_but_request_can_be_rejected(db):
    user, staff, admin, days, doc = setup_case(db)
    ticket = domain.book(db, user, doc.id, days[0].id, 0)
    domain.next_ticket(db, staff, days[0].id, 1)
    for action in ("verify", "start"):
        domain.transition(db, staff, ticket.id, action, ticket.version)
    db.commit()
    row, _ = replacement(db, user, doc)
    with pytest.raises(HTTPException, match="DOCUMENT_IN_SERVICE"):
        approve(db, admin, row)
    db.rollback()
    access.resolve(db, admin, row.id, "reject", row.version, "Finish reception first")
    db.commit()
    assert row.state == "REJECTED"


def test_held_waitlist_offer_moves_to_recovered_account(db):
    user, _, admin, days, doc = setup_case(db)
    entry = Waiting(owner_id=user.id, document_id=doc.id, day_id=days[0].id)
    db.add(entry)
    other = second_user(db)
    db.commit()
    offer_places()
    db.expire_all()
    assert db.get(Waiting, entry.id).state == "OFFERED"
    row = recovery(db, other, doc)
    with session_factory()() as stale:
        old_actor = stale.get(User, user.id)
        stale_entry = stale.get(Waiting, entry.id)
        approve(db, admin, row)
        with pytest.raises(HTTPException, match="NOT_FOUND"):
            finish_offer(stale, old_actor, stale_entry.id, "accept")
    result = finish_offer(db, other, entry.id, "accept")
    db.commit()
    assert db.get(Ticket, result["id"]).owner_id == other.id


def test_request_creation_and_decision_are_idempotent_over_http(db):
    user, _, admin, _, doc = setup_case(db)
    upload = photo(db, user, {**decrypt(doc.encrypted_fields), "number": "HTTP00001"})
    db.commit()
    with TestClient(app) as client:
        auth(db, client, user)
        body = {"kind": "REPLACE", "document_id": doc.id, "upload_id": upload.id}
        first = client.post("/api/v1/document-requests", json=body)
        assert first.status_code == 200, first.text
        assert client.post("/api/v1/document-requests", json=body).json() == first.json()
        auth(db, client, admin)
        decision = {
            "version": first.json()["version"],
            "reason": "Documents checked in person",
            "original_checked": True,
            "account_checked": True,
            "same_person_checked": True,
        }
        url = f"/api/v1/admin/document-requests/{first.json()['id']}/approve"
        response = client.post(url, json=decision)
        assert response.status_code == 200, response.text
        assert client.post(url, json=decision).json() == response.json()
    db.expire_all()
    assert db.get(Document, doc.id).revision == 2
    assert db.scalar(select(func.count()).select_from(DocumentRequest)) == 1


def test_cancelled_request_cannot_be_approved(db):
    user, _, admin, _, doc = setup_case(db)
    row, _ = replacement(db, user, doc)
    with TestClient(app) as client:
        auth(db, client, user)
        response = client.post(f"/api/v1/document-requests/{row.id}/cancel", json={"version": row.version})
        assert response.status_code == 200
    db.expire_all()
    row = db.get(DocumentRequest, row.id)
    with pytest.raises(HTTPException, match="DOCUMENT_REQUEST_NOT_PENDING"):
        approve(db, admin, row)


def test_recovery_waits_for_in_flight_notification_and_suppresses_ambiguous_legacy(db):
    user, _, admin, days, doc = setup_case(db)
    domain.book(db, user, doc.id, days[0].id, 0)
    other = second_user(db)
    db.commit()
    message = db.scalar(select(Outbox).where(Outbox.kind == "BOOKED"))
    message.lease_until = domain.utc_now() + timedelta(seconds=60)
    legacy = Outbox(owner_id=user.id, kind="BOOKED", payload={})
    db.add(legacy)
    db.commit()
    row = recovery(db, other, doc)
    with pytest.raises(HTTPException, match="DELIVERY_IN_PROGRESS"):
        approve(db, admin, row)
    db.rollback()
    message = db.get(Outbox, message.id)
    message.lease_until = None
    db.commit()
    approve(db, admin, row)
    db.refresh(legacy)
    assert legacy.cancelled_at is not None


def test_cached_document_and_ticket_do_not_allow_old_owner_mutation_after_recovery(db):
    user, _, admin, days, doc = setup_case(db)
    ticket = domain.book(db, user, doc.id, days[0].id, 0)
    other = second_user(db)
    db.commit()
    row = recovery(db, other, doc)
    # A separate request has already loaded the old owner into its identity map.
    with session_factory()() as stale:
        old_user = stale.get(User, user.id)
        cached_document = stale.get(Document, doc.id)
        assert cached_document.owner_id == user.id
        old_ticket = stale.get(Ticket, ticket.id)
        approve(db, admin, row)
        with pytest.raises(HTTPException, match="NOT_FOUND"):
            domain.cancel(stale, old_user, old_ticket.id)
        stale.rollback()
        with pytest.raises(HTTPException, match="NOT_FOUND"):
            domain.book(stale, old_user, doc.id, days[1].id, 0)


def test_parallel_decisions_on_two_replacement_requests_have_one_winner(db):
    user, _, admin, _, doc = setup_case(db)
    one, _ = replacement(db, user, doc, "RACE00001")
    two, _ = replacement(db, user, doc, "RACE00002")
    admin_id, ids = admin.id, (one.id, two.id)
    barrier = threading.Barrier(2)

    def attempt(request_id):
        with session_factory()() as s:
            actor = s.get(User, admin_id)
            row = s.get(DocumentRequest, request_id)
            barrier.wait(timeout=10)
            try:
                approve(s, actor, row)
                return "ok"
            except HTTPException as error:
                s.rollback()
                return error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ids)) == ["DOCUMENT_CHANGED", "ok"]
    db.expire_all()
    assert db.get(Document, doc.id).revision == 2
    assert db.scalar(select(func.count()).select_from(DocumentRevision)) == 2


def test_pending_replacement_photo_cannot_be_confirmed_as_a_new_document(db):
    user, _, _, _, doc = setup_case(db)
    row, fields = replacement(db, user, doc, "PENDING01")
    with pytest.raises(HTTPException, match="DOCUMENT_ACCESS_REVIEW_REQUIRED"):
        access.confirm(db, user, row.upload_id, fields)
    assert db.scalar(select(func.count()).select_from(Document)) == 1


def test_confirmation_racing_replacement_never_creates_a_second_document(db):
    user, _, admin, _, doc = setup_case(db)
    row, fields = replacement(db, user, doc, "RACECONF1")
    user_id, admin_id, request_id, upload_id = user.id, admin.id, row.id, row.upload_id
    barrier = threading.Barrier(2)

    def decide():
        with session_factory()() as session:
            actor = session.get(User, admin_id)
            request = session.get(DocumentRequest, request_id)
            barrier.wait(timeout=10)
            try:
                approve(session, actor, request)
                return "approved"
            except HTTPException as error:
                session.rollback()
                return error.detail

    def confirm_photo():
        with session_factory()() as session:
            actor = session.get(User, user_id)
            barrier.wait(timeout=10)
            try:
                result = access.confirm(session, actor, upload_id, fields)
                session.commit()
                return result.id
            except HTTPException as error:
                session.rollback()
                return error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        decision = pool.submit(decide)
        confirmation = pool.submit(confirm_photo)
        assert decision.result() == "approved"
        assert confirmation.result() in (doc.id, "DOCUMENT_ACCESS_REVIEW_REQUIRED")
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Document)) == 1
    assert db.get(Document, doc.id).revision == 2
