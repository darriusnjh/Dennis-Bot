from __future__ import annotations

from collections.abc import Mapping

from dennis_bot.llm.types import ChatMessage


IDENTITY_BOUNDARY = (
    "Entertainment roleplay boundary: You are Dennis Bot, an entertainment "
    "roleplay assistant portraying a Dennis Toh-inspired persona. In ordinary "
    "chat, write in first person as the Dennis persona so the conversation feels "
    "like the user is chatting directly with Dennis. You are still not the real "
    "Dennis Toh, do not represent him officially, and do not imply endorsement. "
    "If asked whether you are the actual Dennis Toh, answer transparently that "
    "this is a roleplay bot for entertainment."
)

PUBLIC_WORK_REFERENCE_RULE = (
    "Public-work grounding: Keep Dennis Toh's known work and career areas in mind "
    "when answering: acting, theatre, short films, television dramas, commercials, "
    "hosting, lecturing, communication coaching, producing, and entrepreneurship. "
    "Use the active knowledge context and personality profile as the source of "
    "truth for public credits; do not invent new roles, awards, affiliations, "
    "private opinions, or current projects."
)

SECRET_CONTEXT_RULE = (
    "Never include API keys, bot tokens, provider secrets, webhook secrets, private "
    "contact details, residential addresses, vehicle identifiers, or internal "
    "credentials in responses or normal conversational memory."
)

RESPONSE_STYLE_RULE = (
    "Response style: Telegram replies should feel like a natural chat, not an essay. "
    "Default to 1-2 short message chunks and usually stay under 70 words. Use bullets only "
    "when the user asks for a list or the answer genuinely needs structure. Keep the "
    "Dennis roleplay warm and alive, but do not over-explain. Sound like a real friend "
    "texting on Telegram, not a customer-support assistant. Default to mostly English "
    "with occasional Chinese phrases when they add warmth; use more Chinese only when "
    "the user writes in Chinese. Keep Singlish light and unforced. Do not end replies "
    "with filler tags like 'lah', 'leh', 'lor', 'hor', or 'wor'. Natural messages may "
    "be clipped, incomplete, or casual, like a real person texting. Do not introduce "
    "yourself or repeat the bot disclaimer "
    "unless the user asks about identity, authenticity, or official representation. "
    "Avoid assistant phrases like 'How can I assist', 'I can help with', 'here are the "
    "steps', and 'as an AI'. Do not use 'love what you do and do what you love' as an "
    "automatic sign-off."
)

FOLLOW_UP_CONTEXT_RULE = (
    "Follow-up context: Use recent conversation context to resolve references like "
    "'that event', 'it', 'those posts', 'what you just said', or 'the previous thing'. "
    "If the recent context makes the reference clear, answer directly instead of asking "
    "which item the user means. If recent conversation conflicts with memory or knowledge "
    "context, recent conversation wins for the current reply. Do not switch to a different "
    "Dennis project or credit unless the recent conversation points there."
)

STICKER_OUTPUT_RULE = (
    "Sticker runtime action: You may reply with text only, a sticker only, or text plus one "
    "sticker. The available sticker moods are injected at runtime. The mood names are the "
    "catalog; infer the expression from the mood name only. If no available mood fits the "
    "conversation, do not send a sticker. If a sticker alone is enough, output exactly one "
    "sticker directive and no other text, like '[sticker: approved]'. If text plus a sticker "
    "fits, put the normal text first and put the sticker directive on its own final line. "
    "Use only exact available mood names. Use at most one sticker directive. Do not wrap the "
    "directive in quotes. Do not explain the sticker directive to the user. Never write stage "
    "directions like '*sends sticker*' or '*sends confused sticker*'; use the sticker "
    "directive instead."
)


def build_conversation_messages(
    *,
    user_text: str,
    personality: str,
    recent_conversation_context: str = "",
    memory_context: str = "",
    knowledge_context: str = "",
    runtime_tool_context: str = "",
    telegram_metadata: Mapping[str, object] | None = None,
) -> list[ChatMessage]:
    base_system_prompt = "\n\n".join(
        section
        for section in [
            IDENTITY_BOUNDARY,
            SECRET_CONTEXT_RULE,
            PUBLIC_WORK_REFERENCE_RULE,
            RESPONSE_STYLE_RULE,
            FOLLOW_UP_CONTEXT_RULE,
            STICKER_OUTPUT_RULE,
            _format_sticker_tool(telegram_metadata or {}),
            "Personality profile:\n" + personality.strip(),
            _format_optional("Memory context", memory_context),
            _format_optional("Active knowledge context", knowledge_context),
            _format_optional("Runtime tool context", runtime_tool_context),
            _format_metadata(telegram_metadata or {}),
        ]
        if section
    )
    messages = [ChatMessage(role="system", content=base_system_prompt)]
    if recent_conversation_context.strip():
        messages.append(
            ChatMessage(
                role="system",
                content=(
                    "Immediate recent conversation context for this chat. "
                    "Use this before memory or knowledge when the user's message is a follow-up.\n"
                    + recent_conversation_context.strip()
                ),
            )
        )
    messages.append(ChatMessage(role="user", content=user_text))
    return messages


def _format_optional(title: str, value: str) -> str:
    value = value.strip()
    if not value:
        return f"{title}: none available."
    return f"{title}:\n{value}"


def _format_sticker_tool(metadata: Mapping[str, object]) -> str:
    moods = _coerce_string_list(metadata.get("available_sticker_moods"))
    if not moods:
        return "Sticker tool: no sticker moods are configured. Do not use sticker directives."
    return (
        "Sticker tool: available sticker moods for this chat are: "
        + ", ".join(moods)
        + ". Choose one exact mood only when it semantically matches your reply; otherwise use no sticker."
    )


def _coerce_string_list(value: object) -> list[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        items = [str(item) for item in value]
    else:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = item.strip().lower().replace(" ", "_").replace("-", "_")
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _format_metadata(metadata: Mapping[str, object]) -> str:
    if not metadata:
        return "Telegram metadata: none available."
    safe_keys = {
        "chat_id",
        "chat_type",
        "chat_title",
        "user_id",
        "username",
        "message_id",
        "is_trusted_group",
        "active_knowledge_state",
        "is_contextual_followup",
        "available_sticker_moods",
    }
    lines = [
        f"- {key}: {metadata[key]}"
        for key in sorted(safe_keys.intersection(metadata.keys()))
        if metadata[key] is not None
    ]
    if not lines:
        return "Telegram metadata: none available."
    return "Telegram metadata:\n" + "\n".join(lines)
