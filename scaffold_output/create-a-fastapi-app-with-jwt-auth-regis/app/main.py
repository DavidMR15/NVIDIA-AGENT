from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.repositories import UserRepository
from app.dependencies import get_repository
from app.schemas import UserCreate, UserLogin, Token, UserResponse
from app.security import create_access_token, verify_password, get_password_hash

app = FastAPI()

# Security
bearer_scheme = HTTPBearer(auto_error=False)

@app.post("/register", response_model=UserResponse)
def register(user_in: UserCreate, repo: UserRepository = Depends(get_repository)):
    # Check if user already exists
    existing = repo.get_by_email(user_in.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )
    # Create user
    hashed_password = get_password_hash(user_in.password)
    user = repo.create(email=user_in.email, hashed_password=hashed_password)
    return user

@app.post("/login", response_model=Token)
def login(user_in: UserLogin, repo: UserRepository = Depends(get_repository)):
    user = repo.get_by_email(user_in.email)
    if not user or not verify_password(user_in.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": str(user.id)})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/me", response_model=UserResponse)
def me(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    repo: UserRepository = Depends(get_repository),
):
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    # Verify token
    from app.security import decode_access_token
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = int(payload.get("sub"))
    user = repo.get_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user