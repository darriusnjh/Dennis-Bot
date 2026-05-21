from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dennis_bot.config import Settings
from dennis_bot.telegram.webhook import build_telegram_webhook_router


class FakeCommandRouter:
    def __init__(self, *, handled: bool = True, fail: bool = False) -> None:
        self.handled = handled
        self.fail = fail
        self.calls = 0

    async def handle_update(self, update):  # noqa: ANN001
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return self.handled


class FakeMessageHandler:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def handle_update(self, update):  # noqa: ANN001
        self.calls += 1
        if self.fail:
            raise RuntimeError("handler failed")
        return True


class FakeRecorder:
    def __init__(self, *, claim: bool = True) -> None:
        self.claim = claim
        self.recorded = 0
        self.claimed = 0

    async def record_metadata(self, update):  # noqa: ANN001
        self.recorded += 1

    async def claim_update(self, update):  # noqa: ANN001
        self.claimed += 1
        return self.claim


def test_telegram_webhook_acknowledges_before_background_handling() -> None:
    command_router = FakeCommandRouter()
    app = _build_app(command_router)

    response = TestClient(app).post(
        "/webhooks/telegram",
        json=_message_update("/status"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "accepted": True}
    assert command_router.calls == 1


def test_telegram_webhook_background_errors_do_not_return_500() -> None:
    command_router = FakeCommandRouter(handled=False)
    app = _build_app(command_router)
    app.state.message_handler = FakeMessageHandler(fail=True)

    response = TestClient(app).post(
        "/webhooks/telegram",
        json=_message_update("hey DennisBot"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "accepted": True}
    assert command_router.calls == 1
    assert app.state.message_handler.calls == 1


def test_telegram_webhook_skips_duplicate_claimed_updates() -> None:
    command_router = FakeCommandRouter()
    app = _build_app(command_router)
    app.state.telegram_recorder = FakeRecorder(claim=False)

    response = TestClient(app).post(
        "/webhooks/telegram",
        json=_message_update("/status"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
    )

    assert response.status_code == 200
    assert command_router.calls == 0
    assert app.state.telegram_recorder.recorded == 1
    assert app.state.telegram_recorder.claimed == 1


def _build_app(command_router: FakeCommandRouter) -> FastAPI:
    settings = Settings(telegram_webhook_secret="secret")
    app = FastAPI()
    app.include_router(build_telegram_webhook_router(settings, command_router))
    return app


def _message_update(text: str) -> dict:
    return {
        "update_id": 123,
        "message": {
            "message_id": 456,
            "text": text,
            "chat": {"id": 789, "type": "private"},
            "from": {"id": 789, "is_bot": False, "first_name": "User"},
        },
    }
