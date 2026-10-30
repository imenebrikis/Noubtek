from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.routers import appointments, auth, members, queue

app = FastAPI(title="ClinicFlow")
app.include_router(auth.router)
app.include_router(members.router)
app.include_router(queue.router)
app.include_router(appointments.router)


@app.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    try:
        await session.execute(text("SELECT 1"))
        db = "ok"
    except Exception:
        db = "unreachable"
    return {"app": "ClinicFlow", "db": db}
