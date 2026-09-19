from app.core.security import hash_password, verify_password
from app.repositories.user_repository import UserRepository


async def test_create_and_get_by_email(db_session):
    repo = UserRepository(db_session)
    created = await repo.create(
        email="Someone@Example.com", password_hash=hash_password("hunter22")
    )

    assert created.id is not None
    assert created.is_active is True

    fetched = await repo.get_by_email("someone@example.com")
    assert fetched is not None
    assert fetched.id == created.id


async def test_get_by_email_returns_none_when_missing(db_session):
    repo = UserRepository(db_session)
    assert await repo.get_by_email("missing@example.com") is None


async def test_get_by_id_returns_none_when_missing(db_session):
    import uuid

    repo = UserRepository(db_session)
    assert await repo.get_by_id(uuid.uuid4()) is None


def test_password_hash_roundtrip():
    hashed = hash_password("correct-horse-battery")
    assert hashed != "correct-horse-battery"
    assert verify_password("correct-horse-battery", hashed)
    assert not verify_password("wrong", hashed)
