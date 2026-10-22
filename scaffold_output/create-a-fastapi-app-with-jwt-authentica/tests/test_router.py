import os
import pytest
from fastapi.testclient import TestClient
from main import app
from auth.repository import InMemoryUserRepository
from auth.service import AuthService
from security import SECRET_KEY, ALGORITHM, create_access_token
import jwt

# Set environment variable for SECRET_KEY if not already set
os.environ.setdefault("SECRET_KEY", "test-secret-key")

client = TestClient(app)

# Helper to create a fresh repository and service for each test
def get_test_repo():
    return InMemoryUserRepository()

def get_test_service(repo):
    return AuthService(repo)

# Override the dependency for the repository in the app
# We'll do this per test by using a fixture that overrides the dependency
@pytest.fixture(autouse=True)
def override_repo_dependency():
    # Clear any existing overrides
    app.dependency_overrides.clear()
    # Create a new repository for each test
    repo = InMemoryUserRepository()
    # Override the get_user_repository dependency to return our test repo
    app.dependency_overrides[get_user_repository] = lambda: repo
    yield
    # Clean up after test
    app.dependency_overrides.clear()

# We need to import get_user_repository from the router to override it
from auth.router import get_user_repository

def test_register_success():
    response = client.post(
        "/auth/register",
        json={"username": "alice", "password": "secret123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 1
    assert data["username"] == "alice"
    # The password should be hashed, not the plain password
    assert data["hashed_password"] != "secret123"
    # Also check that the hashed_password is a string
    assert isinstance(data["hashed_password"], str)
    assert len(data["hashed_password"]) > 0

def test_register_duplicate_username():
    # First registration
    client.post(
        "/auth/register",
        json={"username": "bob", "password": "secret"},
    )
    # Second registration with same username
    response = client.post(
        "/auth/register",
        json={"username": "bob", "password": "another"},
    )
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]

def test_login_success():
    # First, register a user
    client.post(
        "/auth/register",
        json={"username": "charlie", "password": "password123"},
    )
    # Then login
    response = client.post(
        "/auth/login",
        json={"username": "charlie", "password": "password123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    # Optionally, we can decode the token to check the sub
    token = data["access_token"]
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == "charlie"

def test_login_wrong_password():
    # Register a user
    client.post(
        "/auth/register",
        json={"username": "david", "password": "truepass"},
    )
    # Login with wrong password
    response = client.post(
        "/auth/login",
        json={"username": "david", "password": "wrongpass"},
    )
    assert response.status_code == 401
    assert "Incorrect username or password" in response.json()["detail"]

def test_login_non_existent_user():
    response = client.post(
        "/auth/login",
        json={"username": "unknown", "password": "any"},
    )
    assert response.status_code == 401
    assert "Incorrect username or password" in response.json()["detail"]

def test_me_with_valid_token():
    # Register a user to get a token
    reg_response = client.post(
        "/auth/register",
        json={"username": "eve", "password": "evepass"},
    )
    assert reg_response.status_code == 200
    # Login to get token
    login_response = client.post(
        "/auth/login",
        json={"username": "eve", "password": "evepass"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    # Now access /me with the token
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "eve"
    assert data["id"] == 1
    # The hashed_password should be present and not the plain password
    assert data["hashed_password"] != "evepass"

def test_me_with_invalid_token():
    response = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer invalidtoken"},
    )
    assert response.status_code == 401
    assert "Could not validate credentials" in response.json()["detail"]

def test_me_without_token():
    response = client.get("/auth/me")
    assert response.status_code == 401
    # The detail might be "Not authenticated" or similar, but our dependency raises 401 with "Could not validate credentials"
    # Actually, the get_current_user dependency will raise 401 because the token is missing.
    # Let's check the response.
    assert "Could not validate credentials" in response.json()["detail"]