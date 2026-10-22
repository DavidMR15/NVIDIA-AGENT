# Plan

Project structure:
- app/
  - __init__.py
  - main.py: FastAPI app creation, includes routers.
  - models.py: SQLAlchemy User model (though we won't use DB in tests).
  - schemas.py: Pydantic models for request/response.
  - security.py: JWT token creation, verification, password hashing.
  - repositories.py: Abstract UserRepository interface and InMemoryUserRepository implementation.
  - dependencies.py: Dependency providers for repository and security settings.
- tests/
  - test_auth.py: Tests for register, login, /me endpoint using TestClient and in-memory repo.
- requirements.txt: Lists required packages.
- README.md: Project description, setup, run instructions, not verified section.

External services: None; we use in-memory repository for tests. Real repository would be a PostgreSQL adapter but not verified.

Test strategy: Use pytest with TestClient. Override repository dependency to use InMemoryUserRepository. Test registration (success, duplicate email), login (correct/incorrect credentials), token verification, protected endpoint (valid/invalid/missing token). Ensure password hashing.

Now proceed.
