import os
os.environ["SECRET_KEY"] = "test-secret-key-for-testing"
import pytest
from auth.service import AuthService
from auth.repository import InMemoryUserRepository
from models.user import UserCreate, UserLogin

@pytest.fixture
def repo():
    return InMemoryUserRepository()

@pytest.fixture
def service(repo):
    return AuthService(repo)

@pytest.mark.asyncio
async def test_register_success(service):
    user_data = UserCreate(username="alice", password="secret")
    user = await service.register(user_data)
    assert user.id == 1
    assert user.username == "alice"

@pytest.mark.asyncio
async def test_register_duplicate_username(service):
    user_data = UserCreate(username="bob", password="secret")
    await service.register(user_data)
    with pytest.raises(ValueError, match="Username already exists"):
        await service.register(UserCreate(username="bob", password="secret2"))

@pytest.mark.asyncio
async def test_login_success(service):
    await service.register(UserCreate(username="charlie", password="password"))
    token = await service.login(UserLogin(username="charlie", password="password"))
    assert isinstance(token, str)
    # token should be decodable
    from security import decode_token
    payload = decode_token(token)
    assert payload["sub"] == "charlie"

@pytest.mark.asyncio
async def test_login_invalid_credentials(service):
    await service.register(UserCreate(username="dave", password="pwd"))
    with pytest.raises(ValueError, match="Invalid credentials"):
        await service.login(UserLogin(username="dave", password="wrong"))
    with pytest.raises(ValueError, match="Invalid credentials"):
        await service.login(UserLogin(username="unknown", password="pwd"))

@pytest.mark.asyncio
async def test_get_user_by_username(service):
    await service.register(UserCreate(username="eve", password="pass"))
    user = await service.get_user_by_username("eve")
    assert user.username == "eve"
    assert user.id == 1
    assert await service.get_user_by_username("none") is None