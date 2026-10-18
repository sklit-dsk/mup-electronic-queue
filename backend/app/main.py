import io
import json
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from . import document_access, domain, episodes
from .config import settings
from .db import get_db
from .models import (
    Audit,
    Document,
    DocumentRequest,
    DocumentRevision,
    Idempotency,
    OfficeDay,
    Otp,
    Outbox,
    Seat,
    ServiceEpisode,
    Session,
    Ticket,
    Throttle,
    Upload,
    User,
    Waiting,
    uid,
)
from .security import cipher, decrypt, digest, encrypt, fail, keyed

DB = Annotated[DbSession, Depends(get_db)]


@asynccontextmanager
async def lifespan(app):
    settings()
    yield


app = FastAPI(title="Red • MUP Queue", version="0.1.0", lifespan=lifespan)


@app.get("/api/v1/schema", include_in_schema=False)
def schema():
    return app.openapi()


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings().allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"],
)


@app.middleware("http")
async def protect_origin(request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in settings().allowed_origins:
            return JSONResponse({"code": "ORIGIN_DENIED", "message": "Origin denied"}, 403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse({"code": str(exc.detail), "message": str(exc.detail)}, exc.status_code)


@app.exception_handler(IntegrityError)
async def conflict_error(request, exc):
    return JSONResponse({"code": "STATE_CONFLICT", "message": "Reload and try again"}, 409)


def current_user(request: Request, db: DB):
    token = request.cookies.get("session", "")
    session = db.get(Session, digest(token))
    if not session or session.expires_at <= datetime.now(UTC):
        fail("AUTH_REQUIRED", 401)
    if request.method != "GET" and not secrets.compare_digest(
        request.headers.get("x-csrf-token", ""), session.csrf
    ):
        fail("CSRF_REQUIRED", 403)
    user = db.get(User, session.user_id)
    if not user:
        fail("AUTH_REQUIRED", 401)
    return user


USER = Annotated[User, Depends(current_user)]


def staff(user):
    if user.role not in ("staff", "admin"):
        fail("FORBIDDEN", 403)


def admin(user):
    if user.role != "admin":
        fail("FORBIDDEN", 403)


def session_response(db, response, user):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    db.add(
        Session(
            token_hash=digest(token),
            user_id=user.id,
            csrf=csrf,
            expires_at=datetime.now(UTC) + timedelta(hours=12),
        )
    )
    db.commit()
    response.set_cookie(
        "session", token, httponly=True, secure=settings().app_env == "pilot", samesite="lax", max_age=43200
    )
    return {"user": user_view(user), "csrf": csrf}


def user_view(user):
    return {"id": user.id, "name": user.name, "role": user.role, "office": user.office_id}


def rate_limit(db, key, maximum, seconds=60):
    hashed = keyed("rate:" + key)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(hashed[:15], 16)})
    row = db.get(Throttle, hashed)
    now = datetime.now(UTC)
    if not row:
        row = Throttle(key=hashed, count=0, resets_at=now + timedelta(seconds=seconds))
        db.add(row)
    if row.resets_at <= now:
        row.count, row.resets_at = 0, now + timedelta(seconds=seconds)
    if row.count >= maximum:
        db.commit()
        fail("RATE_LIMIT", 429)
    row.count += 1
    db.commit()


def mutate(db, user, request, operation, payload, action):
    key = request.headers.get("idempotency-key", "")
    if not 8 <= len(key) <= 100:
        fail("IDEMPOTENCY_KEY_REQUIRED", 422)
    full = f"{user.id}:{operation}:{key}"
    lock_id = int(digest(full)[:15], 16)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
    request_hash = digest(json.dumps(payload, sort_keys=True, default=str))
    old = db.get(Idempotency, full)
    if old:
        if old.request_hash != request_hash:
            fail("IDEMPOTENCY_CONFLICT")
        return old.result
    result = action()
    db.add(Idempotency(key=full, request_hash=request_hash, result=result))
    db.commit()
    return result


