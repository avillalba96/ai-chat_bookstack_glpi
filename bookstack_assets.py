"""
Extrae de contenido BookStack (HTML o Markdown) enlaces, imágenes y pistas de diagramas (draw.io / mxGraph).
No descarga binarios: solo URLs y texto embebido truncado para el contexto del LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List
from urllib.parse import urljoin, urlparse


@dataclass
class PageAssetBundle:
    links: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)  # "url | alt" o solo url
    drawio_excerpts: List[str] = field(default_factory=list)


def resolve_url(href: str, base_url: str) -> str:
    href = (href or "").strip()
    if not href or href.startswith("#") or href.lower().startswith("javascript:"):
        return ""
    if href.startswith("mailto:") or href.startswith("tel:"):
        return href
    try:
        return urljoin(base_url.rstrip("/") + "/", href)
    except Exception:
        return href


def _dedup_keep_order(items: List[str], max_n: int) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        x = x.strip()
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
        if len(out) >= max_n:
            break
    return out


def extract_from_html(html: str, page_url: str, *, max_links: int = 40, max_images: int = 25) -> PageAssetBundle:
    if not html or not html.strip():
        return PageAssetBundle()
    base = page_url or ""

    links: List[str] = []
    for m in re.finditer(r'href\s*=\s*"([^"]+)"', html, flags=re.I):
        u = resolve_url(m.group(1), base)
        if u:
            links.append(u)
    for m in re.finditer(r"href\s*=\s*'([^']+)'", html, flags=re.I):
        u = resolve_url(m.group(1), base)
        if u:
            links.append(u)

    images: List[str] = []
    for m in re.finditer(r"<img\b[^>]*>", html, flags=re.I):
        tag = m.group(0)
        sm = re.search(r'src\s*=\s*"([^"]+)"', tag, flags=re.I)
        if not sm:
            sm = re.search(r"src\s*=\s*'([^']+)'", tag, flags=re.I)
        if not sm:
            continue
        src = resolve_url(sm.group(1), base)
        if not src:
            continue
        am = re.search(r'alt\s*=\s*"([^"]*)"', tag, flags=re.I) or re.search(
            r"alt\s*=\s*'([^']*)'", tag, flags=re.I
        )
        alt = (am.group(1).strip() if am else "")[:120]
        images.append(f"{src} | {alt}" if alt else src)

    drawio_bits: List[str] = []
    # draw.io a veces deja XML embebido o en data-* / textarea
    for needle in ("mxGraphModel", "mxfile", "diagram", "draw.io", "drawio"):
        if needle.lower() in html.lower():
            idx = html.lower().find(needle.lower())
            if idx >= 0:
                snippet = html[max(0, idx - 200) : idx + 1800]
                drawio_bits.append(snippet)
            break

    return PageAssetBundle(
        links=_dedup_keep_order(links, max_links),
        images=_dedup_keep_order(images, max_images),
        drawio_excerpts=drawio_bits[:2],
    )


def extract_from_markdown(md: str, page_url: str, *, max_links: int = 40, max_images: int = 25) -> PageAssetBundle:
    if not md or not md.strip():
        return PageAssetBundle()
    base = page_url or ""
    links: List[str] = []
    images: List[str] = []

    for m in re.finditer(r"\[([^\]]*)\]\(([^)]+)\)", md):
        u = resolve_url(m.group(2).strip(), base)
        if u:
            links.append(u)

    for m in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", md):
        alt = (m.group(1) or "").strip()[:120]
        u = resolve_url(m.group(2).strip(), base)
        if u:
            images.append(f"{u} | {alt}" if alt else u)

    return PageAssetBundle(
        links=_dedup_keep_order(links, max_links),
        images=_dedup_keep_order(images, max_images),
        drawio_excerpts=[],
    )


def format_asset_bundle(bundle: PageAssetBundle, *, max_drawio_chars: int = 2000) -> str:
    parts: List[str] = []
    if bundle.links:
        parts.append("Enlaces detectados:")
        for u in bundle.links:
            parts.append(f"- {u}")
    if bundle.images:
        parts.append("Imágenes / diagramas (URL y texto alt si hay):")
        for im in bundle.images:
            parts.append(f"- {im}")
    if bundle.drawio_excerpts:
        parts.append("Fragmento embebido relacionado a diagrama (draw.io / mxGraph, truncado):")
        raw = "\n---\n".join(bundle.drawio_excerpts)
        if len(raw) > max_drawio_chars:
            raw = raw[:max_drawio_chars].rstrip() + "\n[...truncado...]"
        parts.append(raw)
    return "\n".join(parts).strip()


def is_probably_html(s: str) -> bool:
    t = s.lstrip()
    return t[:500].lower().find("<html") >= 0 or t[:800].lower().find("<p") >= 0 or t[:800].find("<div") >= 0
