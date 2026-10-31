import os

# must be set before any app import — Settings() validates at import time
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("JWT_SECRET", "test-secret-0123456789abcdef0123456789abcdef")

import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import BigInteger
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.db import get_session
from app.main import app
from app.models import Base


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    # models are Postgres-first; sqlite is test-only
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(type_, compiler, **kw):
    # SQLite only autoincrements INTEGER PRIMARY KEY (appointment_events.id is BIGINT in PG)
    return "INTEGER"


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def client(session):
    async def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def fake_gemini(monkeypatch):
    """Route Gemini calls to a stub. `fake_gemini("text")` -> list of prompts sent (for PII checks)."""
    from app.services import ai_delay, patient_alerts

    def install(message: str) -> list[str]:
        prompts: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            prompts.append(json.loads(req.content)["contents"][0]["parts"][0]["text"])
            body = {"candidates": [{"content": {"parts": [{"text": json.dumps({"message": message})}]}}]}
            return httpx.Response(200, json=body)

        real = httpx.AsyncClient
        monkeypatch.setattr(ai_delay.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
        monkeypatch.setattr(ai_delay.settings, "GEMINI_API_KEY", "test-key")
        ai_delay._cache.clear()
        patient_alerts._templates.clear()
        return prompts

    return install
