"""HTTP 服务：标准库 ThreadingHTTPServer + JSON API + 静态页面。

登录为演示级：POST /api/login {name, role} 后写 cookie。
角色：citizen 市民 / crew 养护班组 / inspector 巡查员 / dispatcher 调度员。
"""
import json
import re
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from . import services
from .db import init_db
from .location import current_position, nearest_seats

WEB_DIR = Path(__file__).resolve().parent / "web"
ROLES = {"citizen": "市民", "crew": "养护班组", "inspector": "巡查员", "dispatcher": "调度员"}


class Handler(BaseHTTPRequestHandler):
    server_version = "SeatCare/1.0"

    # ---------- 基础工具 ----------
    def _user(self):
        cookie = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        if "user" in cookie:
            try:
                payload = json.loads(cookie["user"].value)
                if payload.get("role") in ROLES:
                    return payload.get("name", "未命名"), payload["role"]
            except Exception:
                pass
        # 也允许请求头覆盖（便于测试）
        name = self.headers.get("X-User")
        role = self.headers.get("X-Role")
        if role in ROLES:
            return name or ("测试" + ROLES[role]), role
        return "游客", "citizen"

    def _json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise services.BusinessError("请求体不是合法 JSON")

    def _send_json(self, obj, status=200, extra_headers=None):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path, ctype):
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _require_role(self, role):
        name, my_role = self._user()
        if my_role != role:
            raise services.BusinessError(f"需要{ROLES[role]}身份，当前为{ROLES.get(my_role)}")
        return name

    def log_message(self, fmt, *args):
        pass  # 安静启动

    # ---------- GET 路由 ----------
    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)
            if path in ("/", "/index.html"):
                return self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            if path == "/app.js":
                return self._send_file(WEB_DIR / "app.js", "application/javascript; charset=utf-8")
            if path == "/styles.css":
                return self._send_file(WEB_DIR / "styles.css", "text/css; charset=utf-8")
            if path == "/api/meta":
                return self._send_json({
                    "damage_types": services.DAMAGE_TYPES,
                    "materials": services.MATERIALS,
                    "armrest": services.ARMREST,
                    "fixture": services.FIXTURE,
                    "roles": ROLES,
                })
            if path == "/api/me":
                name, role = self._user()
                return self._send_json({"name": name, "role": role, "role_label": ROLES[role]})
            if path == "/api/seats":
                return self._send_json(services.list_seats(qs.get("park", [None])[0]))
            if path == "/api/incidents":
                return self._send_json(services.list_incidents(qs.get("status", [None])[0]))
            if path == "/api/reports":
                return self._send_json(services.list_reports(qs.get("incident_id", [None])[0]))
            if path == "/api/work-orders":
                return self._send_json(services.list_work_orders(qs.get("status", [None])[0]))
            if path == "/api/location":
                return self._send_json(current_position())
            if path == "/api/location/nearest":
                return self._send_json(nearest_seats())
            m = re.fullmatch(r"/api/seats/(\d+)", path)
            if m:
                return self._send_json(services.get_seat(int(m.group(1))))
            m = re.fullmatch(r"/api/work-orders/(\d+)", path)
            if m:
                return self._send_json(services.get_work_order(int(m.group(1))))
            m = re.fullmatch(r"/api/photos/([\w.\-]+)", path)
            if m:
                p = services.photo_path(m.group(1))
                ctype = ("image/png" if p.suffix == ".png" else
                         "image/webp" if p.suffix == ".webp" else "image/jpeg")
                return self._send_file(p, ctype)
            self._send_json({"error": "not found"}, 404)
        except services.BusinessError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            self._send_json({"error": f"服务器错误: {e}"}, 500)

    # ---------- POST 路由 ----------
    def do_POST(self):
        try:
            path = urlparse(self.path).path
            body = self._json_body()
            name, role = self._user()

            if path == "/api/login":
                r = body.get("role", "citizen")
                if r not in ROLES:
                    raise services.BusinessError("角色不合法")
                uname = body.get("name") or ROLES[r]
                payload = json.dumps({"name": uname, "role": r})
                return self._send_json(
                    {"name": uname, "role": r, "ok": True},
                    extra_headers=[("Set-Cookie", f"user={payload}; Path=/; Max-Age=86400")])

            if path == "/api/seats":
                actor = self._require_role("dispatcher")
                seat = services.create_seat(
                    actor, body["code"], body["name"], body["park"], body["location"],
                    float(body["lat"]), float(body["lng"]), body["material"],
                    body.get("armrest", "none"), body.get("fixture", "ok"))
                return self._send_json(seat, 201)

            m = re.fullmatch(r"/api/seats/(\d+)", path)
            if m:
                self._require_role("dispatcher")
                # usable 不开放直接修改：封控/恢复必须走工单与复验流程
                fields = {k: v for k, v in body.items()
                          if k in ("name", "park", "location", "lat", "lng",
                                   "material", "armrest", "fixture", "status_note")}
                return self._send_json(services.update_seat(name, int(m.group(1)), **fields))

            if path == "/api/reports":
                self._require_role("citizen")
                res = services.submit_report(
                    name, int(body["seat_id"]), body["damage_type"],
                    body.get("description", ""), body.get("phone", ""))
                return self._send_json(res, 201)

            m = re.fullmatch(r"/api/reports/(\d+)/link", path)
            if m:
                self._require_role("dispatcher")
                return self._send_json(services.link_report_to_incident(
                    name, int(m.group(1)), int(body["incident_id"])))

            m = re.fullmatch(r"/api/work-orders/(\d+)/claim", path)
            if m:
                actor = self._require_role("crew")
                return self._send_json(services.claim_work_order(actor, int(m.group(1))))

            m = re.fullmatch(r"/api/work-orders/(\d+)/closures", path)
            if m:
                self._require_role("crew")
                return self._send_json(services.record_closure(
                    name, int(m.group(1)), body["action"], body.get("note", "")), 201)

            m = re.fullmatch(r"/api/work-orders/(\d+)/finish", path)
            if m:
                self._require_role("crew")
                return self._send_json(services.finish_work_order(
                    name, int(m.group(1)), body.get("repair_note", ""),
                    bool(body.get("paint_wet", False))))

            m = re.fullmatch(r"/api/work-orders/(\d+)/paint-dry", path)
            if m:
                self._require_role("crew")
                return self._send_json(services.mark_paint_dry(name, int(m.group(1))))

            m = re.fullmatch(r"/api/work-orders/(\d+)/inspect", path)
            if m:
                actor = self._require_role("inspector")
                return self._send_json(services.inspect_work_order(
                    actor, "inspector", int(m.group(1)),
                    bool(body["passed"]), body.get("note", ""),
                    bool(body.get("tape_confirmed", False))))

            m = re.fullmatch(r"/api/work-orders/(\d+)/materials", path)
            if m:
                self._require_role("crew")
                return self._send_json(services.add_material(
                    name, int(m.group(1)), body["name"],
                    float(body["quantity"]), body.get("unit", "件")), 201)

            m = re.fullmatch(r"/api/seats/(\d+)/photos", path)
            if m:
                phase = body["phase"]
                if role == "citizen" and phase != "report":
                    raise services.BusinessError("市民只能上传报损照片")
                if role == "crew" and phase not in ("before", "after"):
                    raise services.BusinessError("班组只能上传维修前/后照片")
                if role == "inspector" and phase != "reopen":
                    raise services.BusinessError("巡查员只能上传重开核对照片")
                return self._send_json(services.save_photo(
                    name, int(m.group(1)), phase, body["data"],
                    body.get("work_order_id"), body.get("ext", "jpg")), 201)

            self._send_json({"error": "not found"}, 404)
        except services.BusinessError as e:
            self._send_json({"error": str(e)}, 400)
        except KeyError as e:
            self._send_json({"error": f"缺少字段: {e}"}, 400)
        except Exception as e:  # noqa: BLE001
            self._send_json({"error": f"服务器错误: {e}"}, 500)


def run(host="127.0.0.1", port=8000):
    init_db()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"🪑 公共座椅管护系统已启动： http://{host}:{port}", flush=True)
    print("   演示数据含重复报损案例：GY-003 已有 2 次市民报损归并到同一未结事件", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    import sys
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    run(port=p)
