import os

import pytest

url = os.environ.get("TEST_DATABASE_URL", "")
if not url or "_test" not in url.rsplit("/", 1)[-1]:
    raise RuntimeError("Set TEST_DATABASE_URL to a dedicated PostgreSQL database ending in _test")
os.environ["DATABASE_URL"] = url
os.environ["APP_ENV"] = "demo"
os.environ["DATA_DIR"] = os.environ.get("TEST_DATA_DIR", "/tmp/mup-queue-test-files")

from app.db import engine, session_factory
from app import models
from app.seed import seed
from app.security import encrypt, fingerprint


@pytest.fixture(autouse=True)
def clean_db():
    with engine().begin() as conn:
        for table in reversed(models.Base.metadata.sorted_tables):
            conn.execute(models.text(f'TRUNCATE TABLE "{table.name}" CASCADE'))
    seed()
    yield


@pytest.fixture
def db():
    with session_factory()() as session:
        yield session


def new_document(db, user, number="TEST00001"):
    data = {
        "country": "UTO",
        "number": number,
        "name": "TEST APPLICANT",
        "birth_date": "1998-01-01",
        "expiry_date": "2030-01-01",
    }
    doc = models.Document(owner_id=user.id, fingerprint=fingerprint(data), encrypted_fields=encrypt(data))
    db.add(doc)
    db.flush()
    db.add(
        models.DocumentRevision(
            document_id=doc.id, revision=1, fingerprint=doc.fingerprint, encrypted_fields=doc.encrypted_fields
        )
    )
    db.flush()
    return doc
