import uvicorn

from packages.domain.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("apps.api.main:app", host=settings.api_host, port=settings.api_port)
