from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from dennis_bot.app import _handle_normalized_update
from dennis_bot.app import _start_telegram_ingress
from dennis_bot.admin.policy import AdminPolicy
from dennis_bot.config import Settings
from dennis_bot.runtime.adapters import NaturalMessageHandler
from dennis_bot.stickers.service import StickerService
from dennis_bot.stickers.store import InMemoryStickerAliasStore, StickerAlias
from dennis_bot.telegram.client import TelegramApiError, TelegramClient
from dennis_bot.telegram.normalize import normalize_update
from dennis_bot.telegram.router import CommandRouter


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.stickers: list[dict] = []

    async def send_message(self, chat_id, text, **kwargs):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text, **kwargs})
        return {"message_id": len(self.messages)}

    async def send_sticker(self, chat_id, sticker, **kwargs):  # noqa: ANN001
        self.stickers.append({"chat_id": chat_id, "sticker": sticker, **kwargs})
        return {"message_id": len(self.stickers)}

    async def get_sticker_set(self, name: str) -> dict:
        return {
            "name": name,
            "title": "Test Pack",
            "stickers": [
                {"file_id": "file-thinking", "emoji": "thinking", "keywords": ["thinking"]},
                {"file_id": "file-approved", "emoji": "approved", "keywords": ["approved"]},
            ],
        }


class FakeRecorder:
    def __init__(self) -> None:
        self.seen: set[int] = set()
        self.recorded = 0

    async def record_metadata(self, update) -> None:  # noqa: ANN001
        del update
        self.recorded += 1

    async def claim_update(self, update) -> bool:  # noqa: ANN001
        if update.update_id in self.seen:
            return False
        self.seen.add(update.update_id)
        return True


class FakeCommandRouter:
    def __init__(self) -> None:
        self.calls = 0

    async def handle_update(self, update) -> bool:  # noqa: ANN001
        del update
        self.calls += 1
        return True


class FakeOrchestrator:
    def __init__(self, text: str = "Can, steady. This one is done.") -> None:
        self.text = text
        self.incoming = None

    async def respond(self, incoming):  # noqa: ANN001
        self.incoming = incoming
        return SimpleNamespace(text=self.text, model="fake")


def _command_update(
    text: str,
    *,
    user_id: int = 111,
    chat_id: int = 222,
    chat_type: str = "private",
):
    return normalize_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "text": text,
                "chat": {"id": chat_id, "type": chat_type},
                "from": {"id": user_id, "is_bot": False, "first_name": "User"},
            },
        },
        bot_username="DennisBot",
    )


