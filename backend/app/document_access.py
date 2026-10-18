"""Reviewed access recovery and passport replacement on a stable document identity."""

from datetime import date

from sqlalchemy import delete, or_, select, text, update

from . import domain
from .models import (
    Document,
    DocumentRequest,
    DocumentRevision,
    Outbox,
    ServiceEpisode,
    Session,
    Ticket,
    Upload,
    User,
    Waiting,
)
from .security import decrypt, encrypt, fail, fingerprint


def lock_fingerprints(db, *values):
    for value in sorted(set(values)):
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(value[:15], 16)})


def find_document(db, fp):
    revision = db.scalar(select(DocumentRevision).where(DocumentRevision.fingerprint == fp))
    return (
        db.get(Document, revision.document_id)
        if revision
        else db.scalar(select(Document).where(Document.fingerprint == fp))
    )


def checked_upload(db, upload_id, owner_id=None):
    row = db.get(Upload, upload_id)
    if not row or (owner_id is not None and row.owner_id != owner_id):
        fail("NOT_FOUND", 404)
    if row.status != "PASSED" or not row.encrypted_result:
        fail("DOCUMENT_REVIEW_REQUIRED")
    if row.expires_at <= domain.utc_now():
        fail("DOCUMENT_UPLOAD_EXPIRED")
    fields = decrypt(row.encrypted_result).get("fields", {})
    if not all(key in fields for key in ("country", "number", "name", "birth_date", "expiry_date")):
        fail("DOCUMENT_REVIEW_REQUIRED")
    try:
        if (
            date.fromisoformat(fields["expiry_date"]) < domain.local_now().date()
            or date.fromisoformat(fields["birth_date"]) > domain.local_now().date()
        ):
            fail("DOCUMENT_DATE_INVALID", 422)
    except ValueError:
        fail("DOCUMENT_DATE_INVALID", 422)
    return row, fields, fingerprint(fields)


def confirm(db, actor, upload_id, submitted):
    row, fields, fp = checked_upload(db, upload_id, actor.id)
    if any(str(submitted.get(k, "")).upper().strip() != str(fields[k]).upper().strip() for k in fields):
        fail("DOCUMENT_DATA_MISMATCH")
    lock_fingerprints(db, fp)
    document = find_document(db, fp)
    if document:
        document = domain.lock_doc(db, document.id)
        if document.owner_id != actor.id:
            fail("DOCUMENT_ACCESS_REVIEW_REQUIRED")
        if document.fingerprint != fp:
            fail("DOCUMENT_SUPERSEDED")
    else:
        pending = db.scalar(
            select(DocumentRequest.id)
            .join(Upload, DocumentRequest.upload_id == Upload.id)
            .where(
                DocumentRequest.kind == "REPLACE",
                DocumentRequest.state == "PENDING",
                DocumentRequest.fingerprint == fp,
                Upload.expires_at > domain.utc_now(),
                Upload.status == "PASSED",
            )
            .limit(1)
        )
        if pending:
            fail("DOCUMENT_ACCESS_REVIEW_REQUIRED")
        document = Document(owner_id=actor.id, fingerprint=fp, encrypted_fields=encrypt(submitted))
        db.add(document)
        db.flush()
        db.add(
            DocumentRevision(
                document_id=document.id,
                revision=1,
                fingerprint=fp,
                encrypted_fields=document.encrypted_fields,
                created_by=actor.id,
            )
        )
    db.refresh(row, with_for_update=True)
    _, _, current_fp = checked_upload(db, upload_id, actor.id)
    if current_fp != fp:
        fail("DOCUMENT_DATA_MISMATCH")
    row.document_id = document.id
    domain.audit(db, actor, "DOCUMENT_CONFIRM", document.id)
    return document


def request_view(row):
    # Never reveal another account, canonical document or internal decision reason to the requester.
    return {
        "id": row.id,
        "kind": row.kind,
        "state": row.state,
        "version": row.version,
        "created_at": domain.iso_utc(row.created_at),
        "decided_at": domain.iso_utc(row.decided_at),
    }


def create_request(db, actor, kind, upload_id, document_id=None):
    if actor.role != "applicant":
        fail("FORBIDDEN", 403)
    row, fields, fp = checked_upload(db, upload_id, actor.id)
    lock_fingerprints(db, fp)
    document = domain.lock_doc(db, document_id, actor) if kind == "REPLACE" else find_document(db, fp)
    if document and kind == "RECOVER":
        document = domain.lock_doc(db, document.id)
    db.refresh(row, with_for_update=True)
    _, _, actual = checked_upload(db, upload_id, actor.id)
    if actual != fp:
        fail("DOCUMENT_DATA_MISMATCH")
    existing = db.scalar(
        select(DocumentRequest).where(
            DocumentRequest.requester_id == actor.id,
            DocumentRequest.kind == kind,
            DocumentRequest.upload_id == upload_id,
            DocumentRequest.state == "PENDING",
        )
    )
    if existing:
        return existing
    request = DocumentRequest(
        requester_id=actor.id,
        office_id=actor.office_id,
        kind=kind,
        upload_id=upload_id,
        document_id=document.id if document else None,
        fingerprint=fp,
        base_version=document.version if document else None,
        previous_owner_id=document.owner_id if document else None,
    )
    db.add(request)
    db.flush()
    domain.audit(db, actor, "DOCUMENT_REQUEST_" + kind, request.id)
    return request


