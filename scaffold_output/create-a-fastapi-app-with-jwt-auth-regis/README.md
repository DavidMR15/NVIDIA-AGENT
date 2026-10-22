# FastAPI JWT Auth Example

This project implements a simple authentication system using FastAPI with JWT tokens. It includes user registration, login, and a protected endpoint to retrieve the current user's information.

## Folder Structure

```
.
├── app
│   ├── __init__.py
│   ├── main.py          # FastAPI application and route definitions
│   ├── models.py        # User model (plain Python class)
│   ├── repositories.py  # Repository interface and in-memory implementation
│   ├── schemas.py       # Pydantic models for request/response validation
│   ├── security.py      # Password hashing and JWT token handling
│   └── dependencies.py  # Dependency providers
├── tests
│   └── test_auth.py     # Test suite for authentication endpoints
├── requirements.txt     # Python dependencies
└── README.md
```

## Installation

1. Clone the repository (or copy the files).
2. Install the dependencies:

```bash
pip install -r requirements.txt
```

## Running the Application

To start the development server:

```bash
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`.

## Running the Tests

To run the test suite:

```bash
pytest
```

## Configuration

The application uses environment variables for configuration:

- `SECRET_KEY`: Used to sign JWT tokens. **Must be set in production.** Defaults to a test key for development.
- Other settings (like token expiration) are hardcoded for simplicity but can be adjusted in `app/security.py`.

**Important:** The application will refuse to start with a missing or default secret outside of tests. In a production environment, you must set `SECRET_KEY` to a strong, random value.

## Not Verified

The following aspects are not covered by the automated tests:

1. **Persistent Storage**: The tests use an in-memory repository (`InMemoryUserRepository`). The real application could use a database (e.g., PostgreSQL) via a different repository implementation. The adapter for an external database is not tested.
2. **External Services**: No external services (like email providers, OAuth providers, etc.) are integrated or tested.
3. **Performance and Load Testing**: The tests do not cover performance under load or stress conditions.
4. **Security Auditing**: While we follow best practices (hashed passwords, JWT verification, etc.), we have not conducted a formal security audit or penetration testing.
5. **Dockerization**: The project does not include a Dockerfile or docker-compose configuration.
6. **API Documentation**: The automatic Swagger/OpenAPI docs provided by FastAPI are not verified for correctness beyond the test coverage.

## Implementation Details

- **Password Hashing**: Uses `passlib` with the `pbkdf2_sha256` scheme (bcrypt had compatibility issues in the test environment, so we switched to pbkdf2_sha256 which is also secure).
- **JWT Tokens**: Uses `PyJWT` with HS256 algorithm. Tokens expire after 30 minutes (configurable).
- **Repository Pattern**: The `UserRepository` abstracts data access. An in-memory implementation is used for tests and as the default. A real application would provide a database-backed implementation.
- **Dependency Injection**: The repository is provided via FastAPI's dependency injection system, allowing easy swapping for testing.

## License

MIT