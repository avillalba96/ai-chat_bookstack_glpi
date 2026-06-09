from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests


class OpenAICompatClient:
    """
    Cliente para APIs estilo OpenAI (`POST {base}/chat/completions`).
    Sirve para Groq, OpenAI, Azure OpenAI (ruta compatible), Ollama (/v1), LiteLLM, proxies, etc.
    Si `api_key` está vacío, no se envía cabecera Authorization (útil en Ollama local).
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        timeout_s: int = 45,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        b = (base_url or "").strip().rstrip("/")
        if not b:
            raise ValueError("LLM: base_url vacío (definí LLM_BASE_URL o equivalente).")
        self.base_url = b
        self.timeout_s = timeout_s
        self.session = requests.Session()
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if (api_key or "").strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"
        if extra_headers:
            headers.update(extra_headers)
        self.session.headers.update(headers)

    def chat_completions(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 700,
    ) -> str:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                r = self.session.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    timeout=self.timeout_s,
                )
                if r.status_code in (429, 500, 502, 503, 504):
                    retry_after = r.headers.get("retry-after")
                    if retry_after and retry_after.isdigit():
                        wait_s = min(30, int(retry_after))
                    else:
                        wait_s = min(8, 1.5**attempt)
                    time.sleep(wait_s)
                    continue
                r.raise_for_status()
                data = r.json()
                return (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
            except Exception as e:
                last_exc = e
                time.sleep(min(8, 1.5**attempt))
                continue
        if last_exc:
            raise last_exc
        raise RuntimeError("LLM: sin respuesta tras reintentos")

    def chat(
        self,
        *,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 700,
    ) -> str:
        return self.chat_completions(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
        )
