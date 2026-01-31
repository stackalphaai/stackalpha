"""
Seed script to create default admin user.
Run with: uv run python -m app.scripts.seed_admin
"""

import asyncio

from sqlalchemy import select

from app.core.security import get_password_hash
from app.database import AsyncSessionLocal
from app.models import User


async def seed_admin():
    """Create default admin user if not exists."""
    async with AsyncSessionLocal() as session:
        # Check if admin already exists
        result = await session.execute(
            select(User).where(User.email == "admin@stackalpha.com")
        )
        existing_admin = result.scalar_one_or_none()

        if existing_admin:
            print("Admin user already exists:")
            print(f"  Email: {existing_admin.email}")
            print(f"  Is Admin: {existing_admin.is_admin}")
            print(f"  Is Superadmin: {existing_admin.is_superadmin}")
            return

        # Create admin user
        admin_user = User(
            email="admin@stackalpha.com",
            hashed_password=get_password_hash("admin123"),
            full_name="System Administrator",
            is_active=True,
            is_verified=True,
            is_admin=True,
            is_superadmin=True,
        )

        session.add(admin_user)
        await session.commit()

        print("Admin user created successfully!")
        print("  Email: admin@stackalpha.com")
        print("  Password: admin123")
        print("  Role: Superadmin")
        print("")
        print("IMPORTANT: Change this password in production!")


if __name__ == "__main__":
    asyncio.run(seed_admin())
