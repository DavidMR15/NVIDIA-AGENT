# Plan

Project structure:
- main.py: FastAPI app creation, includes routers.
- auth/router.py: APIRouter with /register, /login, /me endpoints.
- auth/service.py: AuthService handling registration, login, token generation, password hashing, user retrieval.
- auth/repository.py: Abstract UserRepository interface and InMemoryUserRepository implementation.
- models/user.py: Pydantic models for UserCreate, UserLogin, Token, UserResponse.
- security.py: JWT utilities (create_access_token, decode_token) using python-jose.
- dependencies.py: Dependency to get current user from token.
- requirements.txt: fastapi, uvicorn, python-jose[cryptography], passlib[bcrypt], pytest, pytest-asyncio.
Tests:
- tests/test_auth_service.py: Unit tests for AuthService using InMemoryUserRepository.
- tests/test_auth_router.py: Integration tests using TestClient.
Test strategy: Mock repository via dependency override; test normal flow, duplicate registration, invalid credentials, missing token, expired token (by manipulating expiry).
External services: None; repository is in-memory for tests; real adapter would be a DB but not needed.
