# Contribuir

Proyecto: **asistente de consulta** BookStack ± GLPI (solo lectura). Ver [AGENTS.md](AGENTS.md).

## Configuración local

1. Cloná el repositorio.
2. `./setup.sh` (crea venv, instala deps, copia `.env.example` → `.env` si falta).
3. Completá credenciales **solo en `.env`** (bloque mínimo al inicio de `.env.example`).
4. Datos reales de persona → `persona.local/` (gitignored), no en `persona/` del repo.

## Estilo del proyecto

- Cambios **acotados** al objetivo del PR/commit.
- La **impronta** del asistente vive en `persona/PERSONA.md` y `persona/CONSTRAINTS.md`, no en lógica innecesaria dentro de `wiki_ask.py`.
- Los **límites** (tokens, recortes, timeouts) deben ser **configurables por `.env`**; si agregás un tope nuevo, documentalo en `.env.example`.

## Free tier Groq

Antes de subir defaults más “ricos” (más páginas, más PDF, más `max_tokens`), verificá el impacto en [rate limits](https://console.groq.com/docs/rate-limits) y dejá valores conservadores en `.env.example`.

## Commits

Mensajes claros en español o inglés, una idea por commit cuando sea posible.
