from abc import ABC, abstractmethod
from typing import Optional
from models.user import UserResponse
from passlib.context import CryptContext

# Use the same context as in the service for consistency
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

class UserRepository(ABC):
    @abstractmethod
    async def get_by_username(self, username: str) -> Optional[UserResponse]:
        ...

    @abstractmethod
    async def get_password_hash(self, username: str) -> Optional[str]:
        ...

    @abstractmethod
    async def create(self, username: str, hashed_password: str) -> UserResponse:
        ...

class InMemoryUserRepository(UserRepository):
    def __init__(self):
        self._users = {}  # username -> UserResponse
        self._passwords = {}  # username -> hashed_password
        self._next_id = 1

    async def get_by_username(self, username: str) -> Optional[UserResponse]:
        return self._users.get(username)

    async def get_password_hash(self, username: str) -> Optional[str]:
        return self._passwords.get(username)

    async def create(self, username: str, hashed_password: str) -> UserResponse:
        if username in self._users:
            raise ValueError("Username already exists")
        user = UserResponse(id=self._next_id, username=username)
        self._users[username] = user
        self._passwords[username] = hashed_password
        self._next_id += 1
        return user