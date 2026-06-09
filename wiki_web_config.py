"""
Claves de entorno gestionadas por la UI web (lectura/escritura .env y overlay por consulta).
"""
from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent

# Orden lógico para la UI (grupos en el front)
ENV_KEY_GROUPS: List[Tuple[str, List[str]]] = [
    (
        "Conexión a la wiki (BookStack)",
        [
            "BOOKSTACK_BASE_URL",
            "BOOKSTACK_TOKEN_ID",
            "BOOKSTACK_TOKEN_SECRET",
            "BOOKSTACK_HTTP_TIMEOUT_S",
        ],
    ),
    (
        "Origen de búsqueda y GLPI (mesa de ayuda)",
        [
            "WIKI_SEARCH_SOURCE",
            "WIKI_GLPI_ENABLED",
            "GLPI_BASE_URL",
            "GLPI_APP_TOKEN",
            "GLPI_USER_TOKEN",
            "GLPI_LOGIN",
            "GLPI_PASSWORD",
            "GLPI_HTTP_TIMEOUT_S",
            "GLPI_API_SESSION_WRITE",
            "GLPI_TICKET_TITLE_SEARCH_FIELD",
            "GLPI_TICKET_CONTENT_SEARCH_FIELD",
            "GLPI_TICKET_ID_SEARCH_FIELD",
            "WIKI_GLPI_SEARCH_IN_FOLLOWUPS",
            "GLPI_ITILFOLLOWUP_CONTENT_FIELD",
            "GLPI_ITILFOLLOWUP_ITEMS_ID_KEY",
            "GLPI_ITILFOLLOWUP_ITEMTYPE_KEY",
            "GLPI_ITILFOLLOWUP_ITEMTYPE_CONTAINS",
            "WIKI_GLPI_MAX_TICKETS",
            "WIKI_GLPI_RANK_POOL",
            "WIKI_GLPI_CONTEXT_MAX_CHARS",
            "WIKI_GLPI_TICKET_DESC_MAX_CHARS",
            "WIKI_GLPI_MAX_FOLLOWUPS",
            "WIKI_GLPI_FOLLOWUP_MAX_CHARS",
            "WIKI_GLPI_MAX_TICKET_USERS",
            "WIKI_GLPI_MAX_GROUP_LINKS",
            "WIKI_GLPI_MAX_SOLUTIONS",
            "WIKI_GLPI_MAX_TICKET_LINKS",
            "WIKI_GLPI_MAX_TASKS",
            "WIKI_GLPI_MAX_CHARS_PER_TICKET",
        ],
    ),
    (
        "Modelo de lenguaje (API OpenAI-compatible: Groq, OpenAI, Ollama, etc.)",
        [
            "LLM_BASE_URL",
            "OPENAI_COMPAT_BASE_URL",
            "LLM_API_KEY",
            "LLM_MODEL",
            "LLM_MAX_TOKENS",
            "LLM_TEMPERATURE",
            "LLM_HTTP_TIMEOUT_S",
            "LLM_VISION_MODEL",
            "GROQ_API_KEY",
            "GROQ_MODEL",
            "GROQ_MAX_TOKENS",
            "GROQ_TEMPERATURE",
            "GROQ_HTTP_TIMEOUT_S",
            "GROQ_VISION_MODEL",
            "WIKI_LLM_USER_MESSAGE_MAX_CHARS",
            "WIKI_LLM_USER_MESSAGE_MAX_CHARS_WITH_GLPI",
            "WIKI_GROQ_USER_MESSAGE_MAX_CHARS",
        ],
    ),
    (
        "Calidad de recuperación y visión",
        [
            "WIKI_QUALITY_MODE",
            "WIKI_SEARCH_MAX_VARIANTS",
            "WIKI_SEARCH_PREFIX_MAX_CHARS",
            "WIKI_SEARCH_STRIP_PREFIX_REGEX",
            "WIKI_SEARCH_VARIANTS_FILE",
            "WIKI_SEARCH_STOPWORDS_FILE",
            "WIKI_VISION_ENABLED",
            "WIKI_VISION_MAX_IMAGES",
            "WIKI_VISION_MAX_BYTES",
            "WIKI_TOP_K",
            "WIKI_KEYWORD_MAX",
        ],
    ),
    (
        "Límites de contexto textual",
        [
            "WIKI_PAGE_MAX_CHARS",
            "WIKI_BOOKSTACK_PAGE_SORT",
            "WIKI_PAGE_URL_DEMOTE_SUBSTRINGS",
            "WIKI_CONTEXT_MAX_CHARS",
            "WIKI_CONTEXT_MAX_CHARS_WITH_GLPI",
            "WIKI_RELEVANT_MAX_LINES",
            "WIKI_EVIDENCE_LINES_PAGE",
            "WIKI_EVIDENCE_LINES_PDF",
            "WIKI_EVIDENCE_LINES_TEXT",
            "WIKI_EVIDENCE_MAX_UNIQUE",
        ],
    ),
    (
        "PDF y adjuntos de texto",
        [
            "WIKI_ATTACH_PDF",
            "WIKI_PDF_MAX_PAGES",
            "WIKI_PDF_MAX_CHARS",
            "WIKI_PDF_MAX_FILES",
            "WIKI_PDF_CONTEXT_BUDGET",
            "WIKI_ATTACH_TEXT",
            "WIKI_TEXT_ATTACH_MAX_FILES",
            "WIKI_TEXT_ATTACH_MAX_CHARS",
            "WIKI_TEXT_CONTEXT_BUDGET",
        ],
    ),
    (
        "HTML, galería y recursos incrustados",
        [
            "WIKI_HTML_ASSETS",
            "WIKI_ASSETS_MAX_LINKS",
            "WIKI_ASSETS_MAX_IMAGES",
            "WIKI_ASSETS_CONTEXT_BUDGET",
            "WIKI_GALLERY",
            "WIKI_GALLERY_MAX_IMAGES",
        ],
    ),
    (
        "Comportamiento del asistente y rutas de archivo",
        [
            "WIKI_PERSONA_FILE",
            "WIKI_CONSTRAINTS_FILE",
            "WIKI_AI_MOOD",
            "WIKI_DOTENV_PATH",
        ],
    ),
    (
        "Interfaz web (requiere reinicio del servicio)",
        [
            "WIKI_WEB_HOST",
            "WIKI_WEB_PORT",
            "WIKI_WEB_GALLERY_MAX",
            "WIKI_PROXY_MAX_BYTES",
            "WIKI_PROXY_HTTP_TIMEOUT_S",
            "WIKI_UI_PRODUCT_NAME",
            "WIKI_UI_TAGLINE",
            "WIKI_CHAT_HISTORY_MAX_MESSAGES",
            "WIKI_CHAT_HISTORY_USER_MAX_CHARS",
            "WIKI_CHAT_HISTORY_ASSISTANT_MAX_CHARS",
        ],
    ),
]

