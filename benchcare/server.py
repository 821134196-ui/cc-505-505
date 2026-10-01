"""HTTP 服务：JSON API + 静态前端 + 本地照片，全部基于标准库。"""
from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .models import ISSUE_TYPES, MATERIALS
from .service import BenchService, ConflictError, NotFound, ServiceError

STATIC_DIR = Path(__file__).parent / "static"


class Handler(BaseHTTPRequestHandler):
    svc: BenchService = None  # 由 make_server 注入

    # ---- 工具 --------------------------------------------------------------

    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        raw = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        if "application/json" in ctype:
            return json.loads(raw.decode("utf-8"))
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8")).items()}

    def _error(self, e: Exception) -> None:
        if isinstance(e, NotFound):
            self._send_json({"error": str(e)}, 404)
        elif isinstance(e, ConflictError):
            self._send_json({"error": str(e)}, 409)
        elif isinstance(e, ServiceError):
            self._send_json({"error": str(e)}, 400)
        else:
            self._send_json({"error": f"服务器错误：{e}"}, 500)

    def log_message(self, *args):  # 静默默认访问日志
        pass

    # ---- 路由 --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        p = u.path
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        s = self.svc
        try:
            if p == "/api/benches":
                self._send_json(s.list_benches())
            elif p.startswith("/api/benches/"):
                self._send_json(s.bench_detail(p.rsplit("/", 1)[1]))
            elif p == "/api/events":
                self._send_json(s.list_events(open_only=q.get("open") == "1"))
            elif p.startswith("/api/events/"):
                self._send_json(s.event_detail(int(p.rsplit("/", 1)[1])))
            elif p == "/api/workorders":
                self._send_json(s.list_work_orders(q.get("status")))
            elif p == "/api/meta":
                self._send_json({"materials": MATERIALS, "issues": ISSUE_TYPES})
            elif p == "/api/location":
                self._send_json(s.location.current_position())
            elif p.startswith("/photos/"):
                self._photo(p)
            else:
                self._static(p)
        except Exception as e:  # noqa: BLE001
            self._error(e)

    def do_POST(self) -> None:  # noqa: N802
        p = urlparse(self.path).path
        s = self.svc
        data = self._body()
        try:
            if p == "/api/benches":
                self._send_json(s.add_bench(
                    data["id"], data["material"], float(data["lat"]),
                    float(data["lng"]), data["location_name"],
                    data.get("armrest", "ok"), data.get("fixture", "ok")), 201)
            elif p == "/api/report":
                res = s.report_damage(
                    data["bench_id"], data["issue_type"],
                    data.get("reporter", "匿名市民"), data.get("note", ""))
                self._send_json(res, 200 if res["merged"] else 201)
            elif p == "/api/events" and data.get("action") == "report":
                res = s.report_damage(
                    data["bench_id"], data["issue_type"],
                    data.get("reporter", "匿名市民"), data.get("note", ""))
                self._send_json(res, 200 if res["merged"] else 201)
            elif p.startswith("/api/events/"):
                self._event_action(int(p.split("/")[3]), data)
            else:
                self._send_json({"error": "未知接口"}, 404)
        except Exception as e:  # noqa: BLE001
            self._error(e)

    def _event_action(self, eid: int, d: dict) -> None:
        s = self.svc
        action = d.get("action")
        crew = d.get("crew", "")
        if action == "claim":
            self._send_json(s.claim(eid, crew))
        elif action == "cordon":
            self._send_json(s.cordon(eid, crew, d.get("note", "")))
        elif action == "before_photo":
            path = s.upload_photo(d["photo"], d.get("suffix", "jpg"))
            s.attach_before_photo(eid, crew, path)
            self._send_json({"photo": path})
        elif action == "materials":
            self._send_json(s.use_materials(eid, crew, d.get("items", [])))
        elif action == "complete":
            path = d.get("after_photo")
            if path and path.startswith("data:"):
                path = s.upload_photo(path, d.get("suffix", "jpg"))
            self._send_json(s.complete_repair(
                eid, crew, d.get("repair_note", ""), path,
                bool(d.get("painted")), int(d.get("paint_dry_minutes", 60))))
        elif action == "review":
            self._send_json(s.review(
                eid, d.get("inspector", ""), bool(d.get("passed")),
                bool(d.get("tape_removed")), d.get("note", "")))
        else:
            self._send_json({"error": f"未知操作 {action}"}, 400)

    # ---- 静态资源与照片 ----------------------------------------------------

    def _photo(self, path: str) -> None:
        name = Path(path).name
        f = self.svc.photo_dir / name
        if not f.is_file():
            self._send_json({"error": "照片不存在"}, 404)
            return
        self._serve_file(f)

    def _static(self, path: str) -> None:
        if path in ("/", ""):
            f = STATIC_DIR / "index.html"
        else:
            f = (STATIC_DIR / path.lstrip("/")).resolve()
            if not str(f).startswith(str(STATIC_DIR.resolve())) or not f.is_file():
                f = STATIC_DIR / "index.html"  # 单页应用兜底
        self._serve_file(f)

    def _serve_file(self, f: Path) -> None:
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8"
                                                   if ctype.startswith("text") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(svc: BenchService, host: str = "127.0.0.1",
                port: int = 8000) -> ThreadingHTTPServer:
    Handler.svc = svc
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd
