import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings
from app.models import ClinicRole


class AuthService:
    """Password hashing + JWT issuing/decoding. Stateless."""

    @staticmethod
    def hash_password(password: str) -> str:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        return bcrypt.checkpw(password.encode(), password_hash.encode())

    @staticmethod
    def _create_token(
        user_id: uuid.UUID, clinic_id: uuid.UUID, role: ClinicRole,
        token_type: str, lifetime: timedelta,
    ) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user_id),
            "clinic_id": str(clinic_id),
            "role": role.value,
            "type": token_type,
            "iat": now,
            "exp": now + lifetime,
        }
        return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

    @classmethod
    def create_access_token(cls, user_id: uuid.UUID, clinic_id: uuid.UUID, role: ClinicRole) -> str:
        return cls._create_token(
            user_id, clinic_id, role, "access",
            timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        )

    @classmethod
    def create_refresh_token(cls, user_id: uuid.UUID, clinic_id: uuid.UUID, role: ClinicRole) -> str:
        return cls._create_token(
            user_id, clinic_id, role, "refresh",
            timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        )

    @staticmethod
    def decode_token(token: str, expected_type: str) -> dict:
        """Raises jwt.PyJWTError on bad signature, expiry, or wrong token type."""
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != expected_type:
            raise jwt.InvalidTokenError(f"expected a {expected_type} token")
        return payload