def test_normalizes_commands_mentions_and_replies() -> None:
    command = _command_update("/status@DennisBot", chat_type="group")
    assert command is not None
    assert command.command is not None
    assert command.command.name == "status"
    assert command.addressed_to_bot

    command_with_at_username_config = normalize_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "text": "/status@DennisBot",
                "chat": {"id": -1, "type": "group"},
                "from": {"id": 111, "is_bot": False},
            },
        },
        bot_username="@DennisBot",
    )
    assert command_with_at_username_config is not None
    assert command_with_at_username_config.command is not None
    assert command_with_at_username_config.addressed_to_bot

    other_bot_command = normalize_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "text": "/status@OtherBot",
                "chat": {"id": -1, "type": "group"},
                "from": {"id": 111, "is_bot": False},
            },
        },
        bot_username="DennisBot",
    )
    assert other_bot_command is not None
    assert other_bot_command.command is None
    assert not other_bot_command.addressed_to_bot

    mention = normalize_update(
        {
            "update_id": 2,
            "message": {
                "message_id": 11,
                "text": "hey @DennisBot",
                "chat": {"id": -1, "type": "supergroup"},
                "from": {"id": 111, "is_bot": False},
            },
        },
        bot_username="DennisBot",
    )
    assert mention is not None
    assert mention.mentioned_bot
    assert mention.addressed_to_bot

    text_mention = normalize_update(
        {
            "update_id": 4,
            "message": {
                "message_id": 13,
                "text": "Dennis Bot can you reply?",
                "entities": [
                    {
                        "offset": 0,
                        "length": 10,
                        "type": "text_mention",
                        "user": {"id": 123, "is_bot": True, "username": "DennisBot"},
                    }
                ],
                "chat": {"id": -1, "type": "supergroup"},
                "from": {"id": 111, "is_bot": False},
            },
        },
        bot_user_id=123,
    )
    assert text_mention is not None
    assert text_mention.mentioned_bot
    assert text_mention.addressed_to_bot

    other_text_mention = normalize_update(
        {
            "update_id": 5,
            "message": {
                "message_id": 14,
                "text": "Other Bot can you reply?",
                "entities": [
                    {
                        "offset": 0,
                        "length": 9,
                        "type": "text_mention",
                        "user": {"id": 999, "is_bot": True, "username": "OtherBot"},
                    }
                ],
                "chat": {"id": -1, "type": "supergroup"},
                "from": {"id": 111, "is_bot": False},
            },
        },
        bot_user_id=123,
    )
    assert other_text_mention is not None
    assert not other_text_mention.mentioned_bot
    assert not other_text_mention.addressed_to_bot

    reply = normalize_update(
        {
            "update_id": 3,
            "message": {
                "message_id": 12,
                "text": "following up",
                "chat": {"id": -1, "type": "group"},
                "from": {"id": 111, "is_bot": False},
                "reply_to_message": {"from": {"id": 999, "is_bot": True}},
            },
        }
    )
    assert reply is not None
    assert reply.reply_to_bot
    assert reply.addressed_to_bot


def test_normalize_ignores_reply_to_other_bot_when_identity_is_known() -> None:
    update = normalize_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "text": "not for Dennis",
                "chat": {"id": -1, "type": "group"},
                "from": {"id": 111, "is_bot": False},
                "reply_to_message": {
                    "from": {"id": 999, "is_bot": True, "username": "OtherBot"}
                },
            },
        },
        bot_user_id=123,
    )
    assert update is not None
    assert not update.reply_to_bot
    assert not update.addressed_to_bot


def test_normalize_ignores_reply_to_other_bot_when_username_is_known() -> None:
    update = normalize_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "text": "not for Dennis",
                "chat": {"id": -1, "type": "group"},
                "from": {"id": 111, "is_bot": False},
                "reply_to_message": {
                    "from": {"id": 999, "is_bot": True, "username": "OtherBot"}
                },
            },
        },
        bot_username="DennisBot",
    )
    assert update is not None
    assert not update.reply_to_bot
    assert not update.addressed_to_bot


def test_normalize_detects_reply_to_dennis_when_identity_is_known() -> None:
    update = normalize_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "text": "for Dennis",
                "chat": {"id": -1, "type": "group"},
                "from": {"id": 111, "is_bot": False},
                "reply_to_message": {
                    "from": {"id": 123, "is_bot": True, "username": "DennisBot"}
                },
            },
        },
        bot_user_id=123,
    )
    assert update is not None
    assert update.reply_to_bot
    assert update.addressed_to_bot


@pytest.mark.asyncio
async def test_handle_normalized_update_skips_duplicate_update_ids() -> None:
    update = _command_update("/status")
    assert update is not None
    recorder = FakeRecorder()
    router = FakeCommandRouter()
    app = SimpleNamespace(
        state=SimpleNamespace(
            telegram_recorder=recorder,
            command_router=router,
        )
    )

    first = await _handle_normalized_update(app, update)
    second = await _handle_normalized_update(app, update)

    assert first is True
    assert second is True
    assert recorder.recorded == 2
    assert router.calls == 1


