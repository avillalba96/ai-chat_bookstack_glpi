# Guía para agentes / colaboradores

## Qué es este proyecto

**Asistente de consulta** para técnicos de soporte: busca en **BookStack** y opcionalmente **GLPI (solo lectura)** y genera respuestas con LLM. **No escribe en GLPI.**

No confundir con `ai-glpi_autoresponder` (otro repo: automatización de tickets).

## Punto de entrada del código

- `wiki_ask.run_ask()` — lógica central
- `web_app.py` — Flask + `/api/ask`
- `persona/` — comportamiento del asistente sin tocar `.py`

## Reglas al contribuir

- Secretos solo en `.env`; nunca en commits.
- Perfiles/personas con datos reales → `persona.local/` (gitignored).
- Nuevos límites → documentar en `.env.example`.
- Cambios de tono → preferir `persona/PERSONA.md` antes que hardcodear en `wiki_ask.py`.

## Probar localmente

```bash
source .venv/bin/activate
./wiki-ask "pregunta de prueba" --debug
./wiki-web
```
