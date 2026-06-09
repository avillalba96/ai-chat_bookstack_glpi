from __future__ import annotations

from dataclasses import dataclass
import base64
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None  # type: ignore[misc, assignment]


@dataclass(frozen=True)
class BookStackResult:
    id: int
    name: str
    type: str
    url: str
    # Si la API de búsqueda lo devuelve (ISO 8601); si no, se rellena al cargar la página.
    updated_at: Optional[str] = None


class BookStackClient:
    def __init__(
        self,
        base_url: str,
        token_id: str,
        token_secret: str,
        timeout_s: int = 20,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Token {token_id}:{token_secret}",
                "Accept": "application/json",
            }
        )

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def search(self, query: str, top_k: int = 5) -> List[BookStackResult]:
        # BookStack: GET /api/search?query=... (paginado). Pedimos count alto y cortamos.
        params = {"query": query, "count": max(top_k, 12), "include": "titles"}
        r = self.session.get(self._url("/api/search"), params=params, timeout=self.timeout_s)
        r.raise_for_status()
        data = r.json()
        items = data.get("data", []) if isinstance(data, dict) else []

        out: List[BookStackResult] = []
        for it in items:
            try:
                ua = it.get("updated_at")
                if ua is None and isinstance(it.get("updated"), dict):
                    ua = it["updated"].get("timestamp") or it["updated"].get("updated_at")
                out.append(
                    BookStackResult(
                        id=int(it["id"]),
                        name=str(it.get("name") or it.get("title") or ""),
                        type=str(it.get("type") or ""),
                        url=str(it.get("url") or ""),
                        updated_at=str(ua).strip() if ua else None,
                    )
                )
            except Exception:
                continue
            if len(out) >= top_k:
                break
        return out

    def search_multi(self, queries: List[str], top_k: int = 5) -> List[BookStackResult]:
        """
        Varias consultas consecutivas, sin duplicar (mismo type+id).
        Orden: primera query con más peso (resultados que aparecen antes).
        """
        seen: set[Tuple[str, int]] = set()
        out: List[BookStackResult] = []
        fetch_n = max(top_k, 12)
        for raw in queries:
            if len(out) >= top_k:
                break
            q = (raw or "").strip()
            if len(q) < 2:
                continue
            for r in self.search(q, top_k=fetch_n):
                key = (r.type, r.id)
                if key in seen:
                    continue
                seen.add(key)
                out.append(r)
                if len(out) >= top_k:
                    break
        return out

    @staticmethod
    def _page_updated_at_from_api_dict(data: Dict[str, Any]) -> str:
        ua = data.get("updated_at")
        if ua is None and isinstance(data.get("updated"), dict):
            ua = data["updated"].get("timestamp") or data["updated"].get("updated_at")
        return str(ua).strip() if ua else ""

    def get_page_text_and_url(self, page_id: int) -> Tuple[str, str]:
        """
        Devuelve (texto, url).
        Intenta obtener HTML/markdown desde /api/pages/{id}. Si no está, cae a export/html.
        """
        r = self.session.get(self._url(f"/api/pages/{page_id}"), timeout=self.timeout_s)
        r.raise_for_status()
        data: Dict[str, Any] = r.json() if isinstance(r.json(), dict) else {}

        url = str(data.get("url") or "")
        # Campos observados en BookStack API (varía por versión/config):
        for key in ("markdown", "html", "text"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val, url

        # Fallback: export HTML (si está habilitado)
        r2 = self.session.get(self._url(f"/api/pages/{page_id}/export/html"), timeout=self.timeout_s)
        r2.raise_for_status()
        return r2.text, url

    def list_attachments_for_page(self, page_id: int) -> List[Dict[str, Any]]:
        """
        Lista adjuntos asociados a una página (archivos y links externos).
        """
        params: Dict[str, Any] = {"count": 200, "filter[uploaded_to]": page_id}
        r = self.session.get(self._url("/api/attachments"), params=params, timeout=self.timeout_s)
        r.raise_for_status()
        data = r.json()
        items = data.get("data", []) if isinstance(data, dict) else []
        out = [it for it in items if isinstance(it, dict)]
        if out:
            return out
        # Fallback: algunas versiones/config no filtran como esperamos; traemos un lote y filtramos.
        r2 = self.session.get(
            self._url("/api/attachments"), params={"count": 500, "sort": "-updated_at"}, timeout=self.timeout_s
        )
        r2.raise_for_status()
        data2 = r2.json()
        items2 = data2.get("data", []) if isinstance(data2, dict) else []
        matched: List[Dict[str, Any]] = []
        for it in items2:
            if not isinstance(it, dict):
                continue
            ut = it.get("uploaded_to")
            try:
                if int(ut) == int(page_id):
                    matched.append(it)
            except Exception:
                continue
            if len(matched) >= 100:
                break
        return matched

    def read_attachment(self, attachment_id: int) -> Dict[str, Any]:
        r = self.session.get(self._url(f"/api/attachments/{attachment_id}"), timeout=self.timeout_s)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {}

    def download_url_bytes(self, url: str) -> bytes:
        """Descarga binarios usando el mismo token (adjuntos en BookStack)."""
        r = self.session.get(url, timeout=self.timeout_s)
        r.raise_for_status()
        return r.content

    def download_image_data_uri(self, url: str, *, max_bytes: int = 4_000_000) -> str:
        """
        Descarga imagen y devuelve data URI para APIs multimodal (Groq vision).
        """
        r = self.session.get(url, timeout=self.timeout_s)
        r.raise_for_status()
        raw = r.content
        if len(raw) > max_bytes:
            raise ValueError("imagen demasiado grande")
        ct = r.headers.get("Content-Type", "image/png")
        ct_main = (ct or "image/png").split(";")[0].strip().lower()
        if ct_main not in ("image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"):
            if "png" in url.lower():
                ct_main = "image/png"
            elif "jpg" in url.lower() or "jpeg" in url.lower():
                ct_main = "image/jpeg"
            else:
                ct_main = "image/png"
        b64 = base64.standard_b64encode(raw).decode("ascii")
        return f"data:{ct_main};base64,{b64}"

    def extract_pdf_text_from_attachment(
        self,
        attachment_id: int,
        *,
        max_pages: int = 12,
        max_chars: int = 3500,
    ) -> Tuple[str, str]:
        """
        Devuelve (texto_extraído, url_adjunto_o_vacío).
        Si no es PDF o falla la extracción, devuelve ("", "").
        """
        if PdfReader is None:
            return "", ""
        meta = self.read_attachment(attachment_id)
        if meta.get("external") is True:
            return "", ""
        ext = str(meta.get("extension") or "").lower()
        name = str(meta.get("name") or "")
        if ext != "pdf" and not name.lower().endswith(".pdf"):
            return "", ""
        url = str(meta.get("url") or "")
        if not url:
            return "", ""
        raw = self.download_url_bytes(url)
        try:
            reader = PdfReader(BytesIO(raw))
            parts: List[str] = []
            n = min(len(reader.pages), max(1, max_pages))
            for i in range(n):
                p = reader.pages[i]
                t = p.extract_text() or ""
                if t.strip():
                    parts.append(t.strip())
            text = "\n\n".join(parts).strip()
            if len(text) > max_chars:
                text = text[:max_chars].rstrip() + "\n\n[...PDF recortado...]"
            return text, url
        except Exception:
            return "", url

    def get_page_payload(self, page_id: int) -> Tuple[str, str, str, str]:
        """
        Devuelve (contenido_principal, kind, url_página, updated_at_iso).
        kind es 'html', 'markdown' o 'export_html'.
        """
        r = self.session.get(self._url(f"/api/pages/{page_id}"), timeout=self.timeout_s)
        r.raise_for_status()
        data: Dict[str, Any] = r.json() if isinstance(r.json(), dict) else {}
        url = str(data.get("url") or "")
        updated_at = self._page_updated_at_from_api_dict(data)
        md = data.get("markdown")
        if isinstance(md, str) and md.strip():
            return md.strip(), "markdown", url, updated_at
        for key in ("html", "raw_html", "text"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip(), "html", url, updated_at
        r2 = self.session.get(self._url(f"/api/pages/{page_id}/export/html"), timeout=self.timeout_s)
        r2.raise_for_status()
        return r2.text.strip(), "export_html", url, updated_at

    def list_gallery_images_for_page(self, page_id: int) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"count": 200, "filter[uploaded_to]": page_id}
        r = self.session.get(self._url("/api/image-gallery"), params=params, timeout=self.timeout_s)
        r.raise_for_status()
        data = r.json()
        items = data.get("data", []) if isinstance(data, dict) else []
        out = [it for it in items if isinstance(it, dict)]
        if out:
            return out
        r2 = self.session.get(
            self._url("/api/image-gallery"), params={"count": 500, "sort": "-updated_at"}, timeout=self.timeout_s
        )
        r2.raise_for_status()
        data2 = r2.json()
        items2 = data2.get("data", []) if isinstance(data2, dict) else []
        matched: List[Dict[str, Any]] = []
        for it in items2:
            if not isinstance(it, dict):
                continue
            ut = it.get("uploaded_to")
            try:
                if int(ut) == int(page_id):
                    matched.append(it)
            except Exception:
                continue
            if len(matched) >= 100:
                break
        return matched

    def extract_text_attachment(
        self,
        attachment_id: int,
        *,
        max_bytes: int = 400_000,
        max_chars: int = 6000,
    ) -> Tuple[str, str]:
        """
        Texto plano desde adjuntos .txt/.md/.log/.csv/.json (no PDF).
        """
        meta = self.read_attachment(attachment_id)
        if meta.get("external") is True:
            return "", ""
        ext = str(meta.get("extension") or "").lower()
        name = str(meta.get("name") or "").lower()
        allowed = {".txt", ".md", ".markdown", ".log", ".csv", ".json", ".xml", ".yml", ".yaml"}
        ok = ext in {"txt", "md", "markdown", "log", "csv", "json", "xml", "yml", "yaml"}
        if not ok and not any(name.endswith(e.lstrip(".")) for e in allowed):
            return "", ""
        url = str(meta.get("url") or "")
        if not url:
            return "", ""
        raw = self.download_url_bytes(url)
        if len(raw) > max_bytes:
            raw = raw[:max_bytes]
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            text = raw.decode("latin-1", errors="replace")
        text = text.strip()
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "\n\n[...adjunto texto recortado...]"
        return text, url

