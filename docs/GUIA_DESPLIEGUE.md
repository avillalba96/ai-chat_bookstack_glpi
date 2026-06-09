# Guía de despliegue

## Desarrollo / uso interno

```bash
./setup.sh
source .venv/bin/activate
./wiki-web
```

Acceso local: http://127.0.0.1:8081

## Producción recomendada

1. **Servidor dedicado** o contenedor con Python 3.10+.
2. **Reverse proxy** (nginx, Caddy, HAProxy) con TLS.
3. **No exponer** `0.0.0.0:8081` directamente a Internet sin autenticación.
4. Variables sensibles solo en `.env` con permisos `600`.
5. Rotar tokens BookStack/GLPI si hubo filtración.

## Solo CLI (menor superficie)

Para equipos que no necesitan UI:

```bash
./wiki-ask "pregunta"
```

Sin Flask = sin proxy web ni localStorage.

## Persona institucional

```env
WIKI_PERSONA_FILE=persona.local/PERSONA.md
WIKI_CONSTRAINTS_FILE=persona.local/CONSTRAINTS.md
```

Copiá `persona/` → `persona.local/` y editá. La carpeta `persona.local/` está en `.gitignore`.

## Cuotas LLM

- Modo `economy` en `.env` para free tier.
- Visión (`LLM_VISION_MODEL`) consume más cuota; desactivar con `WIKI_VISION_ENABLED=false` si no hay capturas críticas.
