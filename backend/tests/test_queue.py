from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import io
import threading

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import func, select

from app import domain
from app.config import Settings
from app.db import session_factory
from app.documents import checksum, parse_mrz
from app.main import app
from app.models import DayClaim, OfficeDay, Seat, Ticket, Upload, User, Waiting
from app.security import encrypt
from app.worker import cleanup, expire_offers, finish_offer, offer_places
from conftest import new_document


def setup_case(db):
    user = db.scalar(select(User).where(User.role == "applicant"))
    staff = db.scalar(select(User).where(User.role == "staff"))
    admin = db.scalar(select(User).where(User.role == "admin"))
    days = list(db.scalars(select(OfficeDay).order_by(OfficeDay.date)))
    doc = new_document(db, user)
    db.commit()
    return user, staff, admin, days, doc


def login(client, role="applicant"):
    response = client.post("/api/v1/auth/demo", json={"role": role})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    client.headers["Idempotency-Key"] = "test-key-0001"
    return response.json()["user"]


def test_110_requests_cannot_allocate_101_seats(db):
    user, _, _, days, _ = setup_case(db)
    docs = [new_document(db, user, f"LOAD{i:05d}").id for i in range(110)]
    db.commit()
    start = threading.Event()

    def attempt(doc_id):
        start.wait()
        with session_factory()() as s:
            try:
                ticket = domain.book(s, s.get(User, user.id), doc_id, days[0].id, 0)
                s.commit()
                return ticket.number
            except HTTPException as e:
                s.rollback()
                return e.detail

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(attempt, doc_id) for doc_id in docs]
        start.set()
        results = [f.result(timeout=30) for f in futures]
    assert len([r for r in results if isinstance(r, int)]) == 100
    assert results.count("DAY_FULL") == 10
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Ticket)) == 100
    assert (
        db.scalar(
            select(func.count()).select_from(Seat).where(Seat.day_id == days[0].id, Seat.state != "FREE")
        )
        == 100
    )


def test_same_passport_parallel_only_one_booking(db):
    user, _, _, days, doc = setup_case(db)
    start = threading.Event()

    def attempt(_):
        start.wait()
        with session_factory()() as s:
            try:
                domain.book(s, s.get(User, user.id), doc.id, days[0].id, 0)
                s.commit()
                return "ok"
            except HTTPException:
                return "conflict"

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(attempt, i) for i in range(10)]
        start.set()
        assert [f.result(timeout=30) for f in futures].count("ok") == 1


def test_cancel_releases_seat_and_rebooking(db):
    user, _, _, days, doc = setup_case(db)
    first = domain.book(db, user, doc.id, days[0].id, 0)
    db.commit()
    domain.cancel(db, user, first.id)
    db.commit()
    second = domain.book(db, user, doc.id, days[0].id, 1)
    db.commit()
    assert second.number == first.number + 1
    assert second.companion == 1


def test_transfer_failure_preserves_original(db):
    user, _, _, days, doc = setup_case(db)
    old = domain.book(db, user, doc.id, days[0].id, 0)
    db.commit()
    for seat in db.scalars(select(Seat).where(Seat.day_id == days[1].id)):
        seat.state, seat.offer_id = "HELD", f"dummy-{seat.ordinal}"
    db.commit()
    with pytest.raises(HTTPException, match="DAY_FULL"):
        domain.transfer(db, user, old.id, days[1].id)
    db.rollback()
    assert db.get(Ticket, old.id).state == "BOOKED"
    assert db.scalar(select(Seat).where(Seat.ticket_id == old.id)).state == "ALLOCATED"


def test_transfer_is_atomic_and_gets_new_number(db):
    user, _, _, days, doc = setup_case(db)
    old = domain.book(db, user, doc.id, days[0].id, 0)
    db.commit()
    new = domain.transfer(db, user, old.id, days[1].id)
    db.commit()
    assert old.state == "TRANSFERRED"
    assert db.get(DayClaim, (doc.id, days[0].date)) is None
    assert db.get(DayClaim, (doc.id, days[1].date)).ticket_id == new.id


def test_staff_cannot_start_without_original(db):
    user, staff, _, days, doc = setup_case(db)
    t = domain.book(db, user, doc.id, days[0].id, 0)
    db.commit()
    domain.next_ticket(db, staff, days[0].id, 1)
    db.commit()
    with pytest.raises(HTTPException, match="ORIGINAL_REQUIRED"):
        domain.transition(db, staff, t.id, "start", t.version)
    db.rollback()


