from __future__ import annotations

from dennis_bot.telegram.polling import TelegramPollingRunner


class FakeTelegramClient:
    def __init__(self, batches: list[list[dict]]) -> None:
        self.batches = batches
        self.calls: list[dict] = []

    async def get_updates(self, **kwargs):  # noqa: ANN001, ANN201
        self.calls.append(kwargs)
        if not self.batches:
            return []
        return self.batches.pop(0)


async def test_polling_routes_normalized_updates_and_advances_offset() -> None:
    handled = []
    client = FakeTelegramClient(
        [
            [
                {
                    "update_id": 10,
                    "message": {
                        "message_id": 100,
                        "text": "hey @DennisBot",
                        "chat": {"id": -1, "type": "group"},
                        "from": {"id": 111, "is_bot": False},
                    },
                },
                {"update_id": 11, "callback_query": {"id": "unsupported"}},
                {
                    "update_id": 12,
                    "message": {
                        "message_id": 101,
                        "text": "/status",
                        "chat": {"id": 222, "type": "private"},
                        "from": {"id": 111, "is_bot": False},
                    },
                },
            ],
            [],
        ]
    )

    async def handle(update):  # noqa: ANN001
        handled.append(update)

    runner = TelegramPollingRunner(
        client,
        handle,
        bot_username="DennisBot",
        offset=5,
        limit=3,
        timeout=1,
        allowed_updates=["message"],
    )

    assert await runner.poll_once() == 2
    assert runner.offset == 13
    assert [update.message_id for update in handled] == [100, 101]
    assert client.calls[0] == {
        "offset": 5,
        "limit": 3,
        "timeout": 1,
        "allowed_updates": ["message"],
    }

    assert await runner.poll_once() == 0
    assert client.calls[1]["offset"] == 13


async def test_polling_does_not_advance_past_failed_handler() -> None:
    client = FakeTelegramClient(
        [
            [
                {
                    "update_id": 20,
                    "message": {
                        "message_id": 200,
                        "text": "/status",
                        "chat": {"id": 222, "type": "private"},
                        "from": {"id": 111, "is_bot": False},
                    },
                },
            ]
        ]
    )

    async def handle(_update):  # noqa: ANN001
        raise RuntimeError("boom")

    runner = TelegramPollingRunner(client, handle, offset=1)

    try:
        await runner.poll_once()
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("handler failure should propagate")

    assert runner.offset == 1
