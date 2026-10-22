from auth.repository import UserRepository
from models.user import UserCreate, UserLogin, UserResponse
from security import create_access_token, pwd_context

class AuthService:
    def __init__(self, repository: UserRepository):
        self.repository = repository

    async def register(self, user_data: UserCreate) -> UserResponse:
        hashed_password = pwd_context.hash(user_data.password)
        user = await self.repository.create(user_data.username, hashed_password)
        return user

    async def login(self, user_data: UserLogin) -> str:
        hashed_password = await self.repository.get_password_hash(user_data.username)
        if not hashed_password or not pwd_context.verify(user_data.password, hashed_password):
            raise ValueError("Invalid credentials")
        access_token = create_access_token(data={"sub": user_data.username})
        return access_token

    async def get_user_by_username(self, username: str):
        return await self.repository.get_by_username(username)