# Seguridad

## Secretos

- **No subas** archivos `.env`, claves de API (LLM) ni tokens de BookStack al repositorio.
- Si un secreto se expuso (chat, ticket, log), **rotálo** de inmediato en la consola de tu proveedor LLM y en BookStack (revocar token / crear uno nuevo).

## Despliegue de la interfaz web

- Por defecto `WIKI_WEB_HOST=0.0.0.0` expone el chat en la red local. En producción usá **reverse proxy + TLS** y restringí el acceso (VPN, firewall, autenticación delante).
- El historial de conversaciones en el navegador vive en **localStorage** del cliente; no es un almacén centralizado seguro.
- El proxy de assets hacia BookStack usa el token del servidor: no expongas la UI a Internet sin controles.

## Dependencias

Instalá desde `requirements.txt` en un entorno virtual y mantené el entorno actualizado según tu política.

## Reporte de vulnerabilidades

Si encontrás un problema de seguridad en **este repositorio**, abrí un issue **privado** o contactá al mantenedor por el canal que indiques en el README. No publiques detalles explotables en issues públicos hasta tener un plan de mitigación.