@pytest.mark.asyncio
async def test_telegram_client_posts_bot_api_methods() -> None:
    calls: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "body": json.loads(request.content)})
        if str(request.url).endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [{"done": True}]})
        return httpx.Response(200, json={"ok": True, "result": {"done": True}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TelegramClient("123:abc", client=http, base_url="https://telegram.test")

    await client.send_message(123, "hello")
    await client.send_sticker(123, "file-id")
    await client.get_sticker_set("pack")
    await client.get_me()
    updates = await client.get_updates(offset=10, limit=5, timeout=1)
    await client.set_webhook("https://example.test/hook", secret_token="secret")
    await client.delete_webhook(drop_pending_updates=True)

    assert [call["url"].rsplit("/", 1)[-1] for call in calls] == [
        "sendMessage",
        "sendSticker",
        "getStickerSet",
        "getMe",
        "getUpdates",
        "setWebhook",
        "deleteWebhook",
    ]
    assert calls[0]["body"] == {"chat_id": 123, "text": "hello"}
    assert calls[4]["body"] == {"offset": 10, "limit": 5, "timeout": 1}
    assert updates == [{"done": True}]
    assert calls[5]["body"]["secret_token"] == "secret"


@pytest.mark.asyncio
async def test_telegram_client_raises_on_api_error() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error_code": 400, "description": "bad"})

    client = TelegramClient(
        "123:abc",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url="https://telegram.test",
    )
    with pytest.raises(TelegramApiError) as exc_info:
        await client.send_message(123, "hello")

    assert exc_info.value.description == "bad"
    assert exc_info.value.error_code == 400


@pytest.mark.asyncio
async def test_telegram_client_reads_api_error_from_http_error_body() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: secret token contains unallowed characters",
            },
        )

    client = TelegramClient(
        "123:abc",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url="https://telegram.test",
    )
    with pytest.raises(TelegramApiError) as exc_info:
        await client.set_webhook("https://example.test/hook", secret_token="bad secret!")

    assert "secret token contains unallowed characters" in exc_info.value.description


@pytest.mark.asyncio
async def test_telegram_webhook_registration_failure_does_not_crash_startup() -> None:
    class FailingTelegram:
        async def set_webhook(self, *args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            raise TelegramApiError("setWebhook", "bad webhook")

    settings = Settings(
        app_env="production",
        base_url="https://dennis-bot.example.com",
        telegram_bot_token="123456789:abcdefghijklmnopqrstuvwxyzABCDE",
        admin_telegram_user_ids="123",
        openai_api_key="sk-test-value",
        simplemem_mcp_url="https://mcp.simplemem.cloud/mcp",
        simplemem_mcp_token="simplemem-token-value",
        telegram_webhook_secret="webhook-secret-value",
    )
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, telegram_client=FailingTelegram()))

    await _start_telegram_ingress(app)

    assert app.state.telegram_webhook_registration_error == "Telegram setWebhook failed: bad webhook"


@pytest.mark.asyncio
async def test_router_blocks_admin_commands_for_non_admin() -> None:
    fake = FakeTelegram()
    router = CommandRouter(
        telegram=fake,
        policy=AdminPolicy(admin_user_ids=frozenset({111})),
        settings=Settings(admin_telegram_user_ids=[111]),
    )

    update = _command_update("/settings", user_id=222)
    assert update is not None
    handled = await router.handle_update(update)

    assert handled
    assert fake.messages[-1]["text"] == "This command is restricted to bot admins."


@pytest.mark.asyncio
async def test_router_calls_injected_memory_service() -> None:
    class Memory:
        async def search(self, query: str) -> str:
            return f"found {query}"

        async def stats(self) -> str:
            return "stats"

        async def finalize(self, chat_id: int) -> str:
            return f"finalized {chat_id}"

    fake = FakeTelegram()
    router = CommandRouter(
        telegram=fake,
        policy=AdminPolicy(admin_user_ids=frozenset({111})),
        settings=Settings(admin_telegram_user_ids=[111]),
        memory_service=Memory(),
    )

    update = _command_update("/memory search dennis")
    assert update is not None
    await router.handle_update(update)

    assert fake.messages[-1]["text"] == "found dennis"


