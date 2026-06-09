"""
Compatibilidad: Groq es un endpoint OpenAI-compatible.
Preferí `OpenAICompatClient` con LLM_BASE_URL / LLM_API_KEY en .env.
"""
from __future__ import annotations

from openai_compat_client import OpenAICompatClient


class GroqClient(OpenAICompatClient):
    """Deprecado a favor de OpenAICompatClient + LLM_* en entorno; se mantiene por imports viejos."""

    def __init__(self, api_key: str, timeout_s: int = 45) -> None:
        super().__init__(
            base_url="https://api.groq.com/openai/v1",
            api_key=api_key,
            timeout_s=timeout_s,
        )
