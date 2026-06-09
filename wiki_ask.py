#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import os
import re
import sys
import unicodedata
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent

from dotenv import load_dotenv

from bookstack_assets import (
    extract_from_html,
    extract_from_markdown,
    format_asset_bundle,
    is_probably_html,
)
from bookstack_client import BookStackClient, BookStackResult
from glpi_client import GlpiClient, GlpiFetchOptions, GlpiTicketHit
from openai_compat_client import OpenAICompatClient
from persona_loader import load_persona_bundle

# Presets: --quality thorough fuerza estos valores; sin --quality, WIKI_QUALITY_MODE rellena solo claves vacías en env.
QUALITY_PRESETS: dict[str, dict[str, int]] = {
    "economy": {
        "WIKI_TOP_K": 2,
        "WIKI_PAGE_MAX_CHARS": 1200,
        "WIKI_CONTEXT_MAX_CHARS": 6500,
        "GROQ_MAX_TOKENS": 320,
        "LLM_MAX_TOKENS": 320,
        "WIKI_GALLERY_MAX_IMAGES": 5,
        "WIKI_PDF_MAX_FILES": 1,
        "WIKI_ASSETS_CONTEXT_BUDGET": 1500,
        "WIKI_PDF_CONTEXT_BUDGET": 3000,
    },
    "balanced": {
        "WIKI_TOP_K": 3,
        "WIKI_PAGE_MAX_CHARS": 1800,
        "WIKI_CONTEXT_MAX_CHARS": 9000,
        "GROQ_MAX_TOKENS": 420,
        "LLM_MAX_TOKENS": 420,
        "WIKI_GALLERY_MAX_IMAGES": 8,
        "WIKI_PDF_MAX_FILES": 2,
        "WIKI_ASSETS_CONTEXT_BUDGET": 2200,
        "WIKI_PDF_CONTEXT_BUDGET": 4000,
    },
    "thorough": {
        "WIKI_TOP_K": 6,
        "WIKI_PAGE_MAX_CHARS": 2800,
        "WIKI_CONTEXT_MAX_CHARS": 14000,
        "GROQ_MAX_TOKENS": 680,
        "LLM_MAX_TOKENS": 680,
        "WIKI_GALLERY_MAX_IMAGES": 12,
        "WIKI_PDF_MAX_FILES": 3,
        "WIKI_ASSETS_CONTEXT_BUDGET": 4000,
        "WIKI_PDF_CONTEXT_BUDGET": 7000,
        "WIKI_TEXT_CONTEXT_BUDGET": 9000,
    },
}


def _apply_quality_mode(mode: str | None, *, force: bool) -> None:
    m = (mode or "balanced").strip().lower()
    if m not in QUALITY_PRESETS:
        m = "balanced"
    for k, v in QUALITY_PRESETS[m].items():
        if force:
            os.environ[k] = str(v)
        else:
            cur = os.getenv(k)
            if cur is None or not str(cur).strip():
                os.environ[k] = str(v)


_IMG_URL_RE = re.compile(r"\.(png|jpe?g|webp|gif)(\?|#|$)", re.IGNORECASE)


def _image_urls_from_sources(urls: List[str], *, max_n: int) -> List[str]:
    seen = set()
    out: List[str] = []
    for u in urls:
        if not _IMG_URL_RE.search(u):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= max_n:
            break
    return out


def _is_url_under_bookstack(target: str, base: str) -> bool:
    """Solo URLs bajo la wiki BookStack (la visión nunca usa imágenes de GLPI u orígenes externos)."""
    b = (base or "").rstrip("/")
    t = (target or "").strip()
    if not t.startswith(("http://", "https://")) or not b:
        return False
    if not t.startswith(b):
        return False
    if len(t) == len(b):
        return True
    return t[len(b)] in "/?#"


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None or not str(v).strip():
        return default
    return str(v).strip()

def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    try:
        return int(str(v).strip()) if v is not None and str(v).strip() else default
    except Exception:
        return default


def _coalesce_env_int(names: Tuple[str, ...], default: int) -> int:
    for n in names:
        v = os.getenv(n)
        if v is None or not str(v).strip():
            continue
        try:
            return int(str(v).strip())
        except ValueError:
            continue
    return default


def _coalesce_env_float(names: Tuple[str, ...], default: float) -> float:
    for n in names:
        v = os.getenv(n)
        if v is None or not str(v).strip():
            continue
        try:
            return float(str(v).strip())
        except ValueError:
            continue
    return default


def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    try:
        return float(str(v).strip()) if v is not None and str(v).strip() else default
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None or not str(v).strip():
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on", "si", "sí")


def _glpi_ticket_public_url(base_url: str, ticket_id: int) -> str:
    """URL web del ticket (no es la API)."""
    b = (base_url or "").strip().rstrip("/")
    if not b:
        return ""
    if "/apirest.php" in b:
        b = b.split("/apirest.php", 1)[0].rstrip("/")
    return f"{b}/front/ticket.form.php?id={int(ticket_id)}"


def _glpi_ticket_user_type_label(t: Any) -> str:
    try:
        n = int(t)
    except (TypeError, ValueError):
        return str(t) if t else ""
    return {1: "Solicitante", 2: "Asignado", 3: "Observador"}.get(n, f"Rol código {n}")


def _format_glpi_followup(fu: Dict[str, Any], max_chars: int) -> str:
    content = _strip_html(str(fu.get("content") or ""))[:max_chars].strip()
    if not content:
        return ""
    priv = fu.get("is_private")
    is_priv = str(priv) in ("1", "true", "True") or priv is True
    tag = "**[Nota interna / privada]** " if is_priv else "**[Seguimiento público]** "
    who = str(fu.get("users_id") or fu.get("requesttypes_id") or "").strip()
    dt = str(fu.get("date") or fu.get("date_mod") or "").strip()
    head = " — ".join(x for x in (dt, who) if x) or "Seguimiento"
    return f"- {tag}({head}) {content}"


def _format_glpi_ticket_block(
    bundle: Dict[str, Any],
    *,
    public_url: str,
    max_desc: int,
    max_fu: int,
    max_fu_each: int,
) -> str:
    t = bundle.get("ticket") or {}
    if not isinstance(t, dict):
        return ""
    tid = t.get("id", "?")
    title = _strip_html(str(t.get("name") or "")) or f"Ticket {tid}"
    status = t.get("status")
    prio = t.get("priority")
    cat = t.get("itilcategories_id")
    ent = t.get("entities_id")
    utype = t.get("type")
    date_creation = str(t.get("date") or "").strip()
    date_mod = str(t.get("date_mod") or "").strip()
    desc = _strip_html(str(t.get("content") or ""))[:max_desc].strip()
    lines = [
        f"### Ticket #{tid}: {title}",
        f"**ID numérico:** {tid}",
        f"**URL:** {public_url}" if public_url else "",
        f"**Estado (GLPI):** {status}" if status is not None else "",
        f"**Prioridad:** {prio}" if prio is not None else "",
        f"**Tipo de ticket:** {utype}" if utype not in (None, "") else "",
        f"**Categoría:** {cat}" if cat not in (None, "", " ") else "",
        f"**Entidad:** {ent}" if ent not in (None, "", " ") else "",
        f"**Creado:** {date_creation}" if date_creation else "",
        f"**Última modificación:** {date_mod}" if date_mod else "",
    ]
    if desc:
        lines.append("#### Descripción inicial (recortada)")
        lines.append(desc)

    tus = bundle.get("ticket_users") or []
    if isinstance(tus, list) and tus:
        lines.append("#### Actores (usuarios vinculados al ticket)")
        for tu in tus:
            if not isinstance(tu, dict):
                continue
            role = _glpi_ticket_user_type_label(tu.get("type"))
            uid = tu.get("users_id")
            alt = tu.get("alternative_email") or ""
            extra = f" · email alt: {alt}" if alt else ""
            lines.append(f"- {role}: usuario/ref. **{uid}**{extra}")

    gts = bundle.get("group_tickets") or []
    if isinstance(gts, list) and gts:
        lines.append("#### Grupos vinculados")
        for gt in gts:
            if not isinstance(gt, dict):
                continue
            role = _glpi_ticket_user_type_label(gt.get("type"))
            gid = gt.get("groups_id")
            lines.append(f"- {role}: grupo **{gid}**")

    sols = bundle.get("solutions") or []
    if isinstance(sols, list) and sols:
        lines.append("#### Solución registrada")
        for sol in sols:
            if not isinstance(sol, dict):
                continue
            body = _strip_html(str(sol.get("content") or ""))[: max_fu_each * 2].strip()
            if not body:
                continue
            d = str(sol.get("date") or sol.get("date_creation") or "").strip()
            u = str(sol.get("users_id") or "").strip()
            lines.append(f"- ({d} · {u}) {body}")

    fus = bundle.get("followups") or []
    if isinstance(fus, list) and fus:
        lines.append("#### Seguimientos, respuestas y notas (públicas e internas visibles para tu perfil)")
        n = 0
        for fu in fus[:max_fu]:
            if not isinstance(fu, dict):
                continue
            s = _format_glpi_followup(fu, max_fu_each)
            if s:
                lines.append(s)
                n += 1
        if n == 0:
            lines.pop()

    tlinks = bundle.get("ticket_ticket") or []
    if isinstance(tlinks, list) and tlinks:
        lines.append("#### Relación con otros tickets (fusionados / vinculados / duplicados — según GLPI)")
        for lk in tlinks[:20]:
            if not isinstance(lk, dict):
                continue
            parts = [f"{k}={v}" for k, v in lk.items() if v not in (None, "", " ")][:12]
            if parts:
                lines.append("- " + "; ".join(parts))

    it = bundle.get("item_tickets") or []
    if isinstance(it, list) and it:
        lines.append("#### Vínculos con ítems (equipos, etc.)")
        for row in it[:15]:
            if not isinstance(row, dict):
                continue
            parts = [f"{k}={v}" for k, v in row.items() if v not in (None, "", " ")][:10]
            if parts:
                lines.append("- " + "; ".join(parts))

    tasks = bundle.get("tasks") or []
    if isinstance(tasks, list) and tasks:
        lines.append("#### Tareas")
        for tk in tasks[:15]:
            if not isinstance(tk, dict):
                continue
            c = _strip_html(str(tk.get("content") or ""))[:600].strip()
            if c:
                st = str(tk.get("state") or "").strip()
                lines.append(f"- ({st}) {c}")

    return "\n".join(x for x in lines if x).strip()


