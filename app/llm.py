"""Minimal LLM client — raw HTTP requests, no SDKs, no LangChain.

Works with any OpenAI-compatible /v1/chat/completions endpoint:
  - Ollama (local):        http://localhost:11434/v1
  - OpenRouter:            https://openrouter.ai/api/v1
  - OpenAI:                https://api.openai.com/v1
  - LM Studio (local):     http://localhost:1234/v1

Configuration is read from the settings table (see app/commands.py schema),
managed via the Settings page. This keeps the app "agnostic" per the PRD:
swapping providers is just changing three text fields, no code changes.
"""

import requests


class LLMConfigError(Exception):
    """Raised when the LLM settings are missing or incomplete."""


class LLMRequestError(Exception):
    """Raised when the LLM HTTP request fails or returns an error."""


def get_llm_settings(db):
    rows = db.execute(
        "SELECT key, value FROM settings WHERE key IN "
        "('llm_api_base_url', 'llm_api_key', 'llm_model_name')"
    ).fetchall()
    settings = {row[0]: row[1] for row in rows}
    return {
        "base_url": (settings.get("llm_api_base_url") or "").rstrip("/"),
        "api_key": settings.get("llm_api_key") or "",
        "model": settings.get("llm_model_name") or "",
    }


def chat_completion(db, messages, timeout=120):
    """Send a chat completion request to the configured LLM provider.

    messages: list of {"role": "system"|"user"|"assistant", "content": str}
    Returns the assistant's reply text.
    Raises LLMConfigError if settings are incomplete, LLMRequestError on
    any HTTP/network/response failure.
    """
    config = get_llm_settings(db)

    if not config["base_url"] or not config["model"]:
        raise LLMConfigError(
            "LLM is not configured. Go to Settings and set the API base URL and model name."
        )

    url = f"{config['base_url']}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if config["api_key"]:
        headers["Authorization"] = f"Bearer {config['api_key']}"

    payload = {
        "model": config["model"],
        "messages": messages,
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.exceptions.RequestException as exc:
        raise LLMRequestError(f"Could not reach LLM provider at {url}: {exc}") from exc

    if response.status_code != 200:
        raise LLMRequestError(
            f"LLM provider returned HTTP {response.status_code}: {response.text[:500]}"
        )

    try:
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise LLMRequestError(f"Unexpected response format from LLM provider: {exc}") from exc
