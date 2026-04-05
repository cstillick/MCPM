"""
One-time script to bootstrap an admin user.
Run with: python create_admin.py
"""
import sys

from database import Base, engine, SessionLocal
from models import User
from auth import hash_password

# Ensure tables exist
Base.metadata.create_all(bind=engine)


def main():
    username = input("Admin username: ").strip()
    if not username:
        print("Error: username cannot be empty.")
        sys.exit(1)

    password = input("Admin password: ").strip()
    if len(password) < 6:
        print("Error: password must be at least 6 characters.")
        sys.exit(1)

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            print(f"Error: user '{username}' already exists.")
            sys.exit(1)

        user = User(
            username=username,
            password_hash=hash_password(password),
            is_admin=True,
            coin_balance=1000,
        )
        db.add(user)
        db.commit()
        print(f"Admin user '{username}' created successfully.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