@pytest.mark.asyncio
async def test_policy_trusted_group_full_memory_access() -> None:
    policy = AdminPolicy(admin_user_ids=frozenset({111}), trusted_group_chat_id=-100)
    trusted = _command_update("/status", chat_id=-100, chat_type="supergroup", user_id=222)
    untrusted = _command_update("/status", chat_id=-200, chat_type="supergroup", user_id=222)
    assert trusted is not None
    assert untrusted is not None

    assert policy.has_full_memory_access(trusted)
    assert not policy.has_full_memory_access(untrusted)


@pytest.mark.asyncio
async def test_router_sends_sticker_alias() -> None:
    fake = FakeTelegram()
    store = InMemoryStickerAliasStore()
    await store.upsert_alias(
        StickerAlias(alias="thinking", file_id="file-thinking", pack_name="pack")
    )
    router = CommandRouter(
        telegram=fake,
        policy=AdminPolicy(admin_user_ids=frozenset({111})),
        settings=Settings(admin_telegram_user_ids=[111]),
        sticker_service=StickerService(fake, store),
    )

    update = _command_update("/stickers test thinking")
    assert update is not None
    await router.handle_update(update)

    assert fake.stickers == [
        {"chat_id": 222, "sticker": "file-thinking", "reply_to_message_id": 10}
    ]


@pytest.mark.asyncio
async def test_router_saves_sticker_alias_from_replied_sample() -> None:
    fake = FakeTelegram()
    store = InMemoryStickerAliasStore()
    sticker_service = StickerService(fake, store)
    router = CommandRouter(
        telegram=fake,
        policy=AdminPolicy(admin_user_ids=frozenset({111})),
        settings=Settings(admin_telegram_user_ids=[111]),
        sticker_service=sticker_service,
    )
    update = normalize_update(
        {
            "update_id": 4,
            "message": {
                "message_id": 12,
                "text": "/stickers save approved",
                "chat": {"id": 222, "type": "private"},
                "from": {"id": 111, "is_bot": False, "first_name": "User"},
                "reply_to_message": {
                    "message_id": 11,
                    "sticker": {"file_id": "file-sample", "emoji": "👍"},
                },
            },
        },
        bot_username="DennisBot",
    )
    assert update is not None

    await router.handle_update(update)
    sent = await sticker_service.send_alias(222, "approved", reply_to_message_id=12)

    assert fake.messages[-1]["text"] == "Saved sticker alias: approved"
    assert sent is True
    assert fake.stickers == [
        {"chat_id": 222, "sticker": "file-sample", "reply_to_message_id": 12}
    ]


@pytest.mark.asyncio
async def test_natural_message_handler_passes_available_sticker_moods_to_model() -> None:
    fake = FakeTelegram()
    store = InMemoryStickerAliasStore()
    await store.upsert_alias(
        StickerAlias(alias="approved", file_id="file-approved", pack_name="pack")
    )
    orchestrator = FakeOrchestrator()
    handler = NaturalMessageHandler(
        telegram=fake,
        orchestrator=orchestrator,
        sticker_service=StickerService(fake, store),
    )

    update = _command_update("I finished the task")
    assert update is not None
    handled = await handler.handle_update(update)

    assert handled is True
    assert fake.messages[-1]["text"] == "Can, steady. This one is done."
    assert fake.stickers == []
    assert orchestrator.incoming.metadata["available_sticker_moods"] == ["approved"]


@pytest.mark.asyncio
async def test_natural_message_handler_supports_sticker_only_response() -> None:
    fake = FakeTelegram()
    store = InMemoryStickerAliasStore()
    await store.upsert_alias(
        StickerAlias(alias="celebrate", file_id="file-celebrate", pack_name="pack")
    )
    handler = NaturalMessageHandler(
        telegram=fake,
        orchestrator=FakeOrchestrator("[sticker: celebrate]"),
        sticker_service=StickerService(fake, store),
    )

    update = _command_update("Done, I submitted it")
    assert update is not None
    handled = await handler.handle_update(update)

    assert handled is True
    assert fake.messages == []
    assert fake.stickers == [
        {"chat_id": 222, "sticker": "file-celebrate", "reply_to_message_id": 10}
    ]


