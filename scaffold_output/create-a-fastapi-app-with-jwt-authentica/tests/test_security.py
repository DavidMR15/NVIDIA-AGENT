import os
import pytest
from datetime import datetime, timedelta, timezone
import jwt
from security import create_access_token, decode_token, SECRET_KEY, ALGORITHM

def test_create_access_token():
    data = {"sub": "testuser"}
    token = create_access_token(data)
    # decode without verification to check payload
    decoded = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert decoded["sub"] == "testuser"
    # exp should be present
    assert "exp" in decoded
    # exp should be approx now + 15 minutes (default)
    now = datetime.now(timezone.utc)
    exp = datetime.fromtimestamp(decoded["exp"], tz=timezone.utc)
    # Allow a small window due to execution time
    assert exp - now < timedelta(minutes=20)
    assert exp - now > timedelta(minutes=10)

def test_decode_token_valid():
    data = {"sub": "testuser"}
    token = create_access_token(data)
    payload = decode_token(token)
    assert payload["sub"] == "testuser"

def test_decode_token_invalid_signature():
    # create a token with a different secret
    other_secret = "othersecret"
    token = jwt.encode({"sub": "testuser", "exp": datetime.utcnow() + timedelta(minutes=15)}, other_secret, algorithm=ALGORITHM)
    with pytest.raises(Exception):
        decode_token(token)

def test_decode_token_expired():
    # create an expired token
    expired = jwt.encode({"sub": "testuser", "exp": datetime.utcnow() - timedelta(seconds=1)}, SECRET_KEY, algorithm=ALGORITHM)
    with pytest.raises(Exception):
        decode_token(expired)