class DemoLogin(BaseModel):
    role: str


class OtpRequest(BaseModel):
    phone: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")


class OtpVerify(BaseModel):
    challenge_id: str
    code: str = Field(pattern=r"^[0-9]{6}$")


class StaffLogin(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


class Fields(BaseModel):
    country: str = Field(min_length=3, max_length=3)
    number: str = Field(min_length=4, max_length=20)
    name: str = Field(min_length=2, max_length=150)
    birth_date: date
    expiry_date: date


class Booking(BaseModel):
    document_id: str
    day_id: str
    companion: int = Field(default=0, ge=0, le=1)


class Transfer(BaseModel):
    day_id: str
    reason: str = Field(default="", max_length=300)


class Call(BaseModel):
    day_id: str
    window: int = Field(ge=1, le=3)


class Action(BaseModel):
    version: int = Field(ge=1)
    reason: str = Field(default="", max_length=300)


class RepeatVisit(Action):
    window: int = Field(ge=1, le=3)
    reason: str = Field(min_length=1, max_length=300)


class EpisodeAction(Action):
    window: int | None = Field(default=None, ge=1, le=3)


class CancelRequest(BaseModel):
    reason: str = Field(default="", max_length=300)
    version: int | None = Field(default=None, ge=1)


class AccessRequest(BaseModel):
    kind: Literal["RECOVER", "REPLACE"]
    upload_id: str
    document_id: str | None = None


class AccessDecision(Action):
    reason: str = Field(min_length=5, max_length=300)
    original_checked: bool = False
    account_checked: bool = False
    same_person_checked: bool = False


class DayChange(BaseModel):
    closed: bool
    reason: str = Field(min_length=3, max_length=300)


class Review(BaseModel):
    fields: Fields
    reason: str = Field(min_length=5, max_length=300)


@app.get("/api/v1/health")
def health(db: DB):
    db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "mode": settings().app_env,
        "capacity": 100,
        "timezone": settings().timezone,
        "ocr": settings().document_reader,
        "sms": "webhook" if settings().sms_webhook_url else "not_connected",
    }


@app.post("/api/v1/auth/demo")
def demo_login(body: DemoLogin, response: Response, db: DB):
    if settings().app_env != "demo" or body.role not in ("applicant", "staff", "admin"):
        fail("NOT_FOUND", 404)
    user = db.scalar(select(User).where(User.phone == f"demo:{body.role}"))
    if not user:
        fail("RUN_SEED", 503)
    return session_response(db, response, user)


@app.post("/api/v1/auth/otp/request")
def request_otp(body: OtpRequest, request: Request, db: DB):
    rate_limit(db, "otp-phone:" + body.phone, 1)
    rate_limit(db, "otp-ip:" + (request.client.host if request.client else "unknown"), 20)
    recent = db.scalar(
        select(func.count())
        .select_from(Otp)
        .where(Otp.created_at > datetime.now(UTC) - timedelta(minutes=1), Otp.phone == body.phone)
    )
    if recent:
        fail("OTP_RATE_LIMIT", 429)
    code = str(secrets.randbelow(900000) + 100000)
    row = Otp(phone=body.phone, code_hash=keyed(code), expires_at=datetime.now(UTC) + timedelta(minutes=5))
    db.add(row)
    db.flush()
    if settings().sms_webhook_url:
        import httpx

        try:
            result = httpx.post(
                settings().sms_webhook_url,
                json={"to": body.phone, "message": f"Red code: {code}"},
                headers={"Authorization": f"Bearer {settings().sms_webhook_token}"},
                timeout=10,
            )
            result.raise_for_status()
        except httpx.HTTPError:
            fail("SMS_UNAVAILABLE", 503)
    elif settings().app_env != "demo":
        fail("SMS_NOT_CONFIGURED", 503)
    db.commit()
    # No OTP is returned, including demo. Demo uses an explicitly labelled role login.
    return {"challenge_id": row.id, "expires_in": 300}


