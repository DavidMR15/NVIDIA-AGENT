from typing import Generator
from app.repositories import UserRepository, InMemoryUserRepository

# For production, we would return a different repository (e.g., database-backed)
# For now, we use the in-memory repository as the default.
def get_repository() -> UserRepository:
    return InMemoryUserRepository()