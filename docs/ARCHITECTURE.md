# Arquitectura

## Propósito

`ai-chat_bookstack_glpi` ayuda a **resolver incidentes** combinando documentación interna (BookStack) con casos históricos en GLPI. Es **read-only**: no modifica tickets ni wiki.

## Flujo `run_ask()` (`wiki_ask.py`)

1. **Entrada:** pregunta en lenguaje natural + `AskParams` (calidad, fuente, flags).
2. **Fuente:** `WIKI_SEARCH_SOURCE` → `wiki` | `glpi` | `both`.
3. **BookStack (si aplica):**
   - Genera variantes de búsqueda (`_wiki_bookstack_search_variants`).
   - `BookStackClient.search_multi` → páginas/libros.
   - `build_context`: HTML/Markdown, PDF (`pypdf`), adjuntos texto, galería, assets HTML.
   - Extrae líneas de **evidencia** para citas literales.
4. **GLPI (si `WIKI_GLPI_ENABLED=true`):**
   - Detecta IDs en la pregunta (`ticket 12345`) o busca por texto en títulos/seguimientos.
   - `GlpiClient` carga bundle: ticket, followups, users, groups, solutions, links.
   - Formatea bloque Markdown para el LLM.
   - Ranking léxico (`_rank_glpi_hits`) para limitar tokens.
5. **Persona:** `persona_loader` + `WIKI_AI_MOOD` → system prompt.
6. **LLM:**
   - Texto: `OpenAICompatClient.chat` con historial opcional (web).
   - Visión: si hay imágenes BookStack descargables → modelo `LLM_VISION_MODEL`.
7. **Salida:** `AskResult` con respuesta, fuentes (URLs), `search_hits`, flag visión.

## Interfaz web (`web_app.py`)

| Ruta | Función |
|------|---------|
| `GET /` | Chat HTML |
| `POST /api/ask` | Llama `run_ask`; reescribe URLs wiki → `/api/proxy` |
| `GET /api/proxy` | Proxy en memoria hacia BookStack (PDF/imágenes) |
| `GET/POST /api/config` | Editar `.env` y persona desde la UI (opcional) |

Historial de conversación: **localStorage** en el navegador; al reenviar, se manda historial acotado al LLM.

## Módulos

| Archivo | Responsabilidad |
|---------|-----------------|
| `bookstack_client.py` | Search API, lectura páginas, descarga imágenes/PDF |
| `bookstack_assets.py` | Parseo HTML/MD: links, imgs, draw.io |
| `glpi_client.py` | initSession, search Ticket/Followup, get_ticket_bundle |
| `openai_compat_client.py` | POST `/chat/completions`, reintentos 429/5xx |
| `wiki_web_config.py` | Defaults UI, grupos de env, merge `.env` |
| `persona_loader.py` | Carga PERSONA + CONSTRAINTS + mood |

## Decisiones de diseño

- **Sin embeddings propios:** búsqueda nativa BookStack/GLPI + ranking léxico; simple de desplegar.
- **Recortes agresivos:** presupuestos por PDF, HTML, GLPI y mensaje usuario evitan 413/TPM.
- **Visión solo BookStack:** imágenes GLPI no van al modelo multimodal (seguridad y ruido).
- **Evidencia separada del contexto:** el LLM debe citar solo `EVIDENCIA_DISPONIBLE`.
- **Provider-agnostic:** `LLM_*` con fallback `GROQ_*` por compatibilidad.

## Extensión

- Nuevos proveedores LLM: ajustar `LLM_BASE_URL` + clave.
- Otra wiki: hoy acoplado a BookStack API; otro backend requeriría cliente nuevo.
- Más fuentes (Confluence, etc.): patrón similar a `glpi_client` + bloque en `run_ask`.
