#!/usr/bin/env python3
"""
Interfaz web local (estilo chat) para consultar la wiki vía wiki_ask.run_ask.
Por defecto escucha en 0.0.0.0:8081 (WIKI_WEB_HOST / WIKI_WEB_PORT en .env o wiki_web_config.ENV_DEFAULTS).
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urlparse

from dotenv import load_dotenv
from flask import Flask, Response, abort, jsonify, render_template, request

load_dotenv()
_dp = os.getenv("WIKI_DOTENV_PATH", "").strip()
if _dp:
    load_dotenv(_dp, override=True)

from bookstack_client import BookStackClient
from wiki_ask import AskParams, _env, _env_int, run_ask
from wiki_web_config import (
    CONFIG_ENV_KEYS_SET,
    SECRET_ENV_KEYS,
    dotenv_path,
    env_overlay,
    merge_dotenv_file,
    read_persona_files,
    runtime_env_with_defaults,
    seed_missing_env_defaults,
    snapshot_env_for_ui,
    write_persona_files,
)

seed_missing_env_defaults()

app = Flask(__name__)
# Evitar caché agresiva del navegador en CSS/JS durante el desarrollo
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

_MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_MD_PAREN_LINK_RE = re.compile(r"\]\(([^)]+)\)")
_SOURCE_LINE_RE = re.compile(r"^(\s*[-*]\s+)(https?://\S+)(\s*)$", re.MULTILINE)

# Query string para forzar recarga de estáticos (cambiar al desplegar UI nueva)
STATIC_ASSET_V = os.getenv("WIKI_STATIC_ASSET_V", str(int(time.time())))


@app.context_processor
def _inject_static_v() -> dict:
    return {"static_v": STATIC_ASSET_V}


@app.after_request
def _no_cache_static(response: Response) -> Response:
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


def _reload_dotenv() -> None:
    load_dotenv(dotenv_path(), override=True)
    p = os.getenv("WIKI_DOTENV_PATH", "").strip()
    if p:
        load_dotenv(p, override=True)
    seed_missing_env_defaults()


def _is_under_bookstack(target: str, base: str) -> bool:
    b = (base or "").rstrip("/")
    t = (target or "").strip()
    if not t.startswith(("http://", "https://")) or not b:
        return False
    if not t.startswith(b):
        return False
    if len(t) == len(b):
        return True
    return t[len(b)] in "/?#"


def _proxy_max_bytes() -> int:
    return max(1_000_000, _env_int("WIKI_PROXY_MAX_BYTES", 40_000_000))


def _proxy_timeout_s(bs: BookStackClient) -> int:
    return max(bs.timeout_s, _env_int("WIKI_PROXY_HTTP_TIMEOUT_S", 120))


def _disposition_inline_filename(target: str) -> str:
    try:
        path = urlparse(target).path
        name = unquote(path.rsplit("/", 1)[-1]) or "recurso"
    except Exception:
        name = "recurso"
    safe = "".join(c if c.isascii() and c not in '\\"\r\n' else "_" for c in name)[:180]
    return safe or "recurso"


def _rewrite_bare_line_urls(text: str, base: str) -> str:
    """Líneas que son únicamente una URL de BookStack (fuera de bloques ```)."""
    lines = text.split("\n")
    in_fence = False
    out: list[str] = []
    for line in lines:
        st = line.strip()
        if st.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        if st.startswith(("http://", "https://")) and _is_under_bookstack(st, base):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f"{indent}[{st}](/api/proxy?u={quote(st, safe='')})")
            continue
        out.append(line)
    return "\n".join(out)


def _rewrite_answer_for_web_ui(text: str, base: str) -> str:
    """
    Enlaces e imágenes hacia BookStack pasan por /api/proxy (mismo origen).
    Así el navegador no descarga directo desde la wiki con credenciales propias;
    el servidor no persiste el cuerpo en disco (solo memoria del request).
    """
    if not text or not base:
        return text

    def img_sub(m: re.Match[str]) -> str:
        alt, url = m.group(1), m.group(2).strip()
        if url.startswith("/api/"):
            return m.group(0)
        if _is_under_bookstack(url, base):
            return f"![{alt}](/api/proxy?u={quote(url, safe='')})"
        return m.group(0)

    text = _MD_IMG_RE.sub(img_sub, text)

    def link_sub(m: re.Match[str]) -> str:
        url = m.group(1).strip()
        if url.startswith("/api/") or url.startswith("#") or url.lower().startswith("mailto:"):
            return m.group(0)
        if _is_under_bookstack(url, base):
            return f"](/api/proxy?u={quote(url, safe='')})"
        return m.group(0)

    text = _MD_PAREN_LINK_RE.sub(link_sub, text)

    def source_sub(m: re.Match[str]) -> str:
        prefix, url, suffix = m.group(1), m.group(2), m.group(3)
        if _is_under_bookstack(url, base):
            return f"{prefix}[{url}](/api/proxy?u={quote(url, safe='')}){suffix}"
        return m.group(0)

    text = _SOURCE_LINE_RE.sub(source_sub, text)
    text = _rewrite_bare_line_urls(text, base)
    return text


def _bookstack_client() -> BookStackClient | None:
    base = _env("BOOKSTACK_BASE_URL")
    tid = _env("BOOKSTACK_TOKEN_ID")
    sec = _env("BOOKSTACK_TOKEN_SECRET")
    if not base or not tid or not sec:
        return None
    return BookStackClient(
        base_url=base.rstrip("/"),
        token_id=tid,
        token_secret=sec,
        timeout_s=_env_int("BOOKSTACK_HTTP_TIMEOUT_S", 25),
    )


def _build_ask_params(raw: Any) -> AskParams:
    if not isinstance(raw, dict):
        raw = {}
    q = raw.get("quality")
    quality = q if q in ("economy", "balanced", "thorough") else None

    def _oi(name: str) -> Optional[int]:
        v = raw.get(name)
        if v is None or v == "":
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def _of(name: str) -> Optional[float]:
        v = raw.get(name)
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    hist_raw = raw.get("history")
    if hist_raw is None:
        hist_raw = raw.get("conversation_history")
    conv_hist = _parse_conversation_history(hist_raw)

    ss = raw.get("search_source")
    search_source = ss if ss in ("wiki", "glpi", "both") else None

    return AskParams(
        quality=quality,
        top_k=_oi("top_k"),
        model=(str(m).strip() if (m := raw.get("model")) else None),
        max_tokens=_oi("max_tokens"),
        temperature=_of("temperature"),
        debug=bool(raw.get("debug")),
        no_pdf=bool(raw.get("no_pdf")),
        no_assets=bool(raw.get("no_assets")),
        no_gallery=bool(raw.get("no_gallery")),
        no_text_attach=bool(raw.get("no_text_attach")),
        no_vision=bool(raw.get("no_vision")),
        persona_file=(str(raw["persona_file"]).strip() if raw.get("persona_file") else None),
        constraints_file=(str(raw["constraints_file"]).strip() if raw.get("constraints_file") else None),
        search_source=search_source,
        conversation_history=conv_hist,
    )


def _parse_conversation_history(raw: Any) -> Optional[List[Dict[str, str]]]:
    if not isinstance(raw, list):
        return None
    out: List[Dict[str, str]] = []
    for item in raw[-24:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        c = content.strip()
        if not c:
            continue
        if len(c) > 32000:
            c = c[:32000]
        out.append({"role": role, "content": c})
    return out if out else None


def _sanitize_runtime_env(obj: Any) -> Dict[str, str]:
    if not isinstance(obj, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in obj.items():
        if k not in CONFIG_ENV_KEYS_SET:
            continue
        if v is None:
            continue
        out[str(k)] = str(v).strip() if isinstance(v, str) else str(v)
    return out


@app.get("/")
def index():
    _reload_dotenv()
    return render_template(
        "chat.html",
        ui_product=_env("WIKI_UI_PRODUCT_NAME") or "Wiki AI Agent",
        ui_tagline=_env("WIKI_UI_TAGLINE") or "Consultá tu wiki y tickets GLPI para resolver casos.",
    )


@app.get("/api/config")
def api_config_get():
    _reload_dotenv()
    env_snap = snapshot_env_for_ui()
    pf, cf, ptext, ctext = read_persona_files()
    from wiki_web_config import ENV_KEY_GROUPS

    return jsonify(
        {
            "dotenv_path": str(dotenv_path()),
            "env": env_snap,
            "env_groups": [{"label": lab, "keys": keys} for lab, keys in ENV_KEY_GROUPS],
            "secret_keys": list(SECRET_ENV_KEYS),
            "persona_path": pf,
            "constraints_path": cf,
            "persona_text": ptext,
            "constraints_text": ctext,
        }
    )


@app.post("/api/config")
def api_config_post():
    data = request.get_json(silent=True) or {}
    errors: list[str] = []

    if "env" in data and isinstance(data["env"], dict):
        updates = _sanitize_runtime_env(data["env"])
        if updates:
            try:
                merge_dotenv_file(dotenv_path(), updates)
            except OSError as e:
                errors.append(f".env: {e}")
        _reload_dotenv()

    if any(k in data for k in ("persona_text", "constraints_text", "persona_path", "constraints_path")):
        pf = str(data.get("persona_path") or os.getenv("WIKI_PERSONA_FILE") or "persona/PERSONA.md")
        cf = str(data.get("constraints_path") or os.getenv("WIKI_CONSTRAINTS_FILE") or "persona/CONSTRAINTS.md")
        pt = data.get("persona_text")
        ct = data.get("constraints_text")
        if isinstance(pt, str) and isinstance(ct, str):
            try:
                write_persona_files(pf, cf, pt, ct)
            except OSError as e:
                errors.append(f"persona: {e}")
            _reload_dotenv()

    return jsonify({"ok": not errors, "errors": errors})


@app.post("/api/ask")
def api_ask():
    data = request.get_json(silent=True) or {}
    q = (data.get("question") or data.get("q") or "").strip()
    if not q:
        return jsonify({"ok": False, "error": "Pregunta vacía."}), 400

    runtime = _sanitize_runtime_env(data.get("runtime_env") or data.get("env"))
    runtime = runtime_env_with_defaults(runtime)
    ask_raw: Dict[str, Any] = dict(data.get("ask_params") or {})
    if data.get("history") is not None:
        ask_raw["history"] = data.get("history")
    ask_params = _build_ask_params(ask_raw)

    with env_overlay(runtime):
        res = run_ask(q, ask_params)
    _reload_dotenv()
    base = _env("BOOKSTACK_BASE_URL") or ""
    out = asdict(res)
    if res.ok and res.answer:
        out["answer"] = _rewrite_answer_for_web_ui(res.answer, base)
    proxy_urls = []
    for u in res.image_urls:
        if _is_under_bookstack(u, base):
            proxy_urls.append(f"/api/proxy?u={quote(u, safe='')}")
    out["image_proxy_urls"] = proxy_urls
    return jsonify(out)


@app.get("/api/proxy")
@app.get("/api/image")
def proxy_bookstack():
    """PDF, imágenes y demás GET permitidos bajo BOOKSTACK_BASE_URL; respuesta en memoria, sin archivo en disco."""
    raw = (request.args.get("u") or request.args.get("url") or "").strip()
    target = unquote(raw)
    base = _env("BOOKSTACK_BASE_URL") or ""
    bs = _bookstack_client()
    if not bs or not target or not _is_under_bookstack(target, base):
        abort(403)
    timeout = _proxy_timeout_s(bs)
    try:
        r = bs.session.get(target, timeout=timeout)
        r.raise_for_status()
    except Exception:
        abort(502)
    body = r.content
    if len(body) > _proxy_max_bytes():
        abort(413)
    ct = (r.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip()
    resp = Response(body, mimetype=ct)
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["Content-Disposition"] = (
        f'inline; filename="{_disposition_inline_filename(target)}"'
    )
    return resp


def main() -> None:
    _reload_dotenv()
    host = (os.getenv("WIKI_WEB_HOST") or "").strip() or "0.0.0.0"
    port_s = (os.getenv("WIKI_WEB_PORT") or "").strip()
    try:
        port = int(port_s) if port_s else 8081
    except ValueError:
        port = 8081
    app.run(host=host, port=port, debug=os.getenv("FLASK_DEBUG") == "1")


if __name__ == "__main__":
    main()
