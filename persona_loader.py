"""
Carga la impronta de la IA desde Markdown en disco (persona + constraints opcionales).
Así cada equipo puede versionar o copiar `PERSONA.md` sin tocar código.
"""
from __future__ import annotations

import os
from pathlib import Path


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _read_if_exists(path: Path) -> str | None:
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return None


def load_persona_bundle(
    *,
    persona_path: str | None,
    constraints_path: str | None,
    mood_line: str | None,
) -> tuple[str, str | None]:
    """
    Devuelve (persona_text, constraints_text o None).
    """
    base = _project_dir()
    default_persona = base / "persona" / "PERSONA.md"
    default_constraints = base / "persona" / "CONSTRAINTS.md"

    p = Path(persona_path.strip()).expanduser() if persona_path and persona_path.strip() else default_persona
    if not p.is_absolute():
        p = (base / p).resolve()

    text = _read_if_exists(p)
    if not text:
        text = _read_if_exists(default_persona) or ""

    c_path = (
        Path(constraints_path.strip()).expanduser()
        if constraints_path and constraints_path.strip()
        else default_constraints
    )
    if not c_path.is_absolute():
        c_path = (base / c_path).resolve()
    constraints = _read_if_exists(c_path)

    if mood_line and mood_line.strip():
        text = (
            text
            + "\n\n## Tono / instrucción puntual (.env WIKI_AI_MOOD)\n"
            + mood_line.strip()
            + "\n"
        )

    return text, constraints
