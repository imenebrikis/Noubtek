import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import TenantContext, require_role, scoped
from app.models import ClinicMember, ClinicRole

router = APIRouter(prefix="/members", tags=["members"])


class MemberRolePatch(BaseModel):
    role: ClinicRole


@router.patch("/{member_id}")
async def update_member_role(
    member_id: uuid.UUID,
    body: MemberRolePatch,
    ctx: TenantContext = Depends(require_role(ClinicRole.admin)),
    session: AsyncSession = Depends(get_session),
):
    member = (
        await session.execute(scoped(ctx, ClinicMember).where(ClinicMember.id == member_id))
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    if member.user_id == ctx.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot change your own role")
    member.role = body.role
    await session.commit()
    return {"id": str(member.id), "role": member.role}
