from datetime import timedelta

from argon2 import PasswordHasher
from sqlalchemy import select, text

from .config import settings
from .db import session_factory
from .domain import local_now
from .models import OfficeDay, Seat, User
from .security import secret


def seed():
    for key in ("encryption_key", "fingerprint_key", "session_secret"):
        secret(key)
    with session_factory()() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(7182201)"))
        for offset in range(settings().booking_horizon_days + 1):
            date_value = local_now().date() + timedelta(days=offset)
            key = f"podgorica:{date_value}"
            if not db.get(OfficeDay, key):
                db.add(OfficeDay(id=key, date=date_value))
                db.flush()
                for n in range(1, 101):
                    db.add(Seat(id=f"{key}:{n}", day_id=key, ordinal=n))
        if settings().app_env == "demo":
            for role, name in [
                ("applicant", "Данила Скловский"),
                ("staff", "Сотрудник MUP"),
                ("admin", "Администратор"),
            ]:
                contact = f"demo:{role}"
                if not db.scalar(select(User).where(User.phone == contact)):
                    db.add(User(phone=contact, name=name, role=role))
        if settings().staff_password and not db.scalar(select(User).where(User.phone == "staff")):
            db.add(
                User(
                    phone="staff",
                    name="Администратор",
                    role="admin",
                    password_hash=PasswordHasher().hash(settings().staff_password),
                )
            )
        db.commit()


if __name__ == "__main__":
    seed()
