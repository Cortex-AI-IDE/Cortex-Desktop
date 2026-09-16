"""Turn a provider's HTTP error body into text a person can read in the chat.

DeepSeek and Anthropic both answer a bad request with JSON, and the providers
used to put that JSON straight into the error the chat displays:

    DeepSeek API HTTP 400: 400 Client Error: Bad Request for url: ... |
    {"error":{"message":"The supported API model names are ...", ...}}

The chat's final cleaner (chat_text.strip_all_control_tags, Pass 8) deletes
anything shaped like a JSON object with a "message" key, so all the user saw
was `{"error":}` - the one sentence that said what was wrong was the part
that got removed.

readable_api_error() pulls that sentence out. The error `type` and `code` are
kept alongside it because agent_bridge matches phrases such as
'context_length_exceeded' in error text to trigger recovery; dropping them
would break that. The full raw body still goes to the log.
"""
from __future__ import annotations

import json


def readable_api_error(body: str, limit: int = 600) -> str:
    """Return the provider's own error message, plus its type/code.

    Handles the shapes these APIs actually send:
      {"error": {"message": ..., "type": ..., "code": ...}}   OpenAI / DeepSeek
      {"type": "error", "error": {"type": ..., "message": ...}} Anthropic
      {"error": "plain string"}
      {"message": ..., "code": ...}
    Anything that is not JSON comes back as trimmed text; an empty body gives "".
    """
    text = (body or "").strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
    except Exception:
        return text[:limit]
    if not isinstance(data, dict):
        return text[:limit]

    err = data.get("error")
    if isinstance(err, dict):
        message = err.get("message") or ""
        extras = [err.get("type"), err.get("code")]
    elif isinstance(err, str):
        message, extras = err, []
    else:
        message = data.get("message") or ""
        extras = [data.get("type") if data.get("type") != "error" else None, data.get("code")]

    if not message:
        return text[:limit]
    tags = []
    for x in extras:
        if x and str(x) not in tags:
            tags.append(str(x))
    out = str(message).strip()
    if tags:
        out += " (" + ", ".join(tags) + ")"
    return out[:limit]


# ── Unknown model names ──────────────────────────────────────────────────────
# A provider that does not know a model name answers 400 or 404. Every provider
# words it differently, so this matches the specific codes and phrases they use
# rather than anything as loose as "not supported" - MiMo's "reasoning_content
# is not supported" style errors are about something else entirely.
_UNKNOWN_MODEL_CODES = ("model_not_found", "not_found_error", "invalid_model", "model_not_exist")
_UNKNOWN_MODEL_PHRASES = (
    "does not exist", "not a valid model", "supported api model names",
    "unknown model", "no such model", "model not found", "invalid model",
    "model is not supported", "unsupported model",
)


def is_unknown_model_error(status: int, body: str, model: str = "") -> bool:
    """True when a provider's error says it does not know the requested model."""
    if status not in (400, 404):
        return False
    low = (body or "").lower()
    if not low:
        return False
    if any(code in low for code in _UNKNOWN_MODEL_CODES):
        return True
    if any(phrase in low for phrase in _UNKNOWN_MODEL_PHRASES):
        return True
    # A 404 that names the exact model is a missing model, not a missing URL.
    return status == 404 and bool(model) and model.lower() in low


def unknown_model_message(provider, model: str, status: int, body: str):
    """One plain message for an unknown model name, or None if it isn't one.

    Looks up what the provider does accept (src/ai/provider_catalog, the user's
    own key, cached) so the message can name the right spelling. If the
    provider's list DOES contain the model, something else is wrong and this
    returns None so the ordinary error is shown instead.
    """
    try:
        if not is_unknown_model_error(status, body, model):
            return None
        label = getattr(provider, "DISPLAY_NAME", "") or type(provider).__name__.replace("Provider", "")
        try:
            from src.ai.provider_catalog import accepted_model_ids
            accepted = accepted_model_ids(provider)
        except Exception:  # noqa: BLE001
            accepted = None
        if accepted and model in accepted:
            return None
        import difflib
        parts = [f"{label} does not recognise the model name '{model}'."]
        twin = {a.lower(): a for a in accepted}.get((model or "").lower()) if accepted else None
        if twin:
            parts.append(f"Its API spells it '{twin}' - model names are case-sensitive.")
        elif accepted:
            close = difflib.get_close_matches(model or "", sorted(accepted), n=5, cutoff=0.4)
            names = close or sorted(accepted)[:8]
            parts.append(("Closest names its API accepts: " if close else "Names its API accepts include: ")
                         + ", ".join(names) + ".")
        reason = readable_api_error(body)
        if reason:
            parts.append(f"{label} said: {reason}")
        parts.append("If this model came from the Cortex model list, its id needs correcting "
                     "in the admin panel (AI Models).")
        return " ".join(parts)
    except Exception:  # noqa: BLE001 - explaining an error must never raise one
        return None