def lock_related_days(db, doc):
    day_ids = set(db.scalars(select(Ticket.day_id).where(Ticket.document_id == doc.id)))
    day_ids.update(db.scalars(select(Waiting.day_id).where(Waiting.document_id == doc.id)))
    for day_id in sorted(day_ids):
        domain.lock_day(db, day_id)
    busy = db.scalar(
        select(Ticket.id).where(Ticket.document_id == doc.id, Ticket.state == "IN_SERVICE").limit(1)
    )
    repeat_busy = db.scalar(
        select(ServiceEpisode.id)
        .join(Ticket, ServiceEpisode.ticket_id == Ticket.id)
        .where(Ticket.document_id == doc.id, ServiceEpisode.state == "IN_SERVICE")
        .limit(1)
    )
    if busy or repeat_busy:
        fail("DOCUMENT_IN_SERVICE")


def resolve(
    db,
    actor,
    request_id,
    action,
    version,
    reason,
    original_checked=False,
    account_checked=False,
    same_person_checked=False,
):
    if actor.role != "admin":
        fail("FORBIDDEN", 403)
    request = db.get(DocumentRequest, request_id)
    if not request or request.office_id != actor.office_id:
        fail("NOT_FOUND", 404)
    if not reason.strip():
        fail("REASON_REQUIRED", 422)
    # Use the same fingerprint -> document -> day -> request/upload order as confirm and booking.
    lock_fingerprints(db, request.fingerprint)
    doc = domain.lock_doc(db, request.document_id) if request.document_id else None
    if doc and action == "approve":
        lock_related_days(db, doc)
    db.refresh(request, with_for_update=True)
    if request.version != version:
        fail("VERSION_CONFLICT")
    if request.state != "PENDING":
        fail("DOCUMENT_REQUEST_NOT_PENDING")
    if action == "approve":
        if not all((original_checked, account_checked, same_person_checked)):
            fail("IDENTITY_CHECK_REQUIRED", 422)
        if not doc or doc.version != request.base_version or doc.owner_id != request.previous_owner_id:
            fail("DOCUMENT_CHANGED")
        target = db.get(User, request.requester_id)
        if not target or target.role != "applicant":
            fail("DOCUMENT_ACCOUNT_INVALID")
        upload = db.get(Upload, request.upload_id)
        db.refresh(upload, with_for_update=True)
        _, fields, fp = checked_upload(db, upload.id, target.id)
        if fp != request.fingerprint:
            fail("DOCUMENT_DATA_MISMATCH")
        if request.kind == "RECOVER":
            if doc.fingerprint != fp:
                fail("DOCUMENT_SUPERSEDED")
            if doc.owner_id == target.id:
                fail("DOCUMENT_ALREADY_ACCESSIBLE")
            recover(db, actor, doc, target)
        else:
            if doc.owner_id != target.id:
                fail("DOCUMENT_CHANGED")
            other = find_document(db, fp)
            if other:
                fail("DOCUMENT_REPLACEMENT_CONFLICT")
            doc.revision += 1
            db.add(
                DocumentRevision(
                    document_id=doc.id,
                    revision=doc.revision,
                    fingerprint=fp,
                    encrypted_fields=encrypt(fields),
                    created_by=actor.id,
                )
            )
            doc.fingerprint, doc.encrypted_fields = fp, encrypt(fields)
        doc.version += 1
        doc.verified_at, doc.verified_by = domain.utc_now(), actor.id
        # Any called visit needs a fresh check at its window after identity data/access changes.
        db.execute(
            update(Ticket)
            .where(Ticket.document_id == doc.id, Ticket.state == "CALLED")
            .values(original_match=False)
        )
        episode_ids = select(Ticket.id).where(Ticket.document_id == doc.id)
        db.execute(
            update(ServiceEpisode)
            .where(ServiceEpisode.ticket_id.in_(episode_ids), ServiceEpisode.state == "CALLED")
            .values(
                original_match=False, verified_at=None, verified_by=None, version=ServiceEpisode.version + 1
            )
        )
        db.execute(update(Ticket).where(Ticket.document_id == doc.id).values(version=Ticket.version + 1))
        upload.document_id = doc.id
        request.state = "APPROVED"
        domain.notify(db, target.id, "DOCUMENT_UPDATED", document_id=doc.id)
    elif action == "reject":
        request.state = "REJECTED"
    else:
        fail("UNKNOWN_ACTION", 422)
    request.reason, request.decided_by, request.decided_at = reason.strip(), actor.id, domain.utc_now()
    request.version += 1
    domain.audit(db, actor, "DOCUMENT_REQUEST_" + action.upper(), request.id, reason)
    return request


def recover(db, actor, doc, target):
    previous = doc.owner_id
    messages = list(
        db.scalars(
            select(Outbox)
            .where(
                or_(
                    Outbox.document_id == doc.id,
                    (Outbox.owner_id == previous)
                    & Outbox.document_id.is_(None)
                    & Outbox.delivered_at.is_(None),
                )
            )
            .order_by(Outbox.id)
            .with_for_update()
        )
    )
    if any(m.lease_until and m.lease_until > domain.utc_now() and m.delivered_at is None for m in messages):
        fail("DELIVERY_IN_PROGRESS")
    for message in messages:
        if message.document_id == doc.id:
            message.owner_id = target.id
        elif message.delivered_at is None:
            # Legacy messages have no document reference; never guess who should receive them.
            message.cancelled_at = domain.utc_now()
    doc.owner_id = target.id
    db.execute(update(Ticket).where(Ticket.document_id == doc.id).values(owner_id=target.id))
    db.execute(update(Waiting).where(Waiting.document_id == doc.id).values(owner_id=target.id))
    db.execute(delete(Session).where(Session.user_id == previous))
    domain.notify(db, previous, "DOCUMENT_ACCESS_CHANGED")
    domain.audit(db, actor, "DOCUMENT_ACCESS_RECOVERED", doc.id)
