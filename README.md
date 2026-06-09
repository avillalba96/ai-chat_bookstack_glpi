# ai-chat_bookstack_glpi

**Asistente de resolución de casos para equipos de soporte:** preguntás en lenguaje natural y el sistema **busca en tu wiki BookStack** y/o **tickets GLPI (solo lectura)**, arma contexto con procedimientos, PDFs, capturas y seguimientos históricos, y el LLM te devuelve **pasos accionables con evidencia**.

> **No escribe en GLPI.** No asigna tickets. No publica notas. Es una herramienta de **consulta y razonamiento** para el técnico que está resolviendo el incidente.

---

## Caso de uso

```
Técnico N2 atiende un ticket o investiga un síntoma
        ↓
«¿Cómo se hace X según la wiki?» / «¿Qué pasó en tickets parecidos?»
        ↓
Búsqueda automática (wiki ± GLPI) + extracción de contexto
        ↓
LLM responde: diagnóstico, pasos, validación, evidencia citada
        ↓
El técnico aplica la solución en su herramienta de tickets
```

---

## Características

| Capacidad | Detalle |
|-----------|---------|
| **Fuentes** | BookStack · GLPI · ambos (`wiki` / `glpi` / `both`) |
| **Interfaces** | CLI (`./wiki-ask`) · chat web (`./wiki-web`) |
| **Contexto wiki** | Páginas HTML/Markdown, PDFs, adjuntos texto, galería, draw.io (texto), enlaces |
| **Contexto GLPI** | Tickets por ID o búsqueda por texto; seguimientos, notas internas visibles, soluciones, vínculos |
| **Visión LLM** | Capturas de la wiki descargadas automáticamente cuando aportan (Scout u otro modelo multimodal) |
| **Evidencia** | Respuestas estructuradas; citas literales de `EVIDENCIA_DISPONIBLE`; sección **Fuentes** automática |
| **Personalidad** | `persona/PERSONA.md` + `persona/CONSTRAINTS.md` sin tocar Python |
| **LLM** | Cualquier API **OpenAI-compatible** (Groq, OpenAI, Ollama, LiteLLM, etc.) |
| **Calidad** | Presets `economy` · `balanced` · `thorough` (tokens y contexto) |

---

## Arquitectura

```text
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  wiki-ask   │     │   wiki-web   │     │  persona/*.md   │
│    (CLI)    │     │   (Flask)    │     │  + .env         │
└──────┬──────┘     └──────┬───────┘     └────────┬────────┘
       │                   │                      │
       └─────────┬─────────┘                      │
                 ▼                                │
          ┌──────────────┐                        │
          │  wiki_ask.py │◄───────────────────────┘
          │   run_ask()  │
          └──────┬───────┘
                 │
     ┌───────────┼───────────┐
     ▼           ▼           ▼
┌─────────┐ ┌─────────┐ ┌──────────────┐
│BookStack│ │  GLPI   │ │ OpenAI-compat│
│  API    │ │  REST   │ │     LLM      │
│ search  │ │ search  │ │ text/vision  │
│ pages   │ │ tickets │ │              │
└─────────┘ └─────────┘ └──────────────┘
```

Detalle: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## Inicio rápido

```bash
git clone git@github.com:avillalba96/ai-chat_bookstack_glpi.git
cd ai-chat_bookstack_glpi
chmod +x setup.sh && ./setup.sh
```

Editá `.env` (mínimo):

```env
BOOKSTACK_BASE_URL=https://wiki.tu-organizacion.example
BOOKSTACK_TOKEN_ID=
BOOKSTACK_TOKEN_SECRET=
LLM_API_KEY=          # o GROQ_API_KEY
```

```bash
source .venv/bin/activate
./wiki-ask "cómo reinicio el servicio de correo según la documentación"
./wiki-web            # http://127.0.0.1:8081
```

Guía paso a paso: [docs/QUICKSTART.md](docs/QUICKSTART.md)

---

## GLPI (opcional)

Por defecto el proyecto funciona **solo con BookStack**. Para buscar también en tickets:

```env
WIKI_GLPI_ENABLED=true
GLPI_BASE_URL=https://glpi.tu-organizacion.example
GLPI_USER_TOKEN=
GLPI_APP_TOKEN=          # si tu instancia lo exige
WIKI_SEARCH_SOURCE=both    # wiki | glpi | both
```

En la UI web elegís fuente: solo wiki, solo GLPI o ambos.

---

## Personalización

| Objetivo | Dónde |
|----------|--------|
| Tono y formato de respuesta | `persona/PERSONA.md` |
| Reglas duras (no inventar) | `persona/CONSTRAINTS.md` |
| Más/menos contexto | `.env` → `WIKI_QUALITY_MODE` |
| Producción con persona propia | `persona.local/` + `WIKI_PERSONA_FILE` (gitignored) |

Variables: [docs/CONFIGURATION.md](docs/CONFIGURATION.md)

---

## Seguridad

- Secretos **solo** en `.env` — ver [SECURITY.md](SECURITY.md)
- BookStack: **API Tokens**, no contraseña de usuario en scripts
- Chat web: por defecto `0.0.0.0` — usá **TLS + firewall** en producción
- Proxy de assets: solo URLs bajo tu `BOOKSTACK_BASE_URL`; sin persistencia en disco
- El LLM recibe contexto **recortado**; revisá límites en `.env` si manejás datos sensibles

Despliegue: [docs/GUIA_DESPLIEGUE.md](docs/GUIA_DESPLIEGUE.md)

---

## Estructura del proyecto

```text
wiki_ask.py           # Motor: búsqueda, contexto, LLM (run_ask)
web_app.py            # API Flask + UI chat
bookstack_client.py   # Cliente BookStack API
glpi_client.py        # Cliente GLPI REST (lectura)
bookstack_assets.py   # Enlaces, imágenes, draw.io desde HTML
openai_compat_client.py
persona/              # Identidad del asistente (demo genérica)
static/ templates/    # Frontend del chat
wiki-ask / wiki-web   # Entradas bash
```

---

## Licencia

MIT — [LICENSE](LICENSE) · [CONTRIBUTING.md](CONTRIBUTING.md)
