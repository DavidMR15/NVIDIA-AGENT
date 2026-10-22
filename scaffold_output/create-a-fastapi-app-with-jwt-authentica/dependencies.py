from auth.repository import UserRepository
from auth.service import AuthService

def get_auth_service(repo: UserRepository = None) -> AuthService:
    if repo is None:
        repo = InMemoryUserRepository()
    return AuthService(repo)