@app.post("/api/v1/auth/otp/verify")
def verify_otp(body: OtpVerify, response: Response, db: DB):
    row = db.scalar(select(Otp).where(Otp.id == body.challenge_id).with_for_update())
    if not row or row.consumed or row.attempts >= 5 or row.expires_at <= datetime.now(UTC):
        fail("OTP_INVALID", 401)
    row.attempts += 1
    if not secrets.compare_digest(row.code_hash, keyed(body.code)):
        db.commit()
        fail("OTP_INVALID", 401)
    row.consumed = True
    user = db.scalar(select(User).where(User.phone == row.phone))
    if not user:
        user = User(phone=row.phone, name="Заявитель", role="applicant")
        db.add(user)
        db.flush()
    return session_response(db, response, user)


@app.post("/api/v1/auth/staff")
def staff_login(body: StaffLogin, request: Request, response: Response, db: DB):
    rate_limit(db, "staff-login:" + body.username, 10)
    user = db.scalar(select(User).where(User.phone == body.username, User.role.in_(("staff", "admin"))))
    try:
        if (
            not user
            or not user.password_hash
            or not PasswordHasher().verify(user.password_hash, body.password)
        ):
            fail("LOGIN_FAILED", 401)
    except (VerifyMismatchError, InvalidHashError):
        fail("LOGIN_FAILED", 401)
    return session_response(db, response, user)


@app.get("/api/v1/me")
def me(request: Request, user: USER, db: DB):
    session = db.get(Session, digest(request.cookies["session"]))
    return {"user": user_view(user), "csrf": session.csrf}


@app.post("/api/v1/auth/logout")
def logout(request: Request, response: Response, user: USER, db: DB):
    db.delete(db.get(Session, digest(request.cookies["session"])))
    db.commit()
    response.delete_cookie("session")
    return {"ok": True}


def upload_owned(db, upload_id, user):
    upload = db.get(Upload, upload_id)
    if not upload or (upload.owner_id != user.id and user.role != "admin"):
        fail("NOT_FOUND", 404)
    if upload.owner_id != user.id and db.get(User, upload.owner_id).office_id != user.office_id:
        fail("NOT_FOUND", 404)
    return upload


@app.post("/api/v1/documents/uploads")
def upload_document(user: USER, db: DB, file: UploadFile = File()):
    rate_limit(db, "upload:" + user.id, 10)
    raw = file.file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        fail("FILE_TOO_LARGE", 413)
    Image.MAX_IMAGE_PIXELS = 20_000_000
    try:
        with Image.open(io.BytesIO(raw)) as original:
            if original.format not in ("JPEG", "PNG") or original.width * original.height > 20_000_000:
                fail("UNSUPPORTED_IMAGE", 422)
            original.load()
            image = ImageOps.exif_transpose(original).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        fail("INVALID_IMAGE", 422)
    file_key = uid() + ".enc"
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=92)
    (settings().data_dir / file_key).write_bytes(cipher().encrypt(buffer.getvalue()))
    upload = Upload(
        owner_id=user.id,
        file_key=file_key,
        expires_at=datetime.now(UTC) + timedelta(hours=settings().photo_retention_hours),
    )
    db.add(upload)
    db.commit()
    return {"id": upload.id, "status": upload.status}


@app.post("/api/v1/documents/demo")
def demo_document(user: USER, db: DB):
    if settings().app_env != "demo":
        fail("NOT_FOUND", 404)
    fields = {
        "country": "UTO",
        "number": "T" + secrets.token_hex(4).upper(),
        "name": "TEST APPLICANT",
        "birth_date": "1998-03-14",
        "expiry_date": "2030-10-15",
    }
    row = Upload(
        owner_id=user.id,
        demo=True,
        status="PASSED",
        expires_at=datetime.now(UTC) + timedelta(days=1),
        encrypted_result=encrypt({"fields": fields, "issues": [], "engine": "synthetic-demo"}),
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "status": row.status, "fields": fields, "demo": True}


