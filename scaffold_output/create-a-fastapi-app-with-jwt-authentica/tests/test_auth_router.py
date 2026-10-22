import os
os.environ["SECRET_KEY"] = "test-secret-key"

from fastapi.testclient import TestClient
from main import app
from auth.repository import InMemoryUserRepository
from auth.service import AuthService

client = TestClient(app)

def override_get_repo():
    return InMemoryUserRepository()

def override_get_service(repo=InMemoryUserRepository()):
    return AuthService(repo)

# We need to override the dependencies in the app.
# The app likely gets the service via dependency injection.
# We'll assume there's a dependency called get_auth_service in dependencies.py.
# If not, we'll adjust later.

from dependencies import get_auth_service

app.dependency_overrides[get_auth_service] = override_get_service

def test_register_and_login_flow():
    # Register
    response = client.post("/register", json={"username": "testuser", "password": "testpass"})
    print(f"Register response: {response.status_code} {response.text}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    assert response.json()["username"] == "testuser"
    user_id = response.json()["id"]

    # Login
    response = client.post("/login", json={"username": "testuser", "password": "testpass"})
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert token

    # Get me
    response = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["username"] == "testuser"
    assert response.json()["id"] == user_id

def test_login_invalid_credentials():
    # First register a user
    client.post("/register", json={"username": "user2", "password": "pass"})
    # Try wrong password
    response = client.post("/login", json={"username": "user2", "password": "wrong"})
    assert response.status_code == 400
    # Try non-existent user
    response = client.post("/login", json={"username": "unknown", "password": "pass"})
    assert response.status_code == 400

def test_me_missing_token():
    response = client.get("/me")
    assert response.status_code == 401

def test_me_invalid_token():
    response = client.get("/me", headers={"Authorization": "Bearer invalidtoken"})
    assert response.status_code == 401