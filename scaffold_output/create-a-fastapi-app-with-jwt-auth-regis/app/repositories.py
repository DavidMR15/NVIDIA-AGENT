from abc import ABC, abstractmethod
from typing import Optional

class UserRepository(ABC):
    @abstractmethod
    def get_by_email(self, email: str) -> Optional["User"]:
        ...

    @abstractmethod
    def get_by_id(self, id: int) -> Optional["User"]:
        ...

    @abstractmethod
    def create(self, email: str, hashed_password: str) -> "User":
        ...

class InMemoryUserRepository(UserRepository):
    def __init__(self):
        self.users = {}
        self.next_id = 1

    def get_by_email(self, email: str) -> Optional["User"]:
        for user in self.users.values():
            if user.email == email:
                return user
        return None

    def get_by_id(self, id: int) -> Optional["User"]:
        return self.users.get(id)

    def create(self, email: str, hashed_password: str) -> "User":
        from app.models import User
        user = User(id=self.next_id, email=email, hashed_password=hashed_password)
        self.users[self.next_id] = user
        self.next_id += 1
        return user

# We'll define the User model here to avoid circular imports, but it's better to have it in models.
# However, for simplicity, we'll import from models. But note: models may depend on repositories?
# Let's create a minimal User model in models.py that doesn't depend on repositories.