# Configuración (referencia)

Todas las variables viven en `.env`. Ver `.env.example` comentado.

## Mínimo (solo wiki)

| Variable | Descripción |
|----------|-------------|
| `BOOKSTACK_BASE_URL` | URL base sin `/` final |
| `BOOKSTACK_TOKEN_ID` | ID del API token |
| `BOOKSTACK_TOKEN_SECRET` | Secret del API token |
| `LLM_API_KEY` o `GROQ_API_KEY` | Bearer del proveedor LLM |

## LLM

| Variable | Default orientativo |
|----------|---------------------|
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` |
| `LLM_MODEL` | `llama-3.1-8b-instant` |
| `LLM_VISION_MODEL` | modelo multimodal (Groq Scout, etc.) |
| `LLM_MAX_TOKENS` | `420` (preset balanced) |
| `WIKI_QUALITY_MODE` | `economy` \| `balanced` \| `thorough` |

Ollama local:

```env
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_API_KEY=
```

## Búsqueda y contexto

| Variable | Efecto |
|----------|--------|
| `WIKI_TOP_K` | Páginas wiki por consulta |
| `WIKI_CONTEXT_MAX_CHARS` | Tope total contexto wiki |
| `WIKI_ATTACH_PDF` | Extraer PDFs adjuntos |
| `WIKI_VISION_ENABLED` | Usar visión si hay capturas wiki |
| `WIKI_SEARCH_VARIANTS_FILE` | Frases extra para búsqueda (una por línea) |

## GLPI

| Variable | Efecto |
|----------|--------|
| `WIKI_GLPI_ENABLED` | `true` para habilitar cliente GLPI |
| `GLPI_BASE_URL` | URL del sitio (sin `/front/...`) |
| `GLPI_USER_TOKEN` | Token de usuario API |
| `GLPI_API_SESSION_WRITE` | `true` si necesitás ver notas internas |
| `WIKI_SEARCH_SOURCE` | `wiki` \| `glpi` \| `both` |
| `WIKI_GLPI_MAX_TICKETS` | Máx. tickets en contexto |

## Interfaz web

| Variable | Default |
|----------|---------|
| `WIKI_WEB_HOST` | `0.0.0.0` |
| `WIKI_WEB_PORT` | `8081` |
| `WIKI_UI_PRODUCT_NAME` | `Wiki AI Agent` |
| `WIKI_CHAT_HISTORY_MAX_MESSAGES` | `12` |

## Persona

| Variable | Default |
|----------|---------|
| `WIKI_PERSONA_FILE` | `persona/PERSONA.md` |
| `WIKI_CONSTRAINTS_FILE` | `persona/CONSTRAINTS.md` |
| `WIKI_AI_MOOD` | línea opcional de tono del día |
