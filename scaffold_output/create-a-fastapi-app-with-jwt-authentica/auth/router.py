from fastapi import APIRouter, Depends, HTTPException, status
from models.user import UserCreate, UserLogin, Token, UserResponse
from auth.service import AuthService
from dependencies import get_auth_service
from security import decode_token
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

router = APIRouter()

# Dependency to get current user from token
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
    service: AuthService = Depends(get_auth_service)
):
    try:
        payload = decode_token(credentials.credentials)
        username = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = await service.get_user_by_username(username)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user

@router.post("/register", response_model=UserResponse)
async def register(user: UserCreate, service: AuthService = Depends(get_auth_service)):
    try:
        db_user = await service.register(user)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return db_user

@router.post("/login", response_model=Token)
async def login(user: UserLogin, service: AuthService = Depends(get_auth_service)):
    try:
        access_token = await service.login(user)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return Token(access_token=access_token)

@router.get("/me", response_model=UserResponse)
async def me(current_user: UserResponse = Depends(get_current_user)):
    return current_user