def complete_ticket(db):
    user, staff, admin, days, doc = setup_case(db)
    t = domain.book(db, user, doc.id, days[0].id, 0)
    db.commit()
    domain.next_ticket(db, staff, days[0].id, 1)
    db.commit()
    for action in ("verify", "start", "complete"):
        domain.transition(db, staff, t.id, action, t.version)
        db.commit()
    return user, staff, admin, days, doc, t


def test_completed_does_not_free_capacity_or_day_claim(db):
    user, _, _, days, doc, t = complete_ticket(db)
    assert domain.seat_for(db, t).state == "CONSUMED"
    assert db.get(DayClaim, (doc.id, days[0].date)).state == "SPENT"
    with pytest.raises(HTTPException, match="DOCUMENT_DAY_LIMIT"):
        domain.book(db, user, doc.id, days[0].id, 0)


def test_completed_seat_cannot_be_released(db):
    _, _, admin, _, _, t = complete_ticket(db)
    with pytest.raises(HTTPException, match="RELEASE_NOT_ALLOWED"):
        domain.transition(db, admin, t.id, "release", t.version, "test reason")


def test_no_show_release_keeps_spent_claim(db, monkeypatch):
    clock = [domain.local_now().replace(hour=9, minute=0, second=0, microsecond=0).astimezone(timezone.utc)]
    monkeypatch.setattr(domain, "utc_now", lambda: clock[0])
    user, staff, admin, days, doc = setup_case(db)
    t = domain.book(db, user, doc.id, days[0].id, 0)
    db.commit()
    domain.next_ticket(db, staff, days[0].id, 1)
    db.commit()
    clock[0] += timedelta(seconds=120)
    domain.transition(db, staff, t.id, "recall", t.version)
    db.commit()
    clock[0] += timedelta(seconds=120)
    for a in ("skip", "no-show"):
        domain.transition(db, staff, t.id, a, t.version, "absent")
        db.commit()
    domain.transition(db, admin, t.id, "release", t.version, "unused allocation")
    db.commit()
    assert db.scalar(select(Seat).where(Seat.ticket_id == t.id)) is None
    assert db.get(DayClaim, (doc.id, days[0].date)).state == "SPENT"


def test_two_windows_call_distinct_tickets(db):
    user, staff, _, days, doc = setup_case(db)
    d2 = new_document(db, user, "PARALLEL2")
    domain.book(db, user, doc.id, days[0].id, 0)
    domain.book(db, user, d2.id, days[0].id, 0)
    db.commit()
    start = threading.Event()

    def call(w):
        start.wait()
        with session_factory()() as s:
            t = domain.next_ticket(s, s.get(User, staff.id), days[0].id, w)
            s.commit()
            return t.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(call, w) for w in (1, 2)]
        start.set()
        assert len({f.result(timeout=30) for f in futures}) == 2


def test_same_window_cannot_take_two(db):
    user, staff, _, days, doc = setup_case(db)
    domain.book(db, user, doc.id, days[0].id, 0)
    db.commit()
    domain.next_ticket(db, staff, days[0].id, 1)
    db.commit()
    with pytest.raises(HTTPException, match="WINDOW_BUSY"):
        domain.next_ticket(db, staff, days[0].id, 1)


def test_waitlist_offer_accept(db):
    user, _, _, days, doc = setup_case(db)
    entry = Waiting(owner_id=user.id, document_id=doc.id, day_id=days[0].id)
    db.add(entry)
    db.commit()
    offer_places()
    db.expire_all()
    assert db.get(Waiting, entry.id).state == "OFFERED"
    assert db.scalar(select(Seat).where(Seat.offer_id == entry.id)).state == "HELD"
    result = finish_offer(db, user, entry.id, "accept")
    db.commit()
    assert result["state"] == "BOOKED"
    assert db.get(DayClaim, (doc.id, days[0].date)).ticket_id == result["id"]


def test_waitlist_expire_frees_claim(db):
    user, _, _, days, doc = setup_case(db)
    entry = Waiting(owner_id=user.id, document_id=doc.id, day_id=days[0].id)
    db.add(entry)
    db.commit()
    offer_places()
    db.expire_all()
    db.get(Waiting, entry.id).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    expire_offers()
    db.expire_all()
    assert db.get(Waiting, entry.id).state == "EXPIRED"
    assert db.get(DayClaim, (doc.id, days[0].date)) is None