@app.get("/api/v1/documents/checks/{upload_id}")
def get_check(upload_id: str, user: USER, db: DB):
    row = upload_owned(db, upload_id, user)
    result = decrypt(row.encrypted_result) if row.encrypted_result else {}
    return {"id": row.id, "status": row.status, "demo": row.demo, "error": row.error, **result}


@app.post("/api/v1/documents/checks/{upload_id}/confirm")
def confirm_check(upload_id: str, body: Fields, user: USER, db: DB):
    document = document_access.confirm(db, user, upload_id, body.model_dump(mode="json"))
    db.commit()
    return {
        "id": document.id,
        "fields": decrypt(document.encrypted_fields),
        "verified": bool(document.verified_at),
        "version": document.version,
        "revision": document.revision,
    }


@app.get("/api/v1/documents/mine")
def my_documents(user: USER, db: DB):
    return [
        {
            "id": d.id,
            "fields": decrypt(d.encrypted_fields),
            "verified": bool(d.verified_at),
            "version": d.version,
            "revision": d.revision,
        }
        for d in db.scalars(
            select(Document).where(Document.owner_id == user.id).order_by(Document.created_at)
        )
    ]


@app.post("/api/v1/document-requests")
def create_document_request(body: AccessRequest, request: Request, user: USER, db: DB):
    return mutate(
        db,
        user,
        request,
        "document-request",
        body.model_dump(),
        lambda: document_access.request_view(
            document_access.create_request(db, user, body.kind, body.upload_id, body.document_id)
        ),
    )


@app.get("/api/v1/document-requests/mine")
def my_document_requests(user: USER, db: DB):
    return [
        document_access.request_view(row)
        for row in db.scalars(
            select(DocumentRequest)
            .where(DocumentRequest.requester_id == user.id)
            .order_by(DocumentRequest.created_at.desc())
            .limit(100)
        )
    ]


@app.get("/api/v1/documents/{document_id}/history")
def document_history(document_id: str, user: USER, db: DB):
    doc = db.get(Document, document_id)
    if not doc or doc.owner_id != user.id:
        fail("NOT_FOUND", 404)
    return [
        {
            "revision": r.revision,
            "current": r.revision == doc.revision,
            "fields": decrypt(r.encrypted_fields),
            "created_at": domain.iso_utc(r.created_at),
        }
        for r in db.scalars(
            select(DocumentRevision)
            .where(DocumentRevision.document_id == doc.id)
            .order_by(DocumentRevision.revision)
        )
    ]


@app.post("/api/v1/document-requests/{request_id}/cancel")
def cancel_document_request(request_id: str, body: Action, request: Request, user: USER, db: DB):
    def action():
        row = db.get(DocumentRequest, request_id, with_for_update=True)
        if not row or row.requester_id != user.id:
            fail("NOT_FOUND", 404)
        if row.version != body.version or row.state != "PENDING":
            fail("VERSION_CONFLICT")
        row.state, row.version = "CANCELLED", row.version + 1
        domain.audit(db, user, "DOCUMENT_REQUEST_CANCEL", row.id)
        return document_access.request_view(row)

    return mutate(
        db, user, request, "document-request-cancel", {"id": request_id, **body.model_dump()}, action
    )


@app.get("/api/v1/admin/document-requests")
def document_requests(user: USER, db: DB):
    admin(user)
    result = []
    for row in db.scalars(
        select(DocumentRequest)
        .where(DocumentRequest.office_id == user.office_id, DocumentRequest.state == "PENDING")
        .order_by(DocumentRequest.created_at)
    ):
        upload = db.get(Upload, row.upload_id)
        fields = decrypt(upload.encrypted_result).get("fields", {}) if upload.encrypted_result else {}
        target = db.get(User, row.requester_id)
        doc = db.get(Document, row.document_id) if row.document_id else None
        result.append(
            {
                **document_access.request_view(row),
                "upload_id": row.upload_id,
                "fields": fields,
                "target_phone": target.phone,
                "has_image": bool(upload.file_key),
                "current_fields": decrypt(doc.encrypted_fields) if doc else None,
                "expired": upload.expires_at <= domain.utc_now() or upload.status != "PASSED",
            }
        )
    return result


