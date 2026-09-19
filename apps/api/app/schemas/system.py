from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str
    database: str


class SystemInfo(BaseModel):
    environment: str
    version: str
    database: str
    authenticated_user: str | None
