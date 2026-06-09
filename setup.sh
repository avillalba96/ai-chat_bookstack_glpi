#!/usr/bin/env bash
# Configuración inicial para quien clona el repo (una sola vez).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Necesitás Python 3 instalado." >&2
  exit 1
fi

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo ""
  echo "Listo. Editá el archivo .env y completá al menos:"
  echo "  BOOKSTACK_BASE_URL, BOOKSTACK_TOKEN_ID, BOOKSTACK_TOKEN_SECRET, GROQ_API_KEY"
  echo ""
else
  echo "Ya existe .env — no lo sobrescribí."
fi

echo "Para usar:"
echo "  source .venv/bin/activate"
echo "  ./wiki-ask \"tu pregunta\""