@app.post("/api/v1/admin/document-requests/{request_id}/{decision}")
def resolve_document_request(
    request_id: str, decision: str, body: AccessDecision, request: Request, user: USER, db: DB
):
    admin(user)
    return mutate(
        db,
        user,
        request,
        "document-request-" + decision,
        {"id": request_id, **body.model_dump()},
        lambda: document_access.request_view(
            document_access.resolve(
                db,
                user,
                request_id,
                decision,
                body.version,
                body.reason,
                body.original_checked,
                body.account_checked,
                body.same_person_checked,
            )
        ),
    )


@app.get("/api/v1/documents/uploads/{upload_id}/image")
def image_file(upload_id: str, user: USER, db: DB):
    row = upload_owned(db, upload_id, user)
    if not row.file_key or row.expires_at <= datetime.now(UTC):
        fail("NOT_FOUND", 404)
    domain.audit(db, user, "PHOTO_VIEW", row.id)
    db.commit()
    return Response(
        cipher().decrypt((settings().data_dir / row.file_key).read_bytes()), media_type="image/jpeg"
    )


@app.get("/api/v1/days")
def days(db: DB):
    return [
        day_view(db, day)
        for day in db.scalars(
            select(OfficeDay).where(OfficeDay.date >= domain.local_now().date()).order_by(OfficeDay.date)
        )
    ]


def day_view(db, day):
    counts = dict(
        db.execute(select(Seat.state, func.count()).where(Seat.day_id == day.id).group_by(Seat.state)).all()
    )
    return {
        "id": day.id,
        "date": str(day.date),
        "closed": day.closed,
        "capacity": 100,
        "free": counts.get("FREE", 0),
        "held": counts.get("HELD", 0),
        "allocated": counts.get("ALLOCATED", 0),
        "consumed": counts.get("CONSUMED", 0),
        "service_status": domain.service_status(day),
    }


@app.get("/api/v1/staff/days")
def staff_days(user: USER, db: DB):
    staff(user)
    unfinished = (
        select(Ticket.id).where(Ticket.day_id == OfficeDay.id, Ticket.state.in_(domain.ACTIVE)).exists()
    )
    repeat_unfinished = (
        select(ServiceEpisode.id)
        .where(
            ServiceEpisode.day_id == OfficeDay.id,
            ServiceEpisode.state.in_(episodes.ACTIVE),
        )
        .exists()
    )
    return [
        day_view(db, day)
        for day in db.scalars(
            select(OfficeDay)
            .where(
                OfficeDay.office_id == user.office_id,
                or_(OfficeDay.date >= domain.local_now().date(), unfinished, repeat_unfinished),
            )
            .order_by(OfficeDay.date)
        )
    ]


