from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

import requests


@dataclass
class GlpiTicketHit:
    id: int
    name: str


@dataclass
class GlpiFetchOptions:
    followup_range: str = "0-99"
    max_ticket_users: int = 40
    max_group_tickets: int = 20
    max_solutions: int = 5
    max_ticket_links: int = 15
    max_tasks: int = 20


class GlpiClient:
    """
    Cliente GLPI REST API (apirest.php).
    Recomendado: GLPI_USER_TOKEN (clave de acceso remoto) + opcional GLPI_APP_TOKEN.
    session_write=true en initSession suele ser necesario para ver notas internas / seguimientos privados
    según el perfil del usuario.
    """

    def __init__(
        self,
        base_url: str,
        *,
        app_token: str = "",
        user_token: str = "",
        login: str = "",
        password: str = "",
        timeout_s: int = 30,
        session_write_on_enter: bool = True,
    ) -> None:
        self.timeout_s = timeout_s
        self._session_write_on_enter = session_write_on_enter
        self.app_token = (app_token or "").strip()
        self.user_token = (user_token or "").strip()
        self.login = (login or "").strip()
        self.password = password or ""
        b = base_url.strip().rstrip("/")
        if b.endswith("apirest.php"):
            self.api_root = b
        else:
            self.api_root = f"{b}/apirest.php"
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._session_token: Optional[str] = None

    def _auth_headers_init(self) -> Dict[str, str]:
        h: Dict[str, str] = {"Content-Type": "application/json"}
        if self.app_token:
            h["App-Token"] = self.app_token
        if self.user_token:
            h["Authorization"] = f"user_token {self.user_token}"
        elif self.login and self.password:
            raw = f"{self.login}:{self.password}".encode("utf-8")
            h["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
        else:
            raise ValueError("GLPI: indicá GLPI_USER_TOKEN o GLPI_LOGIN + GLPI_PASSWORD")
        return h

    def _auth_headers_session(self) -> Dict[str, str]:
        if not self._session_token:
            raise RuntimeError("GLPI: sesión no iniciada")
        h: Dict[str, str] = {
            "Content-Type": "application/json",
            "Session-Token": self._session_token,
        }
        if self.app_token:
            h["App-Token"] = self.app_token
        return h

    def init_session(self, *, session_write: bool = False) -> None:
        url = f"{self.api_root}/initSession"
        if session_write:
            url += "?session_write=true"
        r = self.session.get(url, headers=self._auth_headers_init(), timeout=self.timeout_s)
        r.raise_for_status()
        data = r.json()
        tok = data.get("session_token")
        if not tok:
            raise RuntimeError("GLPI initSession: respuesta sin session_token")
        self._session_token = str(tok)

    def kill_session(self) -> None:
        if not self._session_token:
            return
        try:
            self.session.get(
                f"{self.api_root}/killSession",
                headers=self._auth_headers_session(),
                timeout=self.timeout_s,
            )
        except Exception:
            pass
        self._session_token = None

    def close(self) -> None:
        self.kill_session()

    def __enter__(self) -> GlpiClient:
        self.init_session(session_write=self._session_write_on_enter)
        return self

    def __exit__(self, *args: object) -> None:
        self.kill_session()

    def _get_json(self, path: str, *, params: Optional[Dict[str, Any]] = None) -> Any:
        r = self.session.get(
            f"{self.api_root}/{path.lstrip('/')}",
            headers=self._auth_headers_session(),
            params=params or {},
            timeout=self.timeout_s,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def _get_list(self, path: str, *, params: Optional[Dict[str, Any]] = None) -> List[Any]:
        data = self._get_json(path, params=params)
        if data is None:
            return []
        return data if isinstance(data, list) else []

    def ticket_exists(self, ticket_id: int) -> Optional[Dict[str, Any]]:
        """GET Ticket/id o None si no existe / sin permiso."""
        data = self._get_json(f"Ticket/{int(ticket_id)}", params={"expand_dropdowns": "true"})
        return data if isinstance(data, dict) else None

    def _ticket_search_request(self, params: Dict[str, Any]) -> Optional[List[Any]]:
        """
        None = HTTP 400 (probar otra variante de parámetros).
        Lista (vacía o no) = respuesta válida.
        """
        r = self.session.get(
            f"{self.api_root}/search/Ticket",
            headers=self._auth_headers_session(),
            params=params,
            timeout=self.timeout_s,
        )
        if r.status_code == 400:
            return None
        r.raise_for_status()
        data = r.json()
        rows = data.get("data")
        return rows if isinstance(rows, list) else []

    def search_ticket_ids(
        self,
        text: str,
        *,
        limit: int,
        title_field: str,
        content_field: str,
        id_sort_field: str = "2",
    ) -> List[GlpiTicketHit]:
        q = (text or "").strip()[:1200]
        if not q:
            return []
        # Pedimos muchas más filas: el motor de GLPI suele ordenar por ID ascendente (viejos primero);
        # sin ampliar el rango, nunca aparecen tickets recientes aunque existan.
        fetch_rows = min(500, max(120, limit * 40))
        hi = fetch_rows - 1
        max_unique = min(150, max(fetch_rows, limit * 30))
        sort_f = int((id_sort_field or "2").strip() or "2")

        or_params: Dict[str, Any] = {
            "criteria[0][field]": int(title_field),
            "criteria[0][searchtype]": "contains",
            "criteria[0][value]": q,
            "criteria[1][link]": "OR",
            "criteria[1][field]": int(content_field),
            "criteria[1][searchtype]": "contains",
            "criteria[1][value]": q,
            "forcedisplay[0]": 2,
            "forcedisplay[1]": 1,
            "range": f"0-{hi}",
        }
        title_only: Dict[str, Any] = {
            "criteria[0][field]": int(title_field),
            "criteria[0][searchtype]": "contains",
            "criteria[0][value]": q,
            "forcedisplay[0]": 2,
            "forcedisplay[1]": 1,
            "range": f"0-{hi}",
        }
        rows: Optional[List[Any]] = None
        for base in (or_params, title_only):
            p_sorted = dict(base)
            p_sorted["sort"] = sort_f
            p_sorted["order"] = "DESC"
            rows = self._ticket_search_request(p_sorted)
            if rows is not None:
                break
            p_plain = dict(base)
            rows = self._ticket_search_request(p_plain)
            if rows is not None:
                break
        if rows is None:
            rows = []
        return self._hits_from_ticket_rows(rows, limit=max_unique)

    def search_ticket_title_all_terms(
        self,
        terms: List[str],
        *,
        limit: int,
        title_field: str,
        id_sort_field: str = "2",
    ) -> List[GlpiTicketHit]:
        """
        Título debe contener todos los términos (AND). Útil si la frase completa no matchea
        pero las palabras clave sí (tildes, orden, etc.).
        """
        cleaned = [t.strip() for t in terms if t and len(t.strip()) >= 2]
        if len(cleaned) < 2:
            return []
        fetch_rows = min(500, max(120, limit * 40))
        hi = fetch_rows - 1
        max_unique = min(150, max(fetch_rows, limit * 30))
        sort_f = int((id_sort_field or "2").strip() or "2")
        params: Dict[str, Any] = {
            "forcedisplay[0]": 2,
            "forcedisplay[1]": 1,
            "range": f"0-{hi}",
        }
        ci = 0
        for ti, term in enumerate(cleaned):
            if ti > 0:
                params[f"criteria[{ci}][link]"] = "AND"
                ci += 1
            params[f"criteria[{ci}][field]"] = int(title_field)
            params[f"criteria[{ci}][searchtype]"] = "contains"
            params[f"criteria[{ci}][value]"] = term[:400]
            ci += 1
        rows: Optional[List[Any]] = None
        p_sorted = dict(params)
        p_sorted["sort"] = sort_f
        p_sorted["order"] = "DESC"
        rows = self._ticket_search_request(p_sorted)
        if rows is None:
            rows = self._ticket_search_request(dict(params))
        if rows is None:
            rows = []
        return self._hits_from_ticket_rows(rows, limit=max_unique)

    def search_ticket_by_num_id(self, ticket_id: int, *, id_field: str = "2") -> List[GlpiTicketHit]:
        """Búsqueda por ID numérico exacto (campo de búsqueda 2 = ID en muchas instalaciones)."""
        params: Dict[str, Any] = {
            "criteria[0][field]": int(id_field),
            "criteria[0][searchtype]": "equals",
            "criteria[0][value]": int(ticket_id),
            "forcedisplay[0]": 2,
            "forcedisplay[1]": 1,
            "range": "0-4",
        }
        r = self.session.get(
            f"{self.api_root}/search/Ticket",
            headers=self._auth_headers_session(),
            params=params,
            timeout=self.timeout_s,
        )
        if not r.ok:
            return []
        data = r.json()
        rows = data.get("data")
        if not isinstance(rows, list):
            return []
        return self._hits_from_ticket_rows(rows, limit=5)

    def search_ticket_ids_in_followups(
        self,
        text: str,
        *,
        limit: int,
        content_field: str,
        ticket_id_result_key: str,
        itemtype_result_key: str = "",
        itemtype_must_contain: str = "Ticket",
    ) -> List[int]:
        """Tickets cuyos seguimientos contienen el texto (items_id del ITILFollowup)."""
        q = (text or "").strip()[:1200]
        if not q:
            return []
        tid_key = str(ticket_id_result_key).strip() or "7"
        cf = int(content_field)
        params: Dict[str, Any] = {
            "criteria[0][field]": cf,
            "criteria[0][searchtype]": "contains",
            "criteria[0][value]": q,
            "forcedisplay[0]": int(tid_key),
            "range": f"0-{max(0, min(499, max(100, limit * 24)) - 1)}",
        }
        itk = str(itemtype_result_key).strip()
        if itk.isdigit():
            params["forcedisplay[1]"] = int(itk)
        r = self.session.get(
            f"{self.api_root}/search/ITILFollowup",
            headers=self._auth_headers_session(),
            params=params,
            timeout=self.timeout_s,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        rows = data.get("data")
        if not isinstance(rows, list):
            return []
        out: List[int] = []
        seen: Set[int] = set()
        needle = (itemtype_must_contain or "").strip().lower()
        for row in rows:
            if not isinstance(row, dict):
                continue
            if itk and needle:
                tv = row.get(itk)
                if tv is None and itk.isdigit():
                    tv = row.get(int(itk))
                if needle not in str(tv).lower():
                    continue
            raw = row.get(tid_key)
            if raw is None and tid_key.isdigit():
                raw = row.get(int(tid_key))
            if raw is None:
                for k, v in row.items():
                    if str(k) == tid_key:
                        raw = v
                        break
            try:
                tid = int(raw)
            except (TypeError, ValueError):
                continue
            if tid in seen:
                continue
            seen.add(tid)
            out.append(tid)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _hits_from_ticket_rows(rows: List[Any], *, limit: int) -> List[GlpiTicketHit]:
        out: List[GlpiTicketHit] = []
        seen: Set[int] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            sid = row.get("2") or row.get(2)
            try:
                tid = int(sid)
            except (TypeError, ValueError):
                continue
            if tid in seen:
                continue
            seen.add(tid)
            name = str(row.get("1") or row.get(1) or "").strip() or f"Ticket #{tid}"
            out.append(GlpiTicketHit(id=tid, name=name))
            if len(out) >= limit:
                break
        return out

    def get_ticket_bundle(
        self,
        ticket_id: int,
        *,
        opts: GlpiFetchOptions,
    ) -> Dict[str, Any]:
        ticket = self._get_json(f"Ticket/{int(ticket_id)}", params={"expand_dropdowns": "true"})
        if not isinstance(ticket, dict):
            raise RuntimeError(f"GLPI: ticket {ticket_id} no disponible")

        followups = self._get_list(
            f"Ticket/{int(ticket_id)}/ITILFollowup",
            params={"range": opts.followup_range, "expand_dropdowns": "true"},
        )
        ticket_users = self._get_list(
            f"Ticket/{int(ticket_id)}/Ticket_User",
            params={
                "range": f"0-{max(0, opts.max_ticket_users - 1)}",
                "expand_dropdowns": "true",
            },
        )
        group_tickets = self._get_list(
            f"Ticket/{int(ticket_id)}/Group_Ticket",
            params={
                "range": f"0-{max(0, opts.max_group_tickets - 1)}",
                "expand_dropdowns": "true",
            },
        )
        solutions = self._get_list(
            f"Ticket/{int(ticket_id)}/ITILSolution",
            params={
                "range": f"0-{max(0, opts.max_solutions - 1)}",
                "expand_dropdowns": "true",
            },
        )
        ticket_ticket = self._get_list(
            f"Ticket/{int(ticket_id)}/Ticket_Ticket",
            params={
                "range": f"0-{max(0, opts.max_ticket_links - 1)}",
                "expand_dropdowns": "true",
            },
        )
        tasks = self._get_list(
            f"Ticket/{int(ticket_id)}/TicketTask",
            params={
                "range": f"0-{max(0, opts.max_tasks - 1)}",
                "expand_dropdowns": "true",
            },
        )
        item_tickets = self._get_list(
            f"Ticket/{int(ticket_id)}/Item_Ticket",
            params={"range": "0-24", "expand_dropdowns": "true"},
        )

        return {
            "ticket": ticket,
            "followups": followups,
            "ticket_users": ticket_users,
            "group_tickets": group_tickets,
            "solutions": solutions,
            "ticket_ticket": ticket_ticket,
            "tasks": tasks,
            "item_tickets": item_tickets,
        }
