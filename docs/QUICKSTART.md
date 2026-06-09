# Inicio rápido

## 1. Requisitos

- Python 3.10+
- BookStack con API y token (*Access System API*)
- Clave LLM (Groq, OpenAI, etc.) u Ollama local

## 2. Instalación

```bash
git clone git@github.com:avillalba96/ai-chat_bookstack_glpi.git
cd ai-chat_bookstack_glpi
chmod +x setup.sh wiki-ask wiki-web
./setup.sh
```

## 3. Configuración mínima (`.env`)

```env
BOOKSTACK_BASE_URL=https://wiki.ejemplo.org
BOOKSTACK_TOKEN_ID=tu_token_id
BOOKSTACK_TOKEN_SECRET=tu_token_secret
LLM_API_KEY=gsk_...   # o GROQ_API_KEY
```

Crear token en BookStack: Perfil → API Tokens → *Access System API*.

## 4. Primera consulta (CLI)

```bash
source .venv/bin/activate
./wiki-ask "procedimiento de backup del servidor de archivos"
```

## 5. Chat web

```bash
./wiki-web
```

Abrí http://127.0.0.1:8081 — Enter envía, Mayús+Enter nueva línea.

## 6. Activar GLPI (opcional)

```env
WIKI_GLPI_ENABLED=true
GLPI_BASE_URL=https://glpi.ejemplo.org
GLPI_USER_TOKEN=
GLPI_APP_TOKEN=
WIKI_SEARCH_SOURCE=both
```

Ejemplos de pregunta:

```bash
./wiki-ask "ticket 10042 qué se hizo para resolverlo"
./wiki-ask --search-source glpi "error de autenticación LDAP"
```

## 7. Ajustar el asistente

Editá `persona/PERSONA.md` para el tono de tu equipo (N1/N2, runbooks, etc.).

## 8. Producción local con datos reales

```bash
mkdir -p persona.local
cp persona/PERSONA.md persona.local/
# personalizá persona.local/PERSONA.md
```

En `.env`:

```env
WIKI_PERSONA_FILE=persona.local/PERSONA.md
WIKI_CONSTRAINTS_FILE=persona.local/CONSTRAINTS.md
```

`persona.local/` no se sube a Git.