@app.get("/api/v1/staff/days/{day_id}/summary")
def workday_summary(day_id: str, user: USER, db: DB):
    staff(user)
    day = db.get(OfficeDay, day_id)
    if not day or day.office_id != user.office_id:
        fail("NOT_FOUND", 404)
    tickets = list(db.scalars(select(Ticket).where(Ticket.day_id == day.id).order_by(Ticket.number)))
    unresolved = [t for t in tickets if t.state in ("BOOKED", "CALLED", "SKIPPED")]
    repeats = list(
        db.scalars(
            select(ServiceEpisode).where(ServiceEpisode.day_id == day.id).order_by(ServiceEpisode.created_at)
        )
    )
    return {
        **day_view(db, day),
        "server_now": domain.utc_now().isoformat(),
        "hours_enforced": settings().enforce_hours,
        "recall_interval_seconds": settings().recall_interval_seconds,
        "counts": {
            state: sum(t.state == state for t in tickets)
            for state in (
                "BOOKED",
                "CALLED",
                "SKIPPED",
                "IN_SERVICE",
                "COMPLETED",
                "NO_SHOW",
                "REJECTED",
                "TRANSFERRED",
            )
        },
        "unresolved": [domain.ticket_view(db, t) for t in unresolved],
        "in_service": [domain.ticket_view(db, t) for t in tickets if t.state == "IN_SERVICE"],
        "repeat_unresolved": [domain.episode_view(e) for e in repeats if e.state in ("CALLED", "SKIPPED")],
        "repeat_in_service": [domain.episode_view(e) for e in repeats if e.state == "IN_SERVICE"],
        "repeat_completed": sum(e.state == "COMPLETED" for e in repeats),
    }


@app.post("/api/v1/tickets")
def create_ticket(body: Booking, request: Request, user: USER, db: DB):
    return mutate(
        db,
        user,
        request,
        "book",
        body.model_dump(),
        lambda: domain.ticket_view(db, domain.book(db, user, body.document_id, body.day_id, body.companion)),
    )


@app.get("/api/v1/tickets/mine")
def tickets_mine(user: USER, db: DB):
    return [
        domain.ticket_view(db, t)
        for t in db.scalars(
            select(Ticket).where(Ticket.owner_id == user.id).order_by(Ticket.created_at.desc())
        )
    ]


@app.post("/api/v1/tickets/{ticket_id}/cancel")
def cancel_ticket(ticket_id: str, request: Request, user: USER, db: DB, body: CancelRequest | None = None):
    body = body or CancelRequest()
    return mutate(
        db,
        user,
        request,
        "cancel",
        {"id": ticket_id, **body.model_dump()},
        lambda: domain.ticket_view(db, domain.cancel(db, user, ticket_id, body.reason, body.version)),
    )


@app.post("/api/v1/tickets/{ticket_id}/transfer")
def transfer_ticket(ticket_id: str, body: Transfer, request: Request, user: USER, db: DB):
    return mutate(
        db,
        user,
        request,
        "transfer",
        {"id": ticket_id, **body.model_dump()},
        lambda: domain.ticket_view(db, domain.transfer(db, user, ticket_id, body.day_id, body.reason)),
    )


@app.get("/api/v1/queues/{day_id}")
def public_queue(day_id: str, db: DB):
    day = db.get(OfficeDay, day_id)
    if not day:
        fail("NOT_FOUND", 404)
    tickets = list(db.scalars(select(Ticket).where(Ticket.day_id == day_id).order_by(Ticket.number)))

    def safe(t):
        return {"number": t.number, "label": f"A-{t.number:03d}", "state": t.state, "window": t.window}

    repeats = db.execute(
        select(ServiceEpisode, Ticket)
        .join(Ticket, ServiceEpisode.ticket_id == Ticket.id)
        .where(
            ServiceEpisode.day_id == day_id,
            ServiceEpisode.state.in_(("CALLED", "IN_SERVICE")),
        )
        .order_by(ServiceEpisode.created_at)
    ).all()

    return {
        "date": str(day.date),
        "closed": day.closed,
        "service_status": domain.service_status(day),
        "calling": [safe(t) for t in tickets if t.state in ("CALLED", "IN_SERVICE")]
        + [{**safe(t), "state": e.state, "window": e.window, "repeat": True} for e, t in repeats],
        "waiting": [safe(t) for t in tickets if t.state == "BOOKED"],
        "completed": sum(t.state == "COMPLETED" for t in tickets),
        "revision": sum(t.version for t in tickets),
    }


