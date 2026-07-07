import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import ClinicRole
from app.services.auth_service import AuthService
from app.services.user_service import UserService

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)  # bcrypt input limit
    full_name: str = Field(min_length=1)
    clinic_name: str = Field(min_length=1)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


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
