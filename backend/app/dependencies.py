import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import Select, select

from app.models import ClinicRole
from app.services.auth_service import AuthService

oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")


@dataclass
class TenantContext:
    user_id: uuid.UUID
    clinic_id: uuid.UUID
    role: ClinicRole


def get_context(token: str = Depends(oauth2)) -> TenantContext:
    try:
        payload = AuthService.decode_token(token, expected_type="access")
    except jwt.PyJWTError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TenantContext(
        user_id=uuid.UUID(payload["sub"]),
        clinic_id=uuid.UUID(payload["clinic_id"]),
        role=ClinicRole(payload["role"]),
    )


def require_role(*roles: ClinicRole):
    def checker(ctx: TenantContext = Depends(get_context)) -> TenantContext:
        if ctx.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return ctx

    return checker


def scoped(ctx: TenantContext, model) -> Select:
    """ALL tenant-table reads go through this so clinic_id can't be forgotten."""
    return select(model).where(model.clinic_id == ctx.clinic_id)