@app.get("/api/v1/staff/tickets")
def staff_tickets(day_id: str, user: USER, db: DB):
    staff(user)
    day = db.get(OfficeDay, day_id)
    if not day or day.office_id != user.office_id:
        fail("NOT_FOUND", 404)
    return [
        domain.ticket_view(db, t)
        for t in db.scalars(select(Ticket).where(Ticket.day_id == day_id).order_by(Ticket.number))
    ]


@app.get("/api/v1/staff/tickets/{ticket_id}/document")
def staff_document(ticket_id: str, user: USER, db: DB):
    staff(user)
    ticket = domain.scoped_ticket(db, ticket_id, user)
    document = db.get(Document, ticket.document_id)
    domain.audit(db, user, "DOCUMENT_VIEW", document.id)
    db.commit()
    original = db.get(DocumentRevision, (document.id, ticket.document_revision))
    return {
        "fields": decrypt(document.encrypted_fields),
        "verified": bool(document.verified_at),
        "changed_since_booking": ticket.document_revision != document.revision,
        "booked_fields": decrypt(original.encrypted_fields) if original else None,
    }


@app.post("/api/v1/staff/call-next")
def call_next(body: Call, request: Request, user: USER, db: DB):
    staff(user)

    def action():
        ticket = domain.next_ticket(db, user, body.day_id, body.window)
        return domain.ticket_view(db, ticket) if ticket else {"empty": True}

    return mutate(db, user, request, "call", body.model_dump(), action)


@app.post("/api/v1/staff/tickets/{ticket_id}/episodes")
def create_episode(ticket_id: str, body: RepeatVisit, request: Request, user: USER, db: DB):
    staff(user)
    return mutate(
        db,
        user,
        request,
        "episode-create",
        {"id": ticket_id, **body.model_dump()},
        lambda: domain.episode_view(
            episodes.create(db, user, ticket_id, body.version, body.window, body.reason)
        ),
    )


@app.get("/api/v1/staff/tickets/{ticket_id}/episodes")
def episode_history(ticket_id: str, user: USER, db: DB):
    staff(user)
    ticket = domain.scoped_ticket(db, ticket_id, user)
    return {
        "ticket": domain.ticket_view(db, ticket),
        "episodes": [
            {
                **domain.episode_view(e),
                "reason": e.reason,
                "created_by": e.created_by,
                "verified_by": e.verified_by,
                "verified_at": e.verified_at.isoformat() if e.verified_at else None,
            }
            for e in db.scalars(
                select(ServiceEpisode)
                .where(ServiceEpisode.ticket_id == ticket.id)
                .order_by(ServiceEpisode.number)
            )
        ],
    }


@app.post("/api/v1/staff/episodes/{episode_id}/{action}")
def episode_action(episode_id: str, action: str, body: EpisodeAction, request: Request, user: USER, db: DB):
    staff(user)
    return mutate(
        db,
        user,
        request,
        "episode-" + action,
        {"id": episode_id, **body.model_dump()},
        lambda: domain.episode_view(
            episodes.transition(db, user, episode_id, action, body.version, body.reason, body.window)
        ),
    )


@app.post("/api/v1/staff/tickets/{ticket_id}/{action}")
def staff_action(ticket_id: str, action: str, body: Action, request: Request, user: USER, db: DB):
    staff(user)
    return mutate(
        db,
        user,
        request,
        action,
        {"id": ticket_id, **body.model_dump()},
        lambda: domain.ticket_view(
            db, domain.transition(db, user, ticket_id, action, body.version, body.reason)
        ),
    )