def _strip_accents_ascii(s: str) -> str:
    """Versión sin tildes para segunda pasada en GLPI (según cómo esté guardado el título)."""
    if not s:
        return ""
    return "".join(
        c
        for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _glpi_primary_text_segment(question: str) -> str:
    """
    Si el usuario junta dos pedidos («… blanqueo … o con que dice el 49303»),
    la búsqueda por texto usa solo el primer fragmento; los IDs se siguen leyendo del mensaje completo.
    """
    q = (question or "").strip()
    if not q:
        return ""
    q = re.sub(r'"\s*o\b', '" o ', q, flags=re.I)
    q = re.sub(r"'\s*o\b", "' o ", q, flags=re.I)
    parts = re.split(r"(?i)\s+o\s+", q, maxsplit=1)
    return parts[0].strip() if parts else q


def _normalize_glpi_user_query(question: str) -> str:
    """
    Quita comillas tipo «buscame "blanqueo de contraseña"» y muletillas al inicio
    para que el contains de GLPI matchee el título real.
    """
    q = (question or "").strip()
    if not q:
        return ""
    q = (
        q.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("«", '"')
        .replace("»", '"')
    )
    q = q.strip()
    q = re.sub(
        r"(?is)^(?:\s*(?:por\s+favor|pf|pls|please)\s*,?\s*)*"
        r"(?:busc[aá]me|búscame|buscame|busc[áa]\s+|encontr[aá]me|encontrar|"
        r"decime|decí(?:me)?|trae(?:me)?|traé(?:me)?|mostr[aá]me|ayud[aá]me\s+a\s+|"
        r"quiero\s+(?:que\s+)?(?:busques|encuentres|veas)\s+)?",
        "",
        q,
        count=1,
    ).strip()
    while len(q) >= 2 and q[0] == q[-1] and q[0] in "\"'":
        q = q[1:-1].strip()
    q = re.sub(r"\s+", " ", q).strip()
    return q[:1200]


_GLPI_TERM_STOP = frozenset(
    {
        "como",
        "para",
        "una",
        "uno",
        "del",
        "las",
        "los",
        "con",
        "por",
        "que",
        "hay",
        "algo",
        "sobre",
        "cuando",
        "donde",
        "quien",
        "quién",
        "este",
        "esta",
        "esto",
        "estos",
        "estas",
        "todo",
        "toda",
        "todos",
        "todas",
        "muy",
        "mas",
        "más",
        "menos",
        "solo",
        "sólo",
        "tan",
        "the",
        "and",
        "with",
    }
)


def _glpi_significant_terms(q: str) -> List[str]:
    """Palabras significativas para búsqueda AND en título (GLPI)."""
    raw = (q or "").lower()
    toks = re.findall(r"[a-záéíóúüñ0-9]{3,}", raw, flags=re.IGNORECASE)
    out: List[str] = []
    seen: set[str] = set()
    for t in toks:
        tl = t.lower()
        if tl in _GLPI_TERM_STOP or tl in seen:
            continue
        seen.add(tl)
        out.append(t)
    return out


def _glpi_text_search_variants(q: str) -> List[str]:
    """Varias cadenas a probar en contains (orden importa: primero la más fiel)."""
    q0 = (q or "").strip()
    if not q0:
        return []
    seen: set[str] = set()
    out: List[str] = []

    def push(s: str) -> None:
        s = (s or "").strip()
        if len(s) < 2:
            return
        key = s.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(s)

    push(q0)
    q_de = re.sub(r"(?i)\s+de\s+(?=[a-záéíóúüñ])", " ", q0)
    q_de = re.sub(r"\s+", " ", q_de).strip()
    if q_de != q0:
        push(q_de)
    qa = _strip_accents_ascii(q0)
    if qa.lower() != q0.lower():
        push(qa)
        qad = re.sub(r"(?i)\s+de\s+(?=[a-záéíóúüñ])", " ", qa)
        qad = re.sub(r"\s+", " ", qad).strip()
        if qad.lower() != qa.lower():
            push(qad)
    return out


def _glpi_distinct_emails(q: str) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for m in re.finditer(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", q or ""):
        em = m.group(0).strip()
        low = em.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(em)
    return out


def _glpi_fallback_queries(q: str) -> List[str]:
    """
    Subconsultas si el texto largo no matchea en GLPI (contains tiene límites / @).
    Extrae correos y variantes cortas tipo usuario@subdominio.
    """
    raw = (q or "").strip()
    if not raw:
        return []
    seen: set[str] = set()
    out: List[str] = []
    for m in re.finditer(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", raw):
        em = m.group(0).strip()
        if em.lower() in seen:
            continue
        seen.add(em.lower())
        out.append(em)
        if "@" in em:
            loc, dom = em.split("@", 1)
            parts = dom.split(".")
            if parts:
                short = f"{loc}@{parts[0]}"
                if short.lower() not in seen:
                    seen.add(short.lower())
                    out.append(short)
    return out


def _glpi_query_is_effectively_only_ticket_lookup(question: str, mentioned_ids: List[int]) -> bool:
    """
    True si la intención es solo localizar ticket(s) por número (no mezclar con tema libre).
    Así «ticket 49303» no dispara búsqueda de texto ruidosa; «blanqueo… ticket 49303» sí.
    """
    q = (question or "").strip()
    if q.isdigit():
        return True
    if not mentioned_ids:
        return False
    t = q
    for tid in mentioned_ids:
        t = re.sub(rf"(?<!\d){tid}(?!\d)", " ", t)
    t = re.sub(
        r"(?i)\b(busca|buscá|buscar|trae|traé|traer|dame|dá|mostr[áa]|mir[áa]|ver|"
        r"fijate|fijáte|el|la|los|las|un|una|del|de|lo|al|tu|mis?|"
        r"ticket|tiquet|tkt|incidente|caso|"
        r"por\s+favor|pf|pls|please|n[°º]|numero|número|#|help\s*desk|mesa)\b",
        " ",
        t,
    )
    t = re.sub(r"\s+", " ", t).strip()
    return len(t) < 4


def _glpi_ticket_ids_in_question(question: str) -> List[int]:
    """
    Detecta IDs de ticket mencionados en lenguaje natural (#49303, «ticket 49303», etc.).
    Evita tomar años 1900–2035 de 4 cifras como ID cuando aparecen sueltos.
    """
    q = question or ""
    out: List[int] = []
    seen: set[int] = set()

    def add(n: int) -> None:
        if n < 1 or n in seen:
            return
        seen.add(n)
        out.append(n)

    for m in re.finditer(
        r"(?i)(?:ticket|tiquet|tkt|incidente|caso|pedido|pedidos|req|requerimiento|"
        r"llamad[oa]|hd|help\s*desk|mesa)\s+(\d{3,8})\b",
        q,
    ):
        add(int(m.group(1)))
    for m in re.finditer(r"(?i)n[°º]\s*(\d{3,8})\b", q):
        add(int(m.group(1)))
    for m in re.finditer(r"(?i)(?:^|\s)#(\d{3,8})\b", q):
        add(int(m.group(1)))
    for m in re.finditer(r"(?i)\bid\s*(\d{3,8})\b", q):
        add(int(m.group(1)))
    for m in re.finditer(r"\b(\d{4,8})\b", q):
        digits = m.group(1)
        n = int(digits)
        if len(digits) == 4 and 1900 <= n <= 2035:
            continue
        add(n)
    return out


def _glpi_resolve_ticket_hit(
    client: GlpiClient, ticket_id: int, *, id_search_field: str
) -> List[GlpiTicketHit]:
    """GET directo o búsqueda por campo ID según permisos del GLPI."""
    td = client.ticket_exists(ticket_id)
    if td:
        tname = _strip_html(str(td.get("name") or "")) or f"Ticket #{ticket_id}"
        return [GlpiTicketHit(id=ticket_id, name=tname)]
    return client.search_ticket_by_num_id(ticket_id, id_field=id_search_field)


def _merge_glpi_hits(hits: List[GlpiTicketHit], *, max_n: int) -> List[GlpiTicketHit]:
    seen: set[int] = set()
    out: List[GlpiTicketHit] = []
    for h in hits:
        if h.id in seen:
            continue
        seen.add(h.id)
        out.append(h)
        if len(out) >= max_n:
            break
    return out


def _fetch_glpi_context(question: str, *, debug: bool) -> Tuple[str, List[str]]:
    """
    Devuelve (texto_markdown_para_contexto, urls_ticket).
    Falla en silencio salvo debug (stderr).
    """
    if not _env_bool("WIKI_GLPI_ENABLED", False):
        return "", []
    base = _env("GLPI_BASE_URL")
    if not base:
        return "", []
    app_token = _env("GLPI_APP_TOKEN") or ""
    user_token = _env("GLPI_USER_TOKEN") or ""
    login = _env("GLPI_LOGIN") or ""
    password = _env("GLPI_PASSWORD") or ""
    if not user_token and not (login and password):
        if debug:
            print("GLPI: habilitado pero faltan GLPI_USER_TOKEN o GLPI_LOGIN+GLPI_PASSWORD.", file=sys.stderr)
        return "", []
    # Cuántos tickets completos mandamos al LLM (1 = solo el mejor rankeado → más reciente si empata).
    bundle_max = max(1, _env_int("WIKI_GLPI_MAX_TICKETS", 1))
    # Cuántos candidatos mezclar antes de rankear (suele ser > bundle_max).
    rank_pool = min(
        100,
        max(bundle_max * 25, _env_int("WIKI_GLPI_RANK_POOL", 40)),
    )
    # Límite interno de la API de búsqueda (no confundir con bundle_max).
    search_limit = max(8, bundle_max * 4)
    title_f = str(_env("GLPI_TICKET_TITLE_SEARCH_FIELD") or "1").strip() or "1"
    content_f = str(_env("GLPI_TICKET_CONTENT_SEARCH_FIELD") or "21").strip() or "21"
    id_search_field = str(_env("GLPI_TICKET_ID_SEARCH_FIELD") or "2").strip() or "2"
    timeout = _env_int("GLPI_HTTP_TIMEOUT_S", 45)
    max_total = _env_int("WIKI_GLPI_CONTEXT_MAX_CHARS", 6000)
    max_desc = _env_int("WIKI_GLPI_TICKET_DESC_MAX_CHARS", 3200)
    max_fu = _env_int("WIKI_GLPI_MAX_FOLLOWUPS", 15)
    max_fu_each = _env_int("WIKI_GLPI_FOLLOWUP_MAX_CHARS", 1200)
    session_write = _env_bool("GLPI_API_SESSION_WRITE", True)
    follow_hi = max(0, max_fu - 1)
    opts = GlpiFetchOptions(
        followup_range=f"0-{follow_hi}",
        max_ticket_users=max(1, _env_int("WIKI_GLPI_MAX_TICKET_USERS", 40)),
        max_group_tickets=max(1, _env_int("WIKI_GLPI_MAX_GROUP_LINKS", 20)),
        max_solutions=max(1, _env_int("WIKI_GLPI_MAX_SOLUTIONS", 5)),
        max_ticket_links=max(1, _env_int("WIKI_GLPI_MAX_TICKET_LINKS", 15)),
        max_tasks=max(1, _env_int("WIKI_GLPI_MAX_TASKS", 20)),
    )

    chunks: List[str] = []
    sources: List[str] = []
    try:
        client = GlpiClient(
            base,
            app_token=app_token,
            user_token=user_token,
            login=login,
            password=password,
            timeout_s=timeout,
            session_write_on_enter=session_write,
        )
        with client:
            merged: List[GlpiTicketHit] = []
            qstrip = (question or "").strip()
            primary_seg = _glpi_primary_text_segment(qstrip)
            glpi_q = _normalize_glpi_user_query(primary_seg) or primary_seg.strip() or qstrip
            # IDs explícitos en la pregunta (solo dígitos o «ticket 49303»): no mezclar con búsqueda libre.
            id_only_hits: List[GlpiTicketHit] = []
            skip_text_search = False
            mentioned_ids: List[int] = []
            if qstrip.isdigit():
                mentioned_ids.append(int(qstrip))
            for tid in _glpi_ticket_ids_in_question(qstrip):
                if tid not in mentioned_ids:
                    mentioned_ids.append(tid)
            mentioned_ids = mentioned_ids[: max(12, bundle_max)]
            for tid0 in mentioned_ids:
                id_only_hits.extend(_glpi_resolve_ticket_hit(client, tid0, id_search_field=id_search_field))
            id_only_hits = _merge_glpi_hits(id_only_hits, max_n=min(20, rank_pool))
            if id_only_hits and _glpi_query_is_effectively_only_ticket_lookup(qstrip, mentioned_ids):
                skip_text_search = True

            if skip_text_search:
                hits = _rank_glpi_hits(question, id_only_hits, rank_pool)
                hits = hits[:bundle_max]
            else:
                merged.extend(id_only_hits)
                text_hits: List[GlpiTicketHit] = []
                variants = _glpi_text_search_variants(glpi_q)
                if not variants and glpi_q:
                    variants = [glpi_q]
                for v in variants:
                    text_hits.extend(
                        client.search_ticket_ids(
                            v,
                            limit=search_limit,
                            title_field=title_f,
                            content_field=content_f,
                            id_sort_field=id_search_field,
                        )
                    )
                if not _merge_glpi_hits(text_hits, max_n=1):
                    sig = _glpi_significant_terms(glpi_q)
                    if len(sig) >= 2:
                        text_hits.extend(
                            client.search_ticket_title_all_terms(
                                sig[:6],
                                limit=search_limit,
                                title_field=title_f,
                                id_sort_field=id_search_field,
                            )
                        )
                    if not _merge_glpi_hits(text_hits, max_n=1) and sig:
                        longest = max(sig, key=len)
                        if len(longest) >= 5:
                            text_hits.extend(
                                client.search_ticket_ids(
                                    longest,
                                    limit=search_limit,
                                    title_field=title_f,
                                    content_field=content_f,
                                    id_sort_field=id_search_field,
                                )
                            )
                merged.extend(text_hits)

                if _env_bool("WIKI_GLPI_SEARCH_IN_FOLLOWUPS", True):
                    fu_ids = client.search_ticket_ids_in_followups(
                        glpi_q,
                        limit=search_limit,
                        content_field=str(_env("GLPI_ITILFOLLOWUP_CONTENT_FIELD") or "5").strip() or "5",
                        ticket_id_result_key=str(_env("GLPI_ITILFOLLOWUP_ITEMS_ID_KEY") or "7").strip() or "7",
                        itemtype_result_key=str(_env("GLPI_ITILFOLLOWUP_ITEMTYPE_KEY") or "").strip(),
                        itemtype_must_contain=str(_env("GLPI_ITILFOLLOWUP_ITEMTYPE_CONTAINS") or "Ticket"),
                    )
                    for tid in fu_ids:
                        merged.append(
                            GlpiTicketHit(id=tid, name=f"Ticket #{tid} (coincidencia en seguimiento)")
                        )

                hits = _merge_glpi_hits(merged, max_n=rank_pool)

            if not hits:
                emails = _glpi_distinct_emails(question)
                if len(emails) >= 2:
                    id_sets: List[set[int]] = []
                    for em in emails:
                        hs = client.search_ticket_ids(
                            em,
                            limit=45,
                            title_field=title_f,
                            content_field=content_f,
                            id_sort_field=id_search_field,
                        )
                        id_sets.append({h.id for h in hs})
                    inter = set.intersection(*id_sets) if id_sets else set()
                    if inter:
                        hits = []
                        for tid in sorted(inter, reverse=True)[:bundle_max]:
                            td = client.ticket_exists(tid)
                            tname = (
                                _strip_html(str(td.get("name") or "")) if td else ""
                            ) or f"Ticket #{tid}"
                            hits.append(GlpiTicketHit(id=tid, name=tname))
                        if debug:
                            print(
                                "GLPI: intersección por correos en la pregunta →",
                                [h.id for h in hits],
                                file=sys.stderr,
                            )
                    else:
                        emails = []

                if not hits and len(emails) == 1:
                    merged_fb = list(
                        client.search_ticket_ids(
                            emails[0],
                            limit=search_limit,
                            title_field=title_f,
                            content_field=content_f,
                            id_sort_field=id_search_field,
                        )
                    )
                    hits = _merge_glpi_hits(merged_fb, max_n=rank_pool)
                    if debug and hits:
                        print(
                            f"GLPI: un solo correo en la pregunta {emails[0]!r} → {[h.id for h in hits]}",
                            file=sys.stderr,
                        )

                if not hits:
                    for subq in _glpi_fallback_queries(question):
                        merged_fb = []
                        merged_fb.extend(
                            client.search_ticket_ids(
                                subq,
                                limit=search_limit,
                                title_field=title_f,
                                content_field=content_f,
                                id_sort_field=id_search_field,
                            )
                        )
                        if _env_bool("WIKI_GLPI_SEARCH_IN_FOLLOWUPS", True):
                            fu_ids = client.search_ticket_ids_in_followups(
                                subq,
                                limit=search_limit,
                                content_field=str(_env("GLPI_ITILFOLLOWUP_CONTENT_FIELD") or "5").strip() or "5",
                                ticket_id_result_key=str(_env("GLPI_ITILFOLLOWUP_ITEMS_ID_KEY") or "7").strip() or "7",
                                itemtype_result_key=str(_env("GLPI_ITILFOLLOWUP_ITEMTYPE_KEY") or "").strip(),
                                itemtype_must_contain=str(_env("GLPI_ITILFOLLOWUP_ITEMTYPE_CONTAINS") or "Ticket"),
                            )
                            for tid in fu_ids:
                                merged_fb.append(
                                    GlpiTicketHit(id=tid, name=f"Ticket #{tid} (seguimiento)")
                                )
                        hits = _merge_glpi_hits(merged_fb, max_n=rank_pool)
                        if hits:
                            if debug:
                                print(
                                    f"GLPI: subcadena / correo acortado {subq!r} → {[h.id for h in hits]}",
                                    file=sys.stderr,
                                )
                            break

            hits = _rank_glpi_hits(question, hits, rank_pool)
            hits = hits[:bundle_max]
            if debug and hits:
                print(
                    "GLPI: tickets elegidos (mejor coincidencia / más recientes primero):",
                    [(h.id, h.name) for h in hits],
                    file=sys.stderr,
                )

            for h in hits:
                if sum(len(c) for c in chunks) >= max_total:
                    break
                try:
                    bundle = client.get_ticket_bundle(h.id, opts=opts)
                except Exception as e:
                    if debug:
                        print(f"GLPI: error leyendo ticket {h.id}: {e}", file=sys.stderr)
                    continue
                url = _glpi_ticket_public_url(base, h.id)
                if url and url not in sources:
                    sources.append(url)
                block = _format_glpi_ticket_block(
                    bundle,
                    public_url=url,
                    max_desc=max_desc,
                    max_fu=max_fu,
                    max_fu_each=max_fu_each,
                )
                if block:
                    per = max(1500, _env_int("WIKI_GLPI_MAX_CHARS_PER_TICKET", 3200))
                    if len(block) > per:
                        block = block[: per - 40].rstrip() + "\n…(ticket recortado; subí WIKI_GLPI_MAX_CHARS_PER_TICKET)\n"
                    chunks.append(block)
    except Exception as e:
        if debug:
            print(f"GLPI: error general: {e}", file=sys.stderr)
        return "", []

    text = "\n\n---\n\n".join(chunks).strip()
    if len(text) > max_total:
        text = text[:max_total] + "\n…(contexto GLPI recortado)"
    return text, sources


def _normalize_search_source(raw: Optional[str]) -> str:
    s = (raw or "").strip().lower()
    if s in ("wiki", "glpi", "both"):
        return s
    env_s = (_env("WIKI_SEARCH_SOURCE") or "both").strip().lower()
    if env_s in ("wiki", "glpi", "both"):
        return env_s
    return "both"


def _strip_html(s: str) -> str:
    # Limpieza simple preservando "líneas" para extraer evidencia útil.
    s = re.sub(r"(?is)<script.*?>.*?</script>", " ", s)
    s = re.sub(r"(?is)<style.*?>.*?</style>", " ", s)
    s = re.sub(r"(?i)<br\\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li|h1|h2|h3|h4|pre|code)>", "\n", s)
    s = re.sub(r"(?i)<(p|div|li|h1|h2|h3|h4|pre|code)\\b[^>]*>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

# Muy común en preguntas; ampliá vía WIKI_SEARCH_STOPWORDS_FILE (una palabra por línea).
_KEYWORDS_STOP = frozenset(
    {
        "como",
        "para",
        "una",
        "uno",
        "del",
        "las",
        "los",
        "con",
        "por",
        "que",
        "powershell",
    }
)


def _keywords(question: str) -> List[str]:
    max_k = _env_int("WIKI_KEYWORD_MAX", 8)
    toks = re.findall(r"[a-zA-Z0-9_\\-]{3,}", question.lower())
    out: List[str] = []
    for t in toks:
        if t in _KEYWORDS_STOP:
            continue
        if t not in out:
            out.append(t)
    return out[: max(3, max_k)]


def _wiki_resolve_optional_path(raw: str) -> Optional[Path]:
    p = Path(raw.strip())
    if not str(p):
        return None
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    return p if p.is_file() else None


def _wiki_read_nonempty_lines_from_env(env_key: str) -> List[str]:
    path = _wiki_resolve_optional_path(_env(env_key) or "")
    if path is None:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: List[str] = []
    for line in text.splitlines():
        s = line.split("#", 1)[0].strip()
        if s:
            out.append(s)
    return out


def _wiki_variant_stopwords() -> frozenset[str]:
    extra = frozenset(w.lower() for w in _wiki_read_nonempty_lines_from_env("WIKI_SEARCH_STOPWORDS_FILE"))
    return _KEYWORDS_STOP | extra


def _wiki_bookstack_search_variants(question: str) -> List[str]:
    """
    Variantes genéricas para /api/search: pregunta literal, sin muletillas iniciales,
    tokens y n-gramas (sin listas temáticas en código). Frases extra: WIKI_SEARCH_VARIANTS_FILE.
    """
    q = (question or "").strip()
    if not q:
        return []
    seen: set[str] = set()
    out: List[str] = []

    def add(s: str) -> None:
        t = re.sub(r"\s+", " ", (s or "").strip())
        if len(t) < 2:
            return
        k = t.lower()
        if k in seen:
            return
        seen.add(k)
        out.append(t)

    add(q)
    custom_strip = (_env("WIKI_SEARCH_STRIP_PREFIX_REGEX") or "").strip()
    if custom_strip:
        try:
            stripped = re.sub(custom_strip, "", q, count=1, flags=re.I).strip()
            if stripped and stripped != q:
                add(stripped)
        except re.error:
            pass
    stripped = re.sub(
        r"(?is)^\s*¿?\s*"
        r"(?:c[oó]mo|qu[eé]|d[oó]nde|cu[aá]ndo|por\s+qu[eé]|podr[ií]a?s?|me\s+podes|me\s+podr[ií]s|"
        r"hay\s+forma|existe|busco|busc[oó]|dame|dec[ií]me)\s+",
        "",
        q,
    )
    stripped = re.sub(r"(?is)\s+(?:por\s+favor|pf|pls)\s*[?.!¿]*\s*$", "", stripped).strip()
    if stripped and stripped != q:
        add(stripped)

    kws = _keywords(q)
    if kws:
        add(" ".join(kws))
    for kw in kws:
        add(kw)

    stop = _wiki_variant_stopwords()
    stop_fold = frozenset(
        "".join(
            c for c in unicodedata.normalize("NFD", w.lower()) if unicodedata.category(c) != "Mn"
        )
        for w in stop
    )
    toks = re.findall(r"[a-záéíóúüñ0-9]{3,}", q.lower())
    sig: List[str] = []
    seen_t: set[str] = set()
    for t in toks:
        tf = "".join(
            c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn"
        )
        if tf in stop_fold or t in seen_t:
            continue
        seen_t.add(t)
        sig.append(t)
        if len(sig) >= 14:
            break
    for t in sig:
        add(t)
    for i in range(len(sig) - 1):
        add(f"{sig[i]} {sig[i + 1]}")
    if len(sig) >= 3:
        for i in range(len(sig) - 2):
            add(f"{sig[i]} {sig[i + 1]} {sig[i + 2]}")
            if i >= 5:
                break
    if sig:
        add(" ".join(sig[:8]))

    max_ch = max(24, _env_int("WIKI_SEARCH_PREFIX_MAX_CHARS", 72))
    if len(q) > max_ch + 15:
        add(q[:max_ch].strip())

    for line in _wiki_read_nonempty_lines_from_env("WIKI_SEARCH_VARIANTS_FILE"):
        add(line)

    return out


def _wiki_build_no_hits_message(
    question: str,
    *,
    tried_queries: List[str],
    want_glpi: bool,
    bookstack_base: str,
    glpi_empty_too: bool,
) -> str:
    parts: List[str] = [
        "No encontré documentación en la wiki que coincida de forma clara con lo que pediste.",
        "Probé automáticamente **varias búsquedas** derivadas de tu texto (frase, recortes y combinaciones de palabras) y no aparecieron páginas útiles.",
    ]
    if len(tried_queries) > 1:
        show = tried_queries[:6]
        tail = "…" if len(tried_queries) > 6 else ""
        parts.append(
            "**Algunas consultas que probé en la wiki:** "
            + ", ".join(f"«{t}»" for t in show)
            + tail
        )
    parts.append(
        "**Qué podés hacer:** reformular con otras palabras o conceptos, revisar el índice o libros de la wiki, "
        "o usar mesa de ayuda si está integrada. En `.env` podés definir **WIKI_SEARCH_VARIANTS_FILE** "
        "(frases extra, una por línea) o **WIKI_SEARCH_STOPWORDS_FILE** para afinar qué términos se ignoran al armar variantes."
    )
    b = (bookstack_base or "").strip().rstrip("/")
    if b:
        parts.append(f"**Wiki:** {b}/")
    if want_glpi and _env_bool("WIKI_GLPI_ENABLED", False):
        parts.append(
            "Si en la interfaz elegís **wiki + GLPI**, la misma pregunta también busca en tickets de mesa de ayuda."
        )
    if glpi_empty_too and want_glpi:
        parts.append("En esta ocasión **tampoco hubo coincidencias en GLPI** con esas búsquedas.")
    parts.append(
        "Si creés que debería existir una guía con otro nombre, decilo así lo tenemos en cuenta para indexar mejor."
    )
    return "\n\n".join(parts)


def _rank_glpi_hits(question: str, hits: List[GlpiTicketHit], max_n: int) -> List[GlpiTicketHit]:
    """
    Ordena candidatos por solapamiento léxico con la pregunta y conserva solo los N mejores
    (menos contexto, menos tokens LLM). No reemplaza búsqueda semántica: solo prioriza títulos útiles.
    """
    if not hits:
        return hits
    q = (question or "").strip()
    qlow = q.lower()
    kws = list(_keywords(question))
    for m in re.finditer(r"([\w.+-]+)@[\w.-]+\.[a-zA-Z]{2,}", q):
        loc = m.group(1).lower()
        if len(loc) >= 3 and loc not in kws:
            kws.append(loc)

    def score(h: GlpiTicketHit) -> float:
        name = (h.name or "").lower()
        s = 0.0
        for kw in kws:
            if kw and kw in name:
                s += 4.0
        for w in re.findall(r"[a-záéíóúüñ0-9]{4,}", qlow):
            if w in name:
                s += 1.2
        if qlow.isdigit() and qlow in name:
            s += 80.0
        for m in re.finditer(r"\b(\d{4,8})\b", qlow):
            try:
                if int(m.group(1)) == h.id:
                    s += 150.0
                    break
            except ValueError:
                pass
        return s

    # Misma calidad léxica → preferir ticket más reciente (ID / fecha típica en GLPI).
    ranked = sorted(
        hits,
        key=lambda h: -(score(h) + 0.09 * math.log1p(max(h.id, 0))),
    )
    return ranked[:max_n]


def _extract_relevant(text: str, question: str, max_lines: int = 80) -> str:
    """
    Reduce el contexto a líneas probablemente útiles:
    - líneas con cmdlets (Get-/Set-/New-/Add-/Remove-)
    - líneas que contengan keywords de la pregunta
    """
    kws = _keywords(question)
    lines = [ln.strip() for ln in text.splitlines()]
    keep: List[str] = []
    for ln in lines:
        if not ln:
            continue
        lnl = ln.lower()
        if re.search(r"\b(get|set|new|add|remove|enable|disable)-[a-z]+\b", ln):
            keep.append(ln)
            continue
        if any(kw in lnl for kw in kws):
            keep.append(ln)
            continue
    # de-dup preservando orden
    seen = set()
    uniq: List[str] = []
    for ln in keep:
        if ln in seen:
            continue
        seen.add(ln)
        uniq.append(ln)
        if len(uniq) >= max_lines:
            break
    return "\n".join(uniq).strip()


def _extract_evidence_lines(text: str, question: str, max_lines: int = 8) -> List[str]:
    """
    Extrae líneas "evidencia" más estrictas (para citas literales).
    Prioriza proxy/squid/haproxy/curl/http y keywords de la pregunta.
    """
    kws = _keywords(question)
    bias_words = [
        "proxy",
        "squid",
        "haproxy",
        "curl",
        "wget",
        "http",
        "https",
        "-x",
        "--proxy",
        "connect",
        "cache",
    ]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    scored: List[Tuple[int, str]] = []
    for ln in lines:
        lnl = ln.lower()
        score = 0
        score += sum(2 for kw in kws if kw in lnl)
        score += sum(3 for w in bias_words if w in lnl)
        if re.search(r"\b(curl|wget)\b", lnl):
            score += 4
        if re.search(r"\b(squid|haproxy)\b", lnl):
            score += 4
        if re.search(r"\bhttp[s]?://", lnl):
            score += 2
        if score > 0:
            scored.append((score, ln))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: List[str] = []
    seen = set()
    for _, ln in scored:
        if ln in seen:
            continue
        seen.add(ln)
        out.append(ln)
        if len(out) >= max_lines:
            break
    return out


def _parse_bookstack_iso_ts(iso_s: str) -> float:
    if not iso_s or not str(iso_s).strip():
        return 0.0
    s = str(iso_s).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return 0.0


def _wiki_url_demote_substrings_from_env() -> List[str]:
    raw = (_env("WIKI_PAGE_URL_DEMOTE_SUBSTRINGS") or "").strip()
    if not raw:
        return []
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def _wiki_url_demote_rank(url: str, needles: List[str]) -> int:
    if not needles:
        return 0
    u = (url or "").lower()
    return 1 if any(n in u for n in needles) else 0


def _wiki_sort_page_loads_by_recency(
    loads: List[Tuple[int, BookStackResult, str, str, str, float, str]],
    *,
    needles: List[str],
    mode: str,
) -> List[Tuple[int, BookStackResult, str, str, str, float, str]]:
    """
    loads: (orden_original, result, raw, kind, page_url, ts, updated_at_raw)
    mode: updated_at | search
    """
    m = (mode or "updated_at").strip().lower()
    if m in ("search", "none", "off", "false", "0"):
        return sorted(loads, key=lambda x: x[0])
    return sorted(
        loads,
        key=lambda x: (
            0 if x[5] > 0 else 1,
            -x[5],
            _wiki_url_demote_rank(x[4], needles),
            x[0],
        ),
    )


def build_context(
    client: BookStackClient,
    results: List[BookStackResult],
    question: str,
    max_chars_per_page: int = 1800,
    max_total_chars: int = 9000,
    *,
    relevant_max_lines: int = 55,
    evidence_per_page: int = 5,
    evidence_per_pdf: int = 3,
    evidence_per_text: int = 3,
    evidence_max_unique: int = 10,
    include_pdf: bool = True,
    pdf_max_pages: int = 8,
    pdf_max_chars: int = 2500,
    pdf_max_files: int = 2,
    pdf_context_budget: int = 4000,
    include_html_assets: bool = True,
    assets_max_links: int = 22,
    assets_max_images: int = 12,
    assets_context_budget: int = 2200,
    include_gallery: bool = True,
    gallery_max_images: int = 8,
    include_text_attachments: bool = True,
    text_attach_max_files: int = 2,
    text_attach_max_chars: int = 3500,
    text_context_budget: int = 5000,
) -> Tuple[str, List[str], List[str]]:
    chunks: List[str] = []
    sources: List[str] = []
    evidence: List[str] = []
    total = 0
    pdf_used = 0
    pdf_chars = 0
    text_att_used = 0
    text_att_chars = 0

    sort_mode = (_env("WIKI_BOOKSTACK_PAGE_SORT") or "updated_at").strip()
    demote_needles = _wiki_url_demote_substrings_from_env()
    page_loads: List[Tuple[int, BookStackResult, str, str, str, float, str]] = []
    for i, r in enumerate(results):
        if r.type != "page":
            continue
        try:
            raw, kind, url, updated_at = client.get_page_payload(r.id)
        except Exception:
            continue
        page_url = url or r.url
        ua = (updated_at or (r.updated_at or "")).strip()
        ts = _parse_bookstack_iso_ts(ua)
        page_loads.append((i, r, raw, kind, page_url, ts, ua))

    for _i, r, raw, kind, page_url, _ts, ua in _wiki_sort_page_loads_by_recency(
        page_loads, needles=demote_needles, mode=sort_mode
    ):

        text_full = _strip_html(raw) if kind != "markdown" else raw
        evidence.extend(
            _extract_evidence_lines(text_full, question=question, max_lines=evidence_per_page)
        )
        text_relevant = _extract_relevant(
            text_full, question=question, max_lines=relevant_max_lines
        )
        text = text_relevant or text_full
        if kind == "markdown":
            if len(text) > max_chars_per_page:
                text = text[:max_chars_per_page].rstrip() + "\n\n[...recortado...]"
        else:
            if len(text) > max_chars_per_page:
                text = text[:max_chars_per_page].rstrip() + "\n\n[...recortado...]"
        if page_url:
            sources.append(page_url)
        meta = f"\n_Última actualización (BookStack):_ `{ua}`\n" if ua else ""
        chunk = f"### {r.name}\nURL: {page_url}{meta}\n{text}\n"
        if total + len(chunk) > max_total_chars:
            remaining = max_total_chars - total
            if remaining <= 0:
                break
            chunk = chunk[:remaining].rstrip() + "\n\n[...contexto total recortado...]"
            chunks.append(chunk)
            break
        chunks.append(chunk)
        total += len(chunk)

        # Enlaces, imágenes embebidas y pistas draw.io (desde HTML o Markdown)
        if include_html_assets and assets_context_budget > 0:
            page_url_eff = page_url or client.base_url
            if kind == "markdown":
                bundle = extract_from_markdown(
                    raw, page_url_eff, max_links=assets_max_links, max_images=assets_max_images
                )
            elif kind in ("html", "export_html") or is_probably_html(raw):
                bundle = extract_from_html(
                    raw, page_url_eff, max_links=assets_max_links, max_images=assets_max_images
                )
            else:
                bundle = extract_from_markdown(
                    raw, page_url_eff, max_links=assets_max_links, max_images=assets_max_images
                )
            asset_txt = format_asset_bundle(bundle)
            if asset_txt:
                if len(asset_txt) > assets_context_budget:
                    asset_txt = asset_txt[: assets_context_budget].rstrip() + "\n[...assets recortados...]"
                achunk = f"#### Recursos en la página (enlaces / imágenes / diagramas)\n{asset_txt}\n"
                chunks.append(achunk)
                for ln in asset_txt.splitlines():
                    if ln.startswith("- http") or ln.startswith("- https"):
                        evidence.append(ln[2:].strip()[:500])

        # Galería de imágenes BookStack (API)
        if include_gallery and gallery_max_images > 0:
            try:
                gitems = client.list_gallery_images_for_page(r.id)
            except Exception:
                gitems = []
            lines: List[str] = []
            for gi in gitems[:gallery_max_images]:
                if not isinstance(gi, dict):
                    continue
                gu = str(gi.get("url") or "")
                gname = str(gi.get("name") or "")
                if gu:
                    lines.append(f"- {gu} | {gname}".rstrip(" |"))
                    sources.append(gu)
            if lines:
                gchunk = "#### Galería de imágenes (BookStack)\n" + "\n".join(lines) + "\n"
                chunks.append(gchunk)

        need_atts = include_pdf or include_text_attachments
        atts: List = []
        if need_atts:
            try:
                atts = client.list_attachments_for_page(r.id)
            except Exception:
                atts = []

        for att in atts:
            if att.get("external") is True:
                ext_link = str(att.get("link") or att.get("url") or att.get("path") or "").strip()
                name = str(att.get("name") or "adjunto externo")
                if ext_link:
                    chunks.append(f"#### Adjunto externo (link)\n- {name}: {ext_link}\n")
                    sources.append(ext_link)
                    evidence.append(f"{name}: {ext_link}"[:500])
                continue
            try:
                aid = int(att.get("id"))
            except Exception:
                continue
            ext = str(att.get("extension") or "").lower()
            aname = str(att.get("name") or "")

            # PDF
            if include_pdf and pdf_used < pdf_max_files and pdf_chars < pdf_context_budget:
                if ext == "pdf" or aname.lower().endswith(".pdf"):
                    ptxt, att_url = client.extract_pdf_text_from_attachment(
                        aid, max_pages=pdf_max_pages, max_chars=pdf_max_chars
                    )
                    if ptxt.strip():
                        room = pdf_context_budget - pdf_chars
                        if room > 0:
                            if len(ptxt) > room:
                                ptxt = ptxt[:room].rstrip() + "\n\n[...PDF recortado por presupuesto...]"
                            pdf_chunk = f"### Adjunto PDF: {aname or aid}\nPágina wiki: {page_url}\n\n{ptxt}\n"
                            chunks.append(pdf_chunk)
                            pdf_chars += len(pdf_chunk)
                            pdf_used += 1
                            evidence.extend(
                                _extract_evidence_lines(
                                    ptxt, question=question, max_lines=evidence_per_pdf
                                )
                            )
                            if att_url:
                                sources.append(att_url)
                    continue

            # Texto plano / markdown / csv / json / xml
            if (
                include_text_attachments
                and text_att_used < text_attach_max_files
                and text_att_chars < text_context_budget
            ):
                ttxt, att_url = client.extract_text_attachment(aid, max_chars=text_attach_max_chars)
                if ttxt.strip():
                    room = text_context_budget - text_att_chars
                    if room > 0:
                        body = ttxt if len(ttxt) <= room else ttxt[:room].rstrip() + "\n\n[...recortado...]"
                        tchunk = f"### Adjunto texto: {aname or aid}\nPágina wiki: {page_url}\n\n{body}\n"
                        chunks.append(tchunk)
                        text_att_chars += len(tchunk)
                        text_att_used += 1
                        evidence.extend(
                            _extract_evidence_lines(
                                body, question=question, max_lines=evidence_per_text
                            )
                        )
                        if att_url:
                            sources.append(att_url)

    ev_uniq: List[str] = []
    ev_seen = set()
    for ln in evidence:
        if ln in ev_seen:
            continue
        ev_seen.add(ln)
        ev_uniq.append(ln)
        if len(ev_uniq) >= evidence_max_unique:
            break

    return "\n\n".join(chunks).strip(), sources, ev_uniq


_FUENTES_BLOCK = "\n## Fuentes\n"


def _strip_fuentes_from_answer(text: str) -> str:
    i = text.find(_FUENTES_BLOCK)
    if i >= 0:
        return text[:i].rstrip()
    return text.rstrip()


def _sanitize_conversation_history(history: Optional[List[Dict[str, str]]]) -> List[Dict[str, str]]:
    if not history:
        return []
    max_n = max(0, min(24, _env_int("WIKI_CHAT_HISTORY_MAX_MESSAGES", 12)))
    lim_u = max(200, _env_int("WIKI_CHAT_HISTORY_USER_MAX_CHARS", 800))
    lim_a = max(200, _env_int("WIKI_CHAT_HISTORY_ASSISTANT_MAX_CHARS", 1400))
    tail = history[-max_n:] if max_n else []
    out: List[Dict[str, str]] = []
    for h in tail:
        if not isinstance(h, dict):
            continue
        role = h.get("role")
        content = h.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        body = content.strip()
        if role == "assistant":
            body = _strip_fuentes_from_answer(body)
        lim = lim_u if role == "user" else lim_a
        if len(body) > lim:
            body = body[: lim - 3].rstrip() + "..."
        if body:
            out.append({"role": str(role), "content": body})
    return out


def _history_prefix_for_vision(history_msgs: List[Dict[str, str]]) -> str:
    if not history_msgs:
        return ""
    parts: List[str] = []
    for h in history_msgs:
        label = "Usuario" if h["role"] == "user" else "Asistente"
        parts.append(f"{label}: {h['content']}")
    return (
        "Diálogo previo (solo coherencia de seguimiento; datos fácticos nuevos únicamente del CONTEXTO que sigue):\n"
        + "\n\n".join(parts)
        + "\n\n---\n\n"
    )


@dataclass
class AskParams:
    """Parámetros equivalentes a la CLI; None = tomar de .env / defaults."""

    quality: Optional[str] = None
    top_k: Optional[int] = None
    model: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    debug: bool = False
    no_pdf: bool = False
    no_assets: bool = False
    no_gallery: bool = False
    no_text_attach: bool = False
    no_vision: bool = False
    persona_file: Optional[str] = None
    constraints_file: Optional[str] = None
    # wiki | glpi | both (interfaz web); None = WIKI_SEARCH_SOURCE en .env o both.
    search_source: Optional[str] = None
    # Mensajes previos user/assistant (solo API web); recortados por env para ahorrar tokens.
    conversation_history: Optional[List[Dict[str, str]]] = None


@dataclass
class AskResult:
    ok: bool
    answer: str = ""
    sources: List[str] = field(default_factory=list)
    """URLs de imágenes detectadas en fuentes (PNG/JPEG/WebP/GIF); útil para UI web."""
    image_urls: List[str] = field(default_factory=list)
    search_hits: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    used_vision: bool = False
    model_used: str = ""


def run_ask(question: str, params: Optional[AskParams] = None) -> AskResult:
    """
    Ejecuta una consulta wiki y/o GLPI + LLM (API compatible con OpenAI: Groq, OpenAI, Ollama, etc.).
    El caller debe haber llamado load_dotenv() antes.
    """
    params = params or AskParams()
    q = (question or "").strip()
    if not q:
        return AskResult(ok=False, error="Pregunta vacía.")

    if params.quality:
        _apply_quality_mode(params.quality, force=True)
    else:
        _apply_quality_mode(_env("WIKI_QUALITY_MODE") or "balanced", force=False)

    llm_key = (_env("LLM_API_KEY") or _env("GROQ_API_KEY") or "").strip()
    llm_base_raw = (_env("LLM_BASE_URL") or _env("OPENAI_COMPAT_BASE_URL") or "").strip()
    llm_base = llm_base_raw.rstrip("/") if llm_base_raw else "https://api.groq.com/openai/v1"
    lowb = llm_base.lower()
    llm_local = "localhost" in lowb or "127.0.0.1" in lowb
    if not llm_key and not llm_local:
        return AskResult(
            ok=False,
            error="Falta LLM_API_KEY o GROQ_API_KEY (Bearer). Para Ollama en localhost podés dejar la clave vacía y usar LLM_BASE_URL=http://127.0.0.1:11434/v1",
        )

    src = _normalize_search_source(params.search_source)
    want_wiki = src in ("wiki", "both")
    want_glpi = src in ("glpi", "both")

    base_url = _env("BOOKSTACK_BASE_URL")
    token_id = _env("BOOKSTACK_TOKEN_ID")
    token_secret = _env("BOOKSTACK_TOKEN_SECRET")
    if want_wiki:
        missing_w = [k for k, v in [
            ("BOOKSTACK_BASE_URL", base_url),
            ("BOOKSTACK_TOKEN_ID", token_id),
            ("BOOKSTACK_TOKEN_SECRET", token_secret),
        ] if not v]
        if missing_w:
            return AskResult(
                ok=False,
                error="Búsqueda en wiki activada pero faltan: " + ", ".join(missing_w),
            )

    if want_glpi:
        if not _env_bool("WIKI_GLPI_ENABLED", False):
            return AskResult(
                ok=False,
                error="Búsqueda GLPI activada pero WIKI_GLPI_ENABLED no está en true en .env.",
            )
        if not (_env("GLPI_BASE_URL") or "").strip():
            return AskResult(
                ok=False,
                error="Búsqueda GLPI activada: falta GLPI_BASE_URL en .env.",
            )
        ut = (_env("GLPI_USER_TOKEN") or "").strip()
        lg = (_env("GLPI_LOGIN") or "").strip()
        pw = _env("GLPI_PASSWORD") or ""
        if not ut and not (lg and pw):
            return AskResult(
                ok=False,
                error="Búsqueda GLPI activada: indicá GLPI_USER_TOKEN o GLPI_LOGIN + GLPI_PASSWORD en .env.",
            )

    top_k = params.top_k if params.top_k is not None else _env_int("WIKI_TOP_K", 3)
    wiki_context_cap = _env_int("WIKI_CONTEXT_MAX_CHARS", 9000)
    model = (
        params.model
        if params.model is not None
        else (_env("LLM_MODEL") or _env("GROQ_MODEL") or "llama-3.1-8b-instant")
    )
    max_tokens = (
        int(params.max_tokens)
        if params.max_tokens is not None
        else _coalesce_env_int(("LLM_MAX_TOKENS", "GROQ_MAX_TOKENS"), 420)
    )
    temperature = (
        float(params.temperature)
        if params.temperature is not None
        else _coalesce_env_float(("LLM_TEMPERATURE", "GROQ_TEMPERATURE"), 0.1)
    )

    bs: BookStackClient | None = None
    if want_wiki:
        bs = BookStackClient(
            base_url=base_url or "",
            token_id=token_id or "",
            token_secret=token_secret or "",
            timeout_s=_env_int("BOOKSTACK_HTTP_TIMEOUT_S", 25),
        )

    glpi_text, glpi_sources = ("", [])
    if want_glpi:
        glpi_text, glpi_sources = _fetch_glpi_context(q, debug=params.debug)
    glpi_used = bool(glpi_text.strip())
    if want_wiki and glpi_used:
        wiki_context_cap = min(
            wiki_context_cap,
            _env_int("WIKI_CONTEXT_MAX_CHARS_WITH_GLPI", 5200),
        )

    results: List[BookStackResult] = []
    wiki_tried_variants: List[str] = []
    if bs is not None:
        wiki_tried_variants = _wiki_bookstack_search_variants(q)
        max_wiki_v = max(1, _env_int("WIKI_SEARCH_MAX_VARIANTS", 16))
        results = bs.search_multi(wiki_tried_variants[:max_wiki_v], top_k=top_k)
        if params.debug:
            print("(debug) Wiki: variantes de búsqueda:", wiki_tried_variants[:max_wiki_v], file=sys.stderr)
            print(f"(debug) Wiki: {len(results)} resultado(s) únicos tras fusionar", file=sys.stderr)
    if not results and not glpi_used:
        tried_show = wiki_tried_variants if wiki_tried_variants else [q]
        if want_wiki and want_glpi:
            empty_msg = _wiki_build_no_hits_message(
                q,
                tried_queries=tried_show,
                want_glpi=True,
                bookstack_base=base_url or "",
                glpi_empty_too=True,
            )
        elif want_wiki:
            empty_msg = _wiki_build_no_hits_message(
                q,
                tried_queries=tried_show,
                want_glpi=want_glpi,
                bookstack_base=base_url or "",
                glpi_empty_too=False,
            )
        else:
            empty_msg = "No encontré resultados en GLPI para esa consulta."
        return AskResult(
            ok=True,
            answer=empty_msg,
            sources=[],
            image_urls=[],
            model_used=model or "",
        )

    search_hits: List[Dict[str, Any]] = [{"type": r.type, "id": r.id, "name": r.name, "url": r.url} for r in results]
    for gu in glpi_sources:
        tid_m = re.search(r"[?&]id=(\d+)", gu)
        tid_g: Any = int(tid_m.group(1)) if tid_m else gu
        search_hits.append({"type": "glpi_ticket", "id": tid_g, "name": "Ticket GLPI", "url": gu})
    if params.debug and results:
        print("Resultados (BookStack /api/search):", file=sys.stderr)
        for r in results:
            print(f"- {r.type} id={r.id} name={r.name} url={r.url}", file=sys.stderr)

    include_pdf = not params.no_pdf and _env_bool("WIKI_ATTACH_PDF", True)
    include_html_assets = not params.no_assets and _env_bool("WIKI_HTML_ASSETS", True)
    include_gallery = not params.no_gallery and _env_bool("WIKI_GALLERY", True)
    include_text_attachments = not params.no_text_attach and _env_bool("WIKI_ATTACH_TEXT", True)
    if results and bs is not None:
        context, sources, evidence_lines = build_context(
            bs,
            results,
            question=q,
            max_chars_per_page=_env_int("WIKI_PAGE_MAX_CHARS", 1800),
            max_total_chars=wiki_context_cap,
            relevant_max_lines=_env_int("WIKI_RELEVANT_MAX_LINES", 55),
            evidence_per_page=_env_int("WIKI_EVIDENCE_LINES_PAGE", 5),
            evidence_per_pdf=_env_int("WIKI_EVIDENCE_LINES_PDF", 3),
            evidence_per_text=_env_int("WIKI_EVIDENCE_LINES_TEXT", 3),
            evidence_max_unique=_env_int("WIKI_EVIDENCE_MAX_UNIQUE", 10),
            include_pdf=include_pdf,
            pdf_max_pages=_env_int("WIKI_PDF_MAX_PAGES", 8),
            pdf_max_chars=_env_int("WIKI_PDF_MAX_CHARS", 2500),
            pdf_max_files=_env_int("WIKI_PDF_MAX_FILES", 2),
            pdf_context_budget=_env_int("WIKI_PDF_CONTEXT_BUDGET", 4000),
            include_html_assets=include_html_assets,
            assets_max_links=_env_int("WIKI_ASSETS_MAX_LINKS", 22),
            assets_max_images=_env_int("WIKI_ASSETS_MAX_IMAGES", 12),
            assets_context_budget=_env_int("WIKI_ASSETS_CONTEXT_BUDGET", 2200),
            include_gallery=include_gallery,
            gallery_max_images=_env_int("WIKI_GALLERY_MAX_IMAGES", 8),
            include_text_attachments=include_text_attachments,
            text_attach_max_files=_env_int("WIKI_TEXT_ATTACH_MAX_FILES", 2),
            text_attach_max_chars=_env_int("WIKI_TEXT_ATTACH_MAX_CHARS", 3500),
            text_context_budget=_env_int("WIKI_TEXT_CONTEXT_BUDGET", 5000),
        )
    else:
        context, sources, evidence_lines = "", [], []

    if glpi_used:
        sep = "\n\n---\n\n## GLPI — tickets coincidentes (solo lectura)\n\n"
        context = (context.strip() + sep + glpi_text) if context.strip() else ("## GLPI — tickets coincidentes (solo lectura)\n\n" + glpi_text)
        for gu in glpi_sources:
            if gu not in sources:
                sources.append(gu)
        extra_ev = _extract_evidence_lines(glpi_text, q, max_lines=_env_int("WIKI_EVIDENCE_LINES_PAGE", 5))
        if extra_ev:
            if evidence_lines:
                merged = list(evidence_lines)
                for ln in extra_ev:
                    if ln not in merged:
                        merged.append(ln)
                evidence_lines = merged[: _env_int("WIKI_EVIDENCE_MAX_UNIQUE", 10)]
            else:
                evidence_lines = extra_ev

    if not context or not str(context).strip():
        return AskResult(
            ok=False,
            error="Encontré resultados en búsqueda, pero no pude armar contexto (wiki/GLPI).",
            search_hits=search_hits,
        )

    vision_allowed = not params.no_vision and _env_bool("WIKI_VISION_ENABLED", True)
    vision_model = _env("LLM_VISION_MODEL") or _env("GROQ_VISION_MODEL") or "meta-llama/llama-4-scout-17b-16e-instruct"
    vision_max = min(5, _env_int("WIKI_VISION_MAX_IMAGES", 3))
    kws = _keywords(q)
    kws_text = ", ".join(kws) if kws else "(sin keywords)"

    persona_path = params.persona_file or _env("WIKI_PERSONA_FILE")
    constraints_path = params.constraints_file or _env("WIKI_CONSTRAINTS_FILE")
    mood = _env("WIKI_AI_MOOD")
    persona_body, constraints_body = load_persona_bundle(
        persona_path=persona_path,
        constraints_path=constraints_path,
        mood_line=mood,
    )
    if not persona_body.strip():
        persona_body = (
            "Sos un asistente técnico. Respondé en español, con enfoque operativo para soporte N2.\n"
            "Usá solo el CONTEXTO (documentación BookStack y, si aparece, tickets GLPI). "
            "Si no alcanza: indicá que no hay suficiente información en esas fuentes."
        )
    system_parts: List[str] = ["# Persona e instrucciones (archivo)\n", persona_body]
    if constraints_body and constraints_body.strip():
        system_parts.append("\n---\n# Restricciones duras (archivo)\n")
        system_parts.append(constraints_body.strip())
    system_parts.append("\n---\n# Metadatos de esta consulta (generado)\n")
    system_parts.append(f"Palabras clave detectadas en la pregunta: {kws_text}\n")
    if want_wiki and results:
        _bs_sort = (_env("WIKI_BOOKSTACK_PAGE_SORT") or "updated_at").strip().lower()
        if _bs_sort not in ("search", "none", "off", "false", "0"):
            system_parts.append(
                "**Wiki (BookStack):** en el CONTEXTO, las páginas suelen ir **de la más reciente a la más antigua** "
                "(según `updated_at` en el servidor). Cada bloque puede incluir la fecha de última actualización. "
                "Si hay contradicciones entre guías, **priorizá la más nueva** y no mezcles pasos de entornos distintos "
                "(p. ej. Linux vs Azure/AD) sin dejarlo explícito.\n"
            )
    if glpi_used:
        system_parts.append(
            "\n---\n# GLPI\n"
            "El contexto puede incluir tickets (solicitantes, asignados, observadores, grupos, seguimientos públicos, "
            "notas internas visibles para tu usuario, soluciones, vínculos entre tickets).\n"
            "**Búsqueda GLPI:** no es vectorial ni semántica; el servidor GLPI devuelve tickets que coinciden por texto "
            "(título, descripción, seguimientos) con el motor de búsqueda nativo. Ese texto se incluye acá tal cual; "
            "El LLM solo recibe este prompt (sin embeddings de GLPI).\n"
            "**Formato:** tratá de **resolver** el problema del usuario integrando lo útil de la wiki y de los tickets "
            "(pasos, causas, soluciones ya probadas); si hay tickets viejos y recientes sobre el mismo tema, "
            "priorizá lo más reciente pero podés mencionar enfoques históricos si aportan. "
            "Respuesta clara y accionable primero; citá **número de ticket** cuando corresponda. "
            "Al final el sistema agrega automáticamente la sección «## Fuentes» con las URLs de "
            "cada ticket usado: no la dupliques entera, pero sí podés referir **Ticket #ID** en el cuerpo. "
            "Si sintetizás varios tickets, indicá de cuál sale cada punto clave.\n"
            "No expongas datos personales innecesarios. Si la respuesta se apoya en GLPI, dejalo explícito.\n"
        )
    hist = _sanitize_conversation_history(params.conversation_history)
    if hist:
        system_parts.append(
            "\n---\n# Conversación previa (interfaz web)\n"
            "Pueden seguir turnos anteriores del usuario en el mismo hilo: mantené coherencia, "
            "pero no afirmes hechos nuevos que no aparezcan en el CONTEXTO (wiki o GLPI) de esta consulta.\n"
        )
    system = "".join(system_parts).strip()
    evidence_block = "\n".join(f"- `{ln}`" for ln in evidence_lines) if evidence_lines else "(vacío)"
    user = (
        f"Pregunta: {q}\n\n"
        f"EVIDENCIA_DISPONIBLE (líneas literales extraídas de wiki y/o GLPI):\n{evidence_block}\n\n"
        f"CONTEXTO (para razonar, no para citar literal si no está en EVIDENCIA_DISPONIBLE):\n{context}"
    )
    max_user_ch = _coalesce_env_int(("WIKI_LLM_USER_MESSAGE_MAX_CHARS", "WIKI_GROQ_USER_MESSAGE_MAX_CHARS"), 9000)
    if glpi_used:
        max_user_ch = min(
            max_user_ch,
            _env_int("WIKI_LLM_USER_MESSAGE_MAX_CHARS_WITH_GLPI", 7800),
        )
    if len(user) > max_user_ch:
        user = (
            user[: max(0, max_user_ch - 120)].rstrip()
            + "\n\n[...CONTEXTO recortado: subí WIKI_LLM_USER_MESSAGE_MAX_CHARS o bajá contexto en .env...]"
        )

    data_uris: List[str] = []
    bookstack_base = _env("BOOKSTACK_BASE_URL") or ""
    if vision_allowed and bs is not None and bookstack_base.strip():
        for u in _image_urls_from_sources(list(sources), max_n=vision_max):
            if not _is_url_under_bookstack(u, bookstack_base):
                continue
            try:
                data_uris.append(bs.download_image_data_uri(u, max_bytes=_env_int("WIKI_VISION_MAX_BYTES", 4_000_000)))
            except Exception:
                continue
        if params.debug and vision_allowed and not data_uris and _image_urls_from_sources(
            list(sources), max_n=99
        ):
            print(
                "(debug) Visión automática: no hubo imágenes de BookStack descargables; "
                "(imágenes de GLPI u otros orígenes no se usan para visión).",
                file=sys.stderr,
            )

    _llm_def_timeout = 90 if data_uris else 45
    llm_timeout = _coalesce_env_int(("LLM_HTTP_TIMEOUT_S", "GROQ_HTTP_TIMEOUT_S"), _llm_def_timeout)
    llm = OpenAICompatClient(base_url=llm_base, api_key=llm_key, timeout_s=llm_timeout)

    if data_uris:
        sys_vision = system + (
            "\n\nTe adjuntan capturas de la wiki. Describí elementos de UI relevantes (menús, botones, textos) "
            "y relacionálos con la pregunta. No inventes pasos que no se vean o no estén en el CONTEXTO textual."
        )
        user_vision = _history_prefix_for_vision(hist) + user
        content: List[dict] = [{"type": "text", "text": user_vision}]
        for du in data_uris:
            content.append({"type": "image_url", "image_url": {"url": du}})
        vm = vision_model or "meta-llama/llama-4-scout-17b-16e-instruct"
        answer = llm.chat_completions(
            model=vm,
            messages=[
                {"role": "system", "content": sys_vision},
                {"role": "user", "content": content},
            ],
            temperature=float(temperature),
            max_tokens=int(max_tokens),
        )
        used_vision = True
        model_used = vm
    else:
        tm = model or "llama-3.1-8b-instant"
        llm_messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
        llm_messages.extend(hist)
        llm_messages.append({"role": "user", "content": user})
        answer = llm.chat(
            model=tm,
            messages=llm_messages,
            temperature=float(temperature),
            max_tokens=int(max_tokens),
        )
        used_vision = False
        model_used = tm

    if not answer:
        src = sorted(set(sources))
        return AskResult(
            ok=False,
            error="El LLM no devolvió respuesta (revisá LLM_BASE_URL, modelo y cuota).",
            search_hits=search_hits,
            sources=src,
            image_urls=_image_urls_from_sources(list(src), max_n=24),
        )

    full_answer = answer
    src = sorted(set(sources))
    if src:
        full_answer += "\n\n## Fuentes\n" + "\n".join(f"- {u}" for u in src)

    gallery_max = min(24, _env_int("WIKI_WEB_GALLERY_MAX", 16))
    img_urls = _image_urls_from_sources(list(src), max_n=gallery_max)

    return AskResult(
        ok=True,
        answer=full_answer,
        sources=src,
        image_urls=img_urls,
        search_hits=search_hits,
        used_vision=used_vision,
        model_used=model_used,
    )


def main() -> int:
    dp = os.getenv("WIKI_DOTENV_PATH")
    if dp and str(dp).strip():
        load_dotenv(str(dp).strip())
    else:
        load_dotenv()

    p = argparse.ArgumentParser(
        prog="wiki_ask",
        description=(
            "Consulta tu wiki (BookStack) y/o GLPI y obtiene una respuesta vía LLM (OpenAI-compatible). "
            "Uso normal: solo escribí la pregunta entre comillas; no hace falta ningún flag."
        ),
        epilog=(
            "Opciones --xxx: ajustes puntuales para quien ya conoce el proyecto "
            "(ver README → «Opcional: línea de comandos»). "
            "Personalización habitual: editá .env y persona/PERSONA.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("question", nargs="+", help="Tu pregunta en lenguaje natural.")
    p.add_argument(
        "--quality",
        choices=["economy", "balanced", "thorough"],
        default=None,
        help="economy=menos tokens; thorough=más contexto (más TPM). Sin flag: WIKI_QUALITY_MODE en .env.",
    )
    p.add_argument("--top-k", type=int, default=None, help="Resultados de búsqueda BookStack (default: .env / preset).")
    p.add_argument("--model", default=None, help="Modelo LLM (default: LLM_MODEL o GROQ_MODEL en .env).")
    p.add_argument("--max-tokens", type=int, default=None, help="Máx. tokens de respuesta (LLM_MAX_TOKENS / GROQ_MAX_TOKENS).")
    p.add_argument("--temperature", type=float, default=None, help="Default: LLM_TEMPERATURE o GROQ_TEMPERATURE en .env.")
    p.add_argument(
        "--debug",
        action="store_true",
        help="Muestra resultados crudos de búsqueda (IDs/URLs).",
    )
    p.add_argument(
        "--no-pdf",
        action="store_true",
        help="No extraer texto de adjuntos PDF (ahorra CPU local y acota contexto).",
    )
    p.add_argument(
        "--no-assets",
        action="store_true",
        help="No incluir enlaces/imágenes embebidas/draw.io del HTML/Markdown.",
    )
    p.add_argument(
        "--no-gallery",
        action="store_true",
        help="No listar imágenes de la galería BookStack de cada página.",
    )
    p.add_argument(
        "--no-text-attach",
        action="store_true",
        help="No incluir adjuntos de texto (.txt/.md/.csv/.json/etc.).",
    )
    p.add_argument(
        "--persona-file",
        default=None,
        help="Ruta a PERSONA.md (sobreescribe WIKI_PERSONA_FILE).",
    )
    p.add_argument(
        "--constraints-file",
        default=None,
        help="Ruta a CONSTRAINTS.md (sobreescribe WIKI_CONSTRAINTS_FILE).",
    )
    p.add_argument(
        "--no-vision",
        action="store_true",
        help="Nunca usar modelo visión ni descargar imágenes (solo texto; ahorra RPD Scout y requests).",
    )
    p.add_argument(
        "--search-source",
        choices=["wiki", "glpi", "both"],
        default=None,
        help="wiki | glpi | both (default: WIKI_SEARCH_SOURCE en .env o both).",
    )
    args = p.parse_args()

    question = " ".join(args.question).strip()
    if not question:
        print("Pregunta vacía.", file=sys.stderr)
        return 2

    params = AskParams(
        quality=args.quality,
        top_k=args.top_k,
        model=args.model,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        debug=args.debug,
        no_pdf=args.no_pdf,
        no_assets=args.no_assets,
        no_gallery=args.no_gallery,
        no_text_attach=args.no_text_attach,
        no_vision=args.no_vision,
        persona_file=args.persona_file,
        constraints_file=args.constraints_file,
        search_source=args.search_source,
    )
    res = run_ask(question, params)
    if not res.ok:
        err = res.error or "Error"
        print(err, file=sys.stderr)
        if err.startswith("Faltan variables de entorno") or err.startswith("Falta LLM_API_KEY"):
            print(
                "Copiá `.env.example` a `.env` y completalo, o exportá variables en la shell.",
                file=sys.stderr,
            )
            return 2
        if "Pregunta vacía" in err:
            return 2
        return 1
    print(res.answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