def test_future_limit(db):
    user, _, _, days, doc = setup_case(db)
    domain.book(db, user, doc.id, days[0].id, 0)
    db.commit()
    with pytest.raises(HTTPException, match="FUTURE_SERVICE_LIMIT"):
        domain.book(db, user, doc.id, days[1].id, 0)


def test_http_booking_idempotency_and_payload_conflict(db):
    user, _, _, days, doc = setup_case(db)
    with TestClient(app) as client:
        login(client)
        payload = {"document_id": doc.id, "day_id": days[0].id, "companion": 0}
        first = client.post("/api/v1/tickets", json=payload)
        assert first.status_code == 200, first.text
        second = client.post("/api/v1/tickets", json=payload)
        assert second.json()["id"] == first.json()["id"]
        assert client.post("/api/v1/tickets", json={**payload, "companion": 1}).status_code == 409


def test_access_csrf_and_companion_validation(db):
    user, _, _, days, doc = setup_case(db)
    with TestClient(app) as client:
        login(client)
        assert client.get("/api/v1/admin/audit").status_code == 403
        assert (
            client.post(
                "/api/v1/tickets", json={"document_id": doc.id, "day_id": days[0].id, "companion": 2}
            ).status_code
            == 422
        )
        del client.headers["X-CSRF-Token"]
        assert client.post("/api/v1/auth/logout").status_code == 403
        assert (
            client.post(
                "/api/v1/auth/demo", json={"role": "staff"}, headers={"Origin": "https://evil.invalid"}
            ).status_code
            == 403
        )


def test_role_boundaries_are_enforced_by_api(db):
    setup_case(db)
    with TestClient(app) as client:
        login(client, "applicant")
        assert client.get("/api/v1/staff/days").status_code == 403
        assert client.get("/api/v1/admin/audit").status_code == 403

        login(client, "staff")
        assert client.get("/api/v1/staff/days").status_code == 200
        assert client.get("/api/v1/admin/audit").status_code == 403

        login(client, "admin")
        assert client.get("/api/v1/staff/days").status_code == 200
        assert client.get("/api/v1/admin/audit").status_code == 200


def test_foreign_document_is_not_disclosed(db):
    user, _, _, days, doc = setup_case(db)
    other = User(phone="demo:other", name="OTHER")
    db.add(other)
    db.commit()
    with pytest.raises(HTTPException, match="NOT_FOUND"):
        domain.book(db, other, doc.id, days[0].id, 0)


def test_upload_is_private_and_encrypted(db):
    setup_case(db)
    raw = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(raw, "PNG")
    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/api/v1/documents/uploads", files={"file": ("test.png", raw.getvalue(), "image/png")}
        )
        assert response.status_code == 200, response.text
        upload_id = response.json()["id"]
        assert client.get(f"/api/v1/documents/uploads/{upload_id}/image").status_code == 200
        login(client, "staff")
        assert client.get(f"/api/v1/documents/uploads/{upload_id}/image").status_code == 404


def test_demo_document_change_cannot_bypass_check(db):
    setup_case(db)
    with TestClient(app) as client:
        login(client)
        check = client.post("/api/v1/documents/demo").json()
        fields = {**check["fields"], "number": "OTHER12345"}
        assert client.post(f"/api/v1/documents/checks/{check['id']}/confirm", json=fields).status_code == 409


def test_public_queue_has_no_personal_information(db):
    user, _, _, days, doc = setup_case(db)
    domain.book(db, user, doc.id, days[0].id, 0)
    db.commit()
    with TestClient(app) as client:
        response = client.get(f"/api/v1/queues/{days[0].id}")
        assert response.status_code == 200
        assert all(
            key not in response.text for key in ("document_id", "owner_id", "TEST APPLICANT", "TEST00001")
        )


def test_mrz_checksums():
    a = "P<UTOTEST<<APPLICANT<<<<<<<<<<<<<<<<<<<<<<<<<"
    a = a[:44].ljust(44, "<")
    number = "T12345678"
    b = (
        number
        + checksum(number)
        + "UTO"
        + "980101"
        + checksum("980101")
        + "M"
        + "301015"
        + checksum("301015")
        + "<" * 14
        + checksum("<" * 14)
    )
    b += checksum(b[:10] + b[13:20] + b[21:43])
    fields, issues = parse_mrz([a, b])
    assert fields["number"] == number
    assert not issues
    damaged = b[:9] + str((int(b[9]) + 1) % 10) + b[10:]
    assert "MRZ_CHECKSUM" in parse_mrz([a, damaged])[1]