@app.post("/api/v1/waitlist")
def waitlist_join(body: Booking, request: Request, user: USER, db: DB):
    def action():
        doc = domain.lock_doc(db, body.document_id, user)
        day = domain.lock_day(db, body.day_id)
        domain.validate_date(day.date)
        domain.check_claims(db, doc.id, day.date)
        if day.closed:
            fail("DAY_CLOSED")
        existing = db.scalar(
            select(Waiting).where(
                Waiting.document_id == doc.id,
                Waiting.day_id == day.id,
                Waiting.state.in_(("WAITING", "OFFERED")),
            )
        )
        if existing:
            return {"id": existing.id, "state": existing.state}
        row = Waiting(owner_id=user.id, document_id=doc.id, day_id=day.id, companion=body.companion)
        db.add(row)
        db.flush()
        domain.audit(db, user, "WAITLIST_JOIN", row.id)
        return {"id": row.id, "state": row.state}

    return mutate(db, user, request, "waitlist", body.model_dump(), action)


@app.get("/api/v1/waitlist/mine")
def my_waitlist(user: USER, db: DB):
    return [
        {
            "id": w.id,
            "day_id": w.day_id,
            "state": w.state,
            "expires_at": w.expires_at.isoformat() if w.expires_at else None,
            "ticket_id": w.ticket_id,
        }
        for w in db.scalars(
            select(Waiting).where(Waiting.owner_id == user.id).order_by(Waiting.created_at.desc())
        )
    ]


@app.post("/api/v1/waitlist/{entry_id}/{action}")
def waitlist_action(entry_id: str, action: str, request: Request, user: USER, db: DB):
    from .worker import finish_offer

    return mutate(
        db,
        user,
        request,
        f"waitlist-{action}",
        {"id": entry_id},
        lambda: finish_offer(db, user, entry_id, action),
    )


@app.get("/api/v1/notifications")
def notifications(user: USER, db: DB):
    return [
        {"id": n.id, "kind": n.kind, "payload": n.payload, "at": n.created_at.isoformat()}
        for n in db.scalars(
            select(Outbox).where(Outbox.owner_id == user.id).order_by(Outbox.created_at.desc()).limit(25)
        )
    ]


@app.get("/api/v1/admin/audit")
def audit_log(user: USER, db: DB):
    admin(user)
    return [
        {
            "id": a.id,
            "action": a.action,
            "target": a.target_id,
            "reason": a.reason,
            "at": a.created_at.isoformat(),
        }
        for a in db.scalars(select(Audit).order_by(Audit.created_at.desc()).limit(100))
    ]


@app.post("/api/v1/admin/days/{day_id}")
def day_change(day_id: str, body: DayChange, request: Request, user: USER, db: DB):
    admin(user)
    if not body.reason.strip():
        fail("REASON_REQUIRED", 422)

    def action():
        day = domain.lock_day(db, day_id)
        if day.office_id != user.office_id:
            fail("FORBIDDEN", 403)
        day.closed = body.closed
        domain.audit(db, user, "DAY_CLOSE" if body.closed else "DAY_OPEN", day.id, body.reason)
        return {"ok": True}

    return mutate(db, user, request, "day-change", {"id": day_id, **body.model_dump()}, action)


@app.get("/api/v1/admin/reviews")
def reviews(user: USER, db: DB):
    admin(user)
    return [
        {"id": u.id, "status": u.status, **(decrypt(u.encrypted_result) if u.encrypted_result else {})}
        for u in db.scalars(
            select(Upload).where(Upload.status == "REVIEW_REQUIRED").order_by(Upload.created_at)
        )
    ]


@app.post("/api/v1/admin/reviews/{upload_id}")
def approve_review(upload_id: str, body: Review, user: USER, db: DB):
    admin(user)
    row = db.scalar(select(Upload).where(Upload.id == upload_id).with_for_update())
    if not row or row.status != "REVIEW_REQUIRED":
        fail("REVIEW_STATE_CONFLICT")
    row.status = "PASSED"
    row.encrypted_result = encrypt(
        {
            "fields": body.fields.model_dump(mode="json"),
            "issues": [],
            "engine": "manual-review",
            "reviewed_by": user.id,
        }
    )
    domain.audit(db, user, "DOCUMENT_MANUAL_REVIEW", row.id, body.reason)
    db.commit()
    return {"ok": True}
