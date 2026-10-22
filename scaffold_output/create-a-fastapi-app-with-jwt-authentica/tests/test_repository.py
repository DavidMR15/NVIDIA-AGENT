import pytest
from auth.repository import UserRepository, InMemoryUserRepository
from models import UserCreate, UserResponse

@pytest.fixture
def repo():
    return InMemoryUserRepository()

def test_create_user(repo):
    user_in = UserCreate(username="alice", password="secret")
    user = repo.create(user_in)
    assert user.id == 1
    assert user.username == "alice"
    # password should be hashed
    assert user.hashed_password != "secret"

def test_get_user_by_username(repo):
    user_in = UserCreate(username="bob", password="secret")
    repo.create(user_in)
    user = repo.get_by_username("bob")
    assert user is not None
    assert user.username == "bob"
    assert user.id == 1

def test_get_user_by_username_not_found(repo):
    assert repo.get_by_username("unknown") is None

def test_get_user_by_id(repo):
    user_in = UserCreate(username="charlie", password="secret")
    created = repo.create(user_in)
    user = repo.get_by_id(created.id)
    assert user is not None
    assert user.username == "charlie"

def test_get_user_by_id_not_found(repo):
    assert repo.get_by_id(999) is None