@pytest.mark.asyncio
async def test_natural_message_handler_supports_text_plus_explicit_sticker() -> None:
    fake = FakeTelegram()
    store = InMemoryStickerAliasStore()
    await store.upsert_alias(
        StickerAlias(alias="encourage", file_id="file-encourage", pack_name="pack")
    )
    handler = NaturalMessageHandler(
        telegram=fake,
        orchestrator=FakeOrchestrator("Send me the messy version first.\n[sticker: encourage]"),
        sticker_service=StickerService(fake, store),
    )

    update = _command_update("I feel stuck")
    assert update is not None
    handled = await handler.handle_update(update)

    assert handled is True
    assert fake.messages[-1]["text"] == "Send me the messy version first."
    assert fake.stickers == [
        {"chat_id": 222, "sticker": "file-encourage", "reply_to_message_id": 10}
    ]


@pytest.mark.asyncio
async def test_natural_message_handler_does_not_auto_attach_casual_sticker() -> None:
    fake = FakeTelegram()
    store = InMemoryStickerAliasStore()
    await store.upsert_alias(
        StickerAlias(alias="custom_mood", file_id="file-custom", pack_name="pack")
    )
    handler = NaturalMessageHandler(
        telegram=fake,
        orchestrator=FakeOrchestrator("Ya, sounds good."),
        sticker_service=StickerService(fake, store),
    )

    update = _command_update("hello dennis")
    assert update is not None
    handled = await handler.handle_update(update)

    assert handled is True
    assert fake.messages[-1]["text"] == "Ya, sounds good."
    assert fake.stickers == []


@pytest.mark.asyncio
async def test_natural_message_handler_falls_back_for_explicit_sticker_mood() -> None:
    fake = FakeTelegram()
    store = InMemoryStickerAliasStore()
    await store.upsert_alias(
        StickerAlias(alias="love", file_id="file-love", pack_name="pack")
    )
    handler = NaturalMessageHandler(
        telegram=fake,
        orchestrator=FakeOrchestrator("[sticker: celebrate]"),
        sticker_service=StickerService(fake, store),
    )

    update = _command_update("great news")
    assert update is not None
    handled = await handler.handle_update(update)

    assert handled is True
    assert fake.messages == []
    assert fake.stickers == [
        {"chat_id": 222, "sticker": "file-love", "reply_to_message_id": 10}
    ]


@pytest.mark.asyncio
async def test_natural_message_handler_converts_sticker_stage_direction() -> None:
    fake = FakeTelegram()
    store = InMemoryStickerAliasStore()
    await store.upsert_alias(
        StickerAlias(alias="angry", file_id="file-angry", pack_name="pack")
    )
    await store.upsert_alias(
        StickerAlias(alias="confused", file_id="file-confused", pack_name="pack")
    )
    handler = NaturalMessageHandler(
        telegram=fake,
        orchestrator=FakeOrchestrator("OI. Focus up. Move!\n\n*sends confused sticker*"),
        sticker_service=StickerService(fake, store),
    )

    update = _command_update("can u pretend to be angry?")
    assert update is not None
    handled = await handler.handle_update(update)

    assert handled is True
    assert fake.messages[-1]["text"] == "OI. Focus up. Move!"
    assert fake.stickers == [
        {"chat_id": 222, "sticker": "file-angry", "reply_to_message_id": 10}
    ]


@pytest.mark.asyncio
async def test_natural_message_handler_does_not_auto_attach_angry_contextual_sticker() -> None:
    fake = FakeTelegram()
    store = InMemoryStickerAliasStore()
    await store.upsert_alias(
        StickerAlias(alias="angry", file_id="file-angry", pack_name="pack")
    )
    await store.upsert_alias(
        StickerAlias(alias="confused", file_id="file-confused", pack_name="pack")
    )
    handler = NaturalMessageHandler(
        telegram=fake,
        orchestrator=FakeOrchestrator("OI. Focus up. Move!"),
        sticker_service=StickerService(fake, store),
    )

    update = _command_update("can u pretend to be angry?")
    assert update is not None
    handled = await handler.handle_update(update)

    assert handled is True
    assert fake.stickers == []
