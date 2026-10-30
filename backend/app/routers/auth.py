import asyncio
import secrets
import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import ClinicRole
from app.services.auth_service import AuthService
from app.services.user_service import UserService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)  # bcrypt input limit
    full_name: str = Field(min_length=1)
    clinic_name: str = Field(min_length=1)


class SignupIn(BaseModel):
    """Minimal signup for the login form: email + password only. A fresh clinic is
    created for the user (they become its admin)."""
    email: EmailStr
    password: str = Field(min_length=6, max_length=72)
    full_name: str | None = None
    clinic_name: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class GoogleIn(BaseModel):
    token: str  # Google ID token (the GSI `credential` string)


class RefreshIn(BaseModel):
    refresh_token: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


def _tokens(user_id: uuid.UUID, clinic_id: uuid.UUID, role: ClinicRole) -> TokenOut:
    return TokenOut(
        access_token=AuthService.create_access_token(user_id, clinic_id, role),
        refresh_token=AuthService.create_refresh_token(user_id, clinic_id, role),
    )


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, session: AsyncSession = Depends(get_session)):
    try:
        user, clinic, member = await UserService(session).register(
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            clinic_name=body.clinic_name,
        )
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    return _tokens(user.id, clinic.id, member.role)


@router.post("/signup", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupIn, session: AsyncSession = Depends(get_session)):
    local = body.email.split("@")[0]
    try:
        user, clinic, member = await UserService(session).register(
            email=body.email,
            password=body.password,
            full_name=body.full_name or local,
            clinic_name=body.clinic_name or f"Clinique {local}",
        )
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    return _tokens(user.id, clinic.id, member.role)


@router.post("/google", response_model=TokenOut)
async def google_auth(body: GoogleIn, session: AsyncSession = Depends(get_session)):
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Google sign-in not configured")

    # verify the token signature + audience against our client id (blocking I/O -> thread).
    # ponytail: huge clock_skew tolerates this dev box's clock being far from Google's
    # real issue time (env runs in 2026) — drop to the default in production.
    try:
        idinfo = await asyncio.to_thread(
            google_id_token.verify_oauth2_token,
            body.token, google_requests.Request(), settings.GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=315_360_000,  # 10 years
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid Google token: {e}")

    email = idinfo.get("email")
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google token has no email")

    svc = UserService(session)
    existing = await svc.membership_by_email(email)
    if existing is not None:
        user, membership = existing
        return _tokens(user.id, membership.clinic_id, membership.role)

    # first time: create User + fresh Clinic (same logic as email signup)
    local = email.split("@")[0]
    user, clinic, member = await svc.register(
        email=email,
        password=secrets.token_urlsafe(32),  # random, unusable; they sign in via Google
        full_name=idinfo.get("name") or local,
        clinic_name=f"Clinique {local}",
    )
    return _tokens(user.id, clinic.id, member.role)


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, session: AsyncSession = Depends(get_session)):
    result = await UserService(session).authenticate(body.email, body.password)
    if result is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    user, membership = result
    return _tokens(user.id, membership.clinic_id, membership.role)


@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshIn, session: AsyncSession = Depends(get_session)):
    try:
        payload = AuthService.decode_token(body.refresh_token, expected_type="refresh")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    user_id = uuid.UUID(payload["sub"])
    clinic_id = uuid.UUID(payload["clinic_id"])
    # re-read membership from DB so role changes / removals take effect on refresh
    membership = await UserService(session).get_membership(user_id, clinic_id)
    if membership is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Membership no longer valid")
    return _tokens(user_id, clinic_id, membership.role)