CONFIG_ENV_KEYS: List[str] = []
for _label, keys in ENV_KEY_GROUPS:
    CONFIG_ENV_KEYS.extend(keys)

CONFIG_ENV_KEYS_SET = frozenset(CONFIG_ENV_KEYS)

# Valores por defecto (web + .env.example). Si en el proceso la variable está vacía o ausente, se usa esto.
ENV_DEFAULTS: Dict[str, str] = {
    "BOOKSTACK_BASE_URL": "",
    "BOOKSTACK_TOKEN_ID": "",
    "BOOKSTACK_TOKEN_SECRET": "",
    "BOOKSTACK_HTTP_TIMEOUT_S": "25",
    "WIKI_SEARCH_SOURCE": "both",
    "WIKI_GLPI_ENABLED": "false",
    "GLPI_BASE_URL": "",
    "GLPI_APP_TOKEN": "",
    "GLPI_USER_TOKEN": "",
    "GLPI_LOGIN": "",
    "GLPI_PASSWORD": "",
    "GLPI_HTTP_TIMEOUT_S": "45",
    "GLPI_API_SESSION_WRITE": "true",
    "GLPI_TICKET_TITLE_SEARCH_FIELD": "1",
    "GLPI_TICKET_CONTENT_SEARCH_FIELD": "21",
    "GLPI_TICKET_ID_SEARCH_FIELD": "2",
    "WIKI_GLPI_SEARCH_IN_FOLLOWUPS": "true",
    "GLPI_ITILFOLLOWUP_CONTENT_FIELD": "5",
    "GLPI_ITILFOLLOWUP_ITEMS_ID_KEY": "7",
    "GLPI_ITILFOLLOWUP_ITEMTYPE_KEY": "",
    "GLPI_ITILFOLLOWUP_ITEMTYPE_CONTAINS": "Ticket",
    "WIKI_GLPI_MAX_TICKETS": "1",
    "WIKI_GLPI_RANK_POOL": "40",
    "WIKI_GLPI_CONTEXT_MAX_CHARS": "6000",
    "WIKI_GLPI_TICKET_DESC_MAX_CHARS": "3200",
    "WIKI_GLPI_MAX_FOLLOWUPS": "15",
    "WIKI_GLPI_FOLLOWUP_MAX_CHARS": "1200",
    "WIKI_GLPI_MAX_TICKET_USERS": "40",
    "WIKI_GLPI_MAX_GROUP_LINKS": "20",
    "WIKI_GLPI_MAX_SOLUTIONS": "5",
    "WIKI_GLPI_MAX_TICKET_LINKS": "15",
    "WIKI_GLPI_MAX_TASKS": "20",
    "WIKI_GLPI_MAX_CHARS_PER_TICKET": "3200",
    "LLM_BASE_URL": "",
    "OPENAI_COMPAT_BASE_URL": "",
    "LLM_API_KEY": "",
    "LLM_MODEL": "llama-3.1-8b-instant",
    "LLM_MAX_TOKENS": "420",
    "LLM_TEMPERATURE": "0.1",
    "LLM_HTTP_TIMEOUT_S": "45",
    "LLM_VISION_MODEL": "meta-llama/llama-4-scout-17b-16e-instruct",
    "GROQ_API_KEY": "",
    "GROQ_MODEL": "llama-3.1-8b-instant",
    "GROQ_MAX_TOKENS": "420",
    "GROQ_TEMPERATURE": "0.1",
    "GROQ_HTTP_TIMEOUT_S": "45",
    "GROQ_VISION_MODEL": "meta-llama/llama-4-scout-17b-16e-instruct",
    "WIKI_LLM_USER_MESSAGE_MAX_CHARS": "9000",
    "WIKI_LLM_USER_MESSAGE_MAX_CHARS_WITH_GLPI": "7800",
    "WIKI_GROQ_USER_MESSAGE_MAX_CHARS": "9000",
    "WIKI_QUALITY_MODE": "balanced",
    "WIKI_SEARCH_MAX_VARIANTS": "16",
    "WIKI_SEARCH_PREFIX_MAX_CHARS": "72",
    "WIKI_SEARCH_STRIP_PREFIX_REGEX": "",
    "WIKI_SEARCH_VARIANTS_FILE": "",
    "WIKI_SEARCH_STOPWORDS_FILE": "",
    "WIKI_VISION_ENABLED": "true",
    "WIKI_VISION_MAX_IMAGES": "3",
    "WIKI_VISION_MAX_BYTES": "4000000",
    "WIKI_TOP_K": "3",
    "WIKI_KEYWORD_MAX": "8",
    "WIKI_PAGE_MAX_CHARS": "1800",
    "WIKI_BOOKSTACK_PAGE_SORT": "updated_at",
    "WIKI_PAGE_URL_DEMOTE_SUBSTRINGS": "",
    "WIKI_CONTEXT_MAX_CHARS": "9000",
    "WIKI_CONTEXT_MAX_CHARS_WITH_GLPI": "5200",
    "WIKI_RELEVANT_MAX_LINES": "55",
    "WIKI_EVIDENCE_LINES_PAGE": "5",
    "WIKI_EVIDENCE_LINES_PDF": "3",
    "WIKI_EVIDENCE_LINES_TEXT": "3",
    "WIKI_EVIDENCE_MAX_UNIQUE": "10",
    "WIKI_ATTACH_PDF": "true",
    "WIKI_PDF_MAX_PAGES": "8",
    "WIKI_PDF_MAX_CHARS": "2500",
    "WIKI_PDF_MAX_FILES": "2",
    "WIKI_PDF_CONTEXT_BUDGET": "4000",
    "WIKI_ATTACH_TEXT": "true",
    "WIKI_TEXT_ATTACH_MAX_FILES": "2",
    "WIKI_TEXT_ATTACH_MAX_CHARS": "3500",
    "WIKI_TEXT_CONTEXT_BUDGET": "5000",
    "WIKI_HTML_ASSETS": "true",
    "WIKI_ASSETS_MAX_LINKS": "22",
    "WIKI_ASSETS_MAX_IMAGES": "12",
    "WIKI_ASSETS_CONTEXT_BUDGET": "2200",
    "WIKI_GALLERY": "true",
    "WIKI_GALLERY_MAX_IMAGES": "8",
    "WIKI_PERSONA_FILE": "persona/PERSONA.md",
    "WIKI_CONSTRAINTS_FILE": "persona/CONSTRAINTS.md",
    "WIKI_AI_MOOD": "",
    "WIKI_DOTENV_PATH": "",
    "WIKI_WEB_HOST": "0.0.0.0",
    "WIKI_WEB_PORT": "8081",
    "WIKI_WEB_GALLERY_MAX": "16",
    "WIKI_PROXY_MAX_BYTES": "40000000",
    "WIKI_PROXY_HTTP_TIMEOUT_S": "120",
    "WIKI_UI_PRODUCT_NAME": "Wiki AI Agent",
    "WIKI_UI_TAGLINE": "Consultá tu wiki y tickets GLPI para resolver casos.",
    "WIKI_CHAT_HISTORY_MAX_MESSAGES": "12",
    "WIKI_CHAT_HISTORY_USER_MAX_CHARS": "800",
    "WIKI_CHAT_HISTORY_ASSISTANT_MAX_CHARS": "1400",
}

