"""Authentication routes: register / login / logout / me (session-cookie based)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator

from app.security.passwords import hash_password, needs_rehash, verify_password
from app.security.sessions import create_session, revoke_session
from app.security.user_auth import (
    clear_session_cookie,
    get_session_token,
    require_user,
    set_session_cookie,
)
from db.database_manager import get_project_db_connection_string, get_session
from db.model import User

router = APIRouter(prefix="/auth", tags=["auth"])

# Verified against on every failed-lookup login to keep timing uniform (anti-enumeration).
_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=1024)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        local, _, domain = value.partition("@")
        if not local or "." not in domain:
            raise ValueError("Invalid email address.")
        return value


class UserOut(BaseModel):
    id: int
    email: str
    is_admin: bool


def _user_out(user: User) -> UserOut:
    return UserOut(id=user.id, email=user.email, is_admin=user.is_admin)


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: Credentials, response: Response) -> UserOut:
    db = get_session(get_project_db_connection_string())
    try:
        if db.query(User).filter(User.email == payload.email).first() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Email already registered."
            )
        user = User(email=payload.email, password_hash=hash_password(payload.password))
        db.add(user)
        db.commit()
        db.refresh(user)
        token = create_session(user.id, db=db)
        db.commit()
        set_session_cookie(response, token)
        return _user_out(user)
    finally:
        db.close()


@router.post("/login", response_model=UserOut)
def login(payload: Credentials, response: Response) -> UserOut:
    db = get_session(get_project_db_connection_string())
    try:
        user = db.query(User).filter(User.email == payload.email).first()
        if user is None:
            verify_password(_DUMMY_HASH, payload.password)  # equalize timing
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials."
            )
        if not verify_password(user.password_hash, payload.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials."
            )
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled.")
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(payload.password)
            db.commit()
        token = create_session(user.id, db=db)
        db.commit()
        set_session_cookie(response, token)
        return _user_out(user)
    finally:
        db.close()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> None:
    token = get_session_token(request)
    if token:
        revoke_session(token)
    clear_session_cookie(response)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(require_user)) -> UserOut:
    return _user_out(user)
