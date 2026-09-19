"""Development helper: create (or update) a user directly in the database.

Usage (inside the api container or a local venv with DATABASE_URL configured):

    python -m app.scripts.create_user user@example.com "a-strong-password"
"""

import asyncio
import sys

from app.core.security import hash_password
from app.db.session import get_session_factory
from app.repositories.user_repository import UserRepository


async def create_or_update_user(email: str, password: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        repo = UserRepository(session)
        existing = await repo.get_by_email(email)
        if existing is not None:
            existing.password_hash = hash_password(password)
            existing.is_active = True
            await session.commit()
            print(f"Updated existing user: {email}")
            return

        user = await repo.create(email=email, password_hash=hash_password(password))
        print(f"Created user: {user.email} ({user.id})")


def main() -> None:
    if len(sys.argv) != 3:
        print('Usage: python -m app.scripts.create_user <email> "<password>"')
        sys.exit(1)

    _, email, password = sys.argv
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        sys.exit(1)

    asyncio.run(create_or_update_user(email, password))


if __name__ == "__main__":
    main()
