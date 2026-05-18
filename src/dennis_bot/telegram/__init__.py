from dennis_bot.telegram.client import TelegramClient
from dennis_bot.telegram.normalize import normalize_update
from dennis_bot.telegram.polling import TelegramPollingRunner

__all__ = ["CommandRouter", "TelegramClient", "TelegramPollingRunner", "normalize_update"]


def __getattr__(name: str):
    if name == "CommandRouter":
        from dennis_bot.telegram.router import CommandRouter

        return CommandRouter
    raise AttributeError(name)