assert set(ENV_DEFAULTS.keys()) == CONFIG_ENV_KEYS_SET, (
    f"ENV_DEFAULTS y CONFIG_ENV_KEYS desalineados: "
    f"{set(ENV_DEFAULTS.keys()) ^ CONFIG_ENV_KEYS_SET}"
)

# Claves que conviene enmascarar hasta que el usuario pulse «mostrar»
SECRET_ENV_KEYS = frozenset(
    {
        "BOOKSTACK_TOKEN_SECRET",
        "LLM_API_KEY",
        "GROQ_API_KEY",
        "GLPI_APP_TOKEN",
        "GLPI_USER_TOKEN",
        "GLPI_PASSWORD",
    }
)

_KEY_LINE_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=")


def dotenv_path() -> Path:
    p = os.getenv("WIKI_DOTENV_PATH", "").strip()
    if p:
        return Path(p).expanduser().resolve()
    return (PROJECT_ROOT / ".env").resolve()


def _escape_env_doublequoted(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def parse_dotenv_values(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        k, _, v = s.partition("=")
        key = k.strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[key] = v
    return out


def merge_dotenv_file(path: Path, updates: Dict[str, str]) -> None:
    """Actualiza o añade claves conocidas; preserva el resto del archivo."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    keys_written: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        m = _KEY_LINE_RE.match(line)
        if m:
            key = m.group(2)
            if key in updates:
                val = updates[key]
                new_lines.append(f'{key}="{_escape_env_doublequoted(val)}"')
                keys_written.add(key)
                continue
        new_lines.append(line)
    for key, val in updates.items():
        if key not in keys_written:
            new_lines.append(f'{key}="{_escape_env_doublequoted(val)}"')
    path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")


def seed_missing_env_defaults() -> None:
    """Rellena os.environ con ENV_DEFAULTS solo donde falte o esté en blanco (útil sin .env completo)."""
    for k in CONFIG_ENV_KEYS:
        cur = (os.getenv(k) or "").strip()
        if not cur:
            os.environ[k] = ENV_DEFAULTS.get(k, "")


def snapshot_env_for_ui() -> Dict[str, str]:
    """Valores para la UI: proceso + fallback a ENV_DEFAULTS si sigue vacío."""
    out: Dict[str, str] = {}
    for k in CONFIG_ENV_KEYS:
        cur = (os.getenv(k) or "").strip()
        out[k] = cur if cur else ENV_DEFAULTS.get(k, "")
    return out


def runtime_env_with_defaults(runtime: Dict[str, str]) -> Dict[str, str]:
    """Una consulta: merge cliente + defaults para no mandar claves vacías al LLM/wiki."""
    merged: Dict[str, str] = {}
    for k in CONFIG_ENV_KEYS:
        v = (runtime.get(k) or "").strip()
        merged[k] = v if v else ENV_DEFAULTS.get(k, "")
    return merged


def resolve_project_path(rel_or_abs: str) -> Path:
    p = Path(rel_or_abs.strip())
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / p).resolve()


@contextlib.contextmanager
def env_overlay(updates: Dict[str, str]) -> Iterator[None]:
    """
    Aplica variables solo durante run_ask; no escribe disco.
    Solo acepta claves en CONFIG_ENV_KEYS_SET.
    """
    saved: Dict[str, str | None] = {}
    try:
        for k, v in updates.items():
            if k not in CONFIG_ENV_KEYS_SET:
                continue
            saved[k] = os.environ.get(k)
            os.environ[k] = str(v)
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def read_persona_files() -> Tuple[str, str, str, str]:
    """persona_path_str, constraints_path_str, persona_body, constraints_body"""
    pf = os.getenv("WIKI_PERSONA_FILE", "persona/PERSONA.md").strip() or "persona/PERSONA.md"
    cf = os.getenv("WIKI_CONSTRAINTS_FILE", "persona/CONSTRAINTS.md").strip() or "persona/CONSTRAINTS.md"
    pp = resolve_project_path(pf)
    cp = resolve_project_path(cf)
    pt = pp.read_text(encoding="utf-8") if pp.is_file() else ""
    ct = cp.read_text(encoding="utf-8") if cp.is_file() else ""
    return str(pf), str(cf), pt, ct


def write_persona_files(
    persona_path_str: str,
    constraints_path_str: str,
    persona_text: str,
    constraints_text: str,
) -> None:
    pp = resolve_project_path(persona_path_str.strip() or "persona/PERSONA.md")
    cp = resolve_project_path(constraints_path_str.strip() or "persona/CONSTRAINTS.md")
    pp.parent.mkdir(parents=True, exist_ok=True)
    cp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text(persona_text, encoding="utf-8")
    cp.write_text(constraints_text, encoding="utf-8")
