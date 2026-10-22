import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.repositories import UserRepository, InMemoryUserRepository
from app.dependencies import get_repository

# Create a shared in-memory repository for all tests
test_repo = InMemoryUserRepository()

# Override the dependency to use our shared repository
app.dependency_overrides[get_repository] = lambda: test_repo

client = TestClient(app)

def reset_repo():
    test_repo.users.clear()
    test_repo.next_id = 1

def test_register_success():
    reset_repo()
    response = client.post(
        "/register",
        json={"email": "test@example.com", "password": "secret"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@example.com"
    assert "id" in data

def test_register_duplicate_email():
    reset_repo()
    # First registration
    client.post(
        "/register",
        json={"email": "test2@example.com", "password": "secret"},
    )
    # Duplicate
    response = client.post(
        "/register",
        json={"email": "test2@example.com", "password": "secret2"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Email already registered"

def test_login_success():
    reset_repo()
    # Register a user
    client.post(
        "/register",
        json={"email": "test3@example.com", "password": "secret"},
    )
    # Login
    response = client.post(
        "/login",
        json={"email": "test3@example.com", "password": "secret"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

def test_login_invalid_credentials():
    reset_repo()
    # Register a user
    client.post(
        "/register",
        json={"email": "test4@example.com", "password": "secret"},
    )
    # Wrong password
    response = client.post(
        "/login",
        json={"email": "test4@example.com", "password": "wrong"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"
    # Non-existent email
    response = client.post(
        "/login",
        json={"email": "nonexistent@example.com", "password": "secret"},
    )
    assert response.status_code == 401

def test_me_endpoint():
    reset_repo()
    # Register and login to get token
    client.post(
        "/register",
        json={"email": "test5@example.com", "password": "secret"},
    )
    login_response = client.post(
        "/login",
        json={"email": "test5@example.com", "password": "secret"},
    )
    token = login_response.json()["access_token"]
    # Access protected endpoint
    response = client.get(
        "/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test5@example.com"
    # The ID should be 1 because we reset the repo before this test
    assert data["id"] == 1

def test_me_endpoint_missing_token():
    reset_repo()
    response = client.get("/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"

def test_me_endpoint_invalid_token():
    reset_repo()
    response = client.get(
        "/me",
        headers={"Authorization": "Bearer invalidtoken"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"