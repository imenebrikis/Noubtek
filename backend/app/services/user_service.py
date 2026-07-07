import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Clinic, ClinicMember, ClinicRole, User
from app.services.auth_service import AuthService


class UserService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def register(
        self, *, email: str, password: str, full_name: str, clinic_name: str,
    ) -> tuple[User, Clinic, ClinicMember]:
        """Create User + Clinic + admin ClinicMember in ONE transaction."""
        user = User(
            email=email,
            password_hash=AuthService.hash_password(password),
            full_name=full_name,
        )
        clinic = Clinic(name=clinic_name)
        self.session.add_all([user, clinic])
        await self.session.flush()  # get ids; still uncommitted
        member = ClinicMember(clinic_id=clinic.id, user_id=user.id, role=ClinicRole.admin)
        self.session.add(member)
        await self.session.commit()
        return user, clinic, member

    async def authenticate(self, email: str, password: str) -> tuple[User, ClinicMember] | None:
        user = (
            await self.session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is None or not AuthService.verify_password(password, user.password_hash):
            return None
        # ponytail: first membership wins; add a clinic switcher when multi-clinic users exist
        membership = (
            await self.session.execute(
                select(ClinicMember)
                .where(ClinicMember.user_id == user.id)
                .order_by(ClinicMember.created_at)
            )
        ).scalars().first()
        if membership is None:
            return None
        return user, membership

    async def get_membership(self, user_id: uuid.UUID, clinic_id: uuid.UUID) -> ClinicMember | None:
        return (
            await self.session.execute(
                select(ClinicMember).where(
                    ClinicMember.user_id == user_id,
                    ClinicMember.clinic_id == clinic_id,
                )
            )
        ).scalar_one_or_none()
