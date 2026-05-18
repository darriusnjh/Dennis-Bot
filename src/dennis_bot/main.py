from __future__ import annotations

import os

import uvicorn

from dennis_bot.app import create_app
from dennis_bot.config import get_settings


def main() -> None:
    settings = get_settings()
    reload_enabled = settings.app_env == "development" and not settings.telegram_use_polling
    uvicorn.run(
        "dennis_bot.app:create_app",
        factory=True,
        host=settings.app_host,
        port=int(os.getenv("PORT") or settings.app_port),
        reload=reload_enabled,
    )


if __name__ == "__main__":
    main()
