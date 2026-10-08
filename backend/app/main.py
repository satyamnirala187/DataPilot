"""DataPilot API entry point."""

from fastapi import FastAPI

from app.config import settings

app = FastAPI(title=settings.app_name)


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the API is running."""
    return {"status": "ok", "service": settings.app_name}