def test_pilot_rejects_fake_or_missing_configuration():
    with pytest.raises(RuntimeError):
        Settings(app_env="pilot", document_reader="fake").validate_mode()


def test_photo_expiration_cleanup(db):
    user, _, _, _, _ = setup_case(db)
    from app.config import settings

    path = settings().data_dir / "test-expired.enc"
    path.write_bytes(b"test-only")
    upload = Upload(
        owner_id=user.id,
        file_key=path.name,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        encrypted_result=encrypt({"fields": {}}),
    )
    db.add(upload)
    db.commit()
    cleanup()
    db.expire_all()
    assert not path.exists()
    assert db.get(Upload, upload.id).encrypted_result is None


def test_stale_staff_action_rejected(db):
    user, staff, _, days, doc = setup_case(db)
    t = domain.book(db, user, doc.id, days[0].id, 0)
    db.commit()
    domain.next_ticket(db, staff, days[0].id, 1)
    db.commit()
    with pytest.raises(HTTPException, match="VERSION_CONFLICT"):
        domain.transition(db, staff, t.id, "verify", 1)


def test_waitlist_has_priority_over_new_booking(db):
    user, _, _, days, doc = setup_case(db)
    other_doc = new_document(db, user, "WAIT00002")
    entry = Waiting(owner_id=user.id, document_id=doc.id, day_id=days[0].id)
    db.add(entry)
    db.commit()
    with pytest.raises(HTTPException, match="WAITLIST_PRIORITY"):
        domain.book(db, user, other_doc.id, days[0].id, 0)
    db.rollback()
    offer_places()
    db.expire_all()
    assert db.get(Waiting, entry.id).state == "OFFERED"


def test_accept_expiration_race_does_not_leak_held_seat(db):
    user, _, _, days, doc = setup_case(db)
    entry = Waiting(owner_id=user.id, document_id=doc.id, day_id=days[0].id)
    db.add(entry)
    db.commit()
    offer_places()
    db.expire_all()
    db.get(Waiting, entry.id).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    user_id, entry_id = user.id, entry.id
    start = threading.Event()

    def run(action):
        start.wait()
        with session_factory()() as s:
            try:
                result = finish_offer(s, s.get(User, user_id), entry_id, action)
                s.commit()
                return result
            except HTTPException:
                s.rollback()
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        fs = [pool.submit(run, a) for a in ("accept", "expire")]
        start.set()
        result = [f.result(timeout=30) for f in fs]
    assert len([r for r in result if r]) == 1
    db.expire_all()
    assert db.scalar(select(Seat).where(Seat.offer_id == entry.id)) is None
    assert db.get(DayClaim, (doc.id, days[0].date)) is None


def test_real_ocr_synthetic_fixture_extracts_number():
    from pathlib import Path
    from app.documents import read_document

    result = read_document(Path(__file__).parent / "assets" / "synthetic_passport.png")
    assert result["fields"]["number"] == "123456789"
    assert result["fields"]["birth_date"] == "1998-01-01"
    # This fixture deliberately needs review; successful OCR is not proof of authenticity.
    assert result["status"] == "REVIEW_REQUIRED"
    assert "VISIBLE_NAME_MISMATCH" in result["issues"]


def test_end_to_end_api_from_document_to_completed(db):
    with TestClient(app) as client:
        login(client)
        check = client.post("/api/v1/documents/demo").json()
        document = client.post(f"/api/v1/documents/checks/{check['id']}/confirm", json=check["fields"])
        assert document.status_code == 200, document.text
        day = client.get("/api/v1/days").json()[0]
        booked = client.post(
            "/api/v1/tickets",
            json={"document_id": document.json()["id"], "day_id": day["id"], "companion": 1},
        )
        assert booked.status_code == 200, booked.text
        ticket_id = booked.json()["id"]
        login(client, "staff")
        called = client.post("/api/v1/staff/call-next", json={"day_id": day["id"], "window": 1})
        assert called.status_code == 200, called.text
        assert called.json()["id"] == ticket_id
        ticket = called.json()
        for n, action in enumerate(("verify", "start", "complete")):
            client.headers["Idempotency-Key"] = f"workflow-action-{n}"
            response = client.post(
                f"/api/v1/staff/tickets/{ticket_id}/{action}", json={"version": ticket["version"]}
            )
            assert response.status_code == 200, response.text
            ticket = response.json()
        assert ticket["state"] == "COMPLETED"
        login(client)
        assert client.get("/api/v1/tickets/mine").json()[0]["state"] == "COMPLETED"
