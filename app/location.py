"""定位服务适配器。

真实环境可替换为高德/百度/系统 GPS SDK；本项目使用模拟适配器，
按座椅坐标返回稳定的“模拟定位”，并给出到目标座椅的估算距离与导航文案。
"""
import math
from .db import get_conn


def current_position():
    """模拟当前位置：返回第一个未结事件座椅附近的坐标，否则返回公园中心。"""
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT s.lat, s.lng, s.name FROM seats s
               JOIN incidents i ON i.seat_id=s.id AND i.status='open'
               ORDER BY i.id ASC LIMIT 1"""
        ).fetchone()
        if row:
            # 模拟人在该座椅北侧约 40 米
            return {"lat": row["lat"] + 0.00036, "lng": row["lng"],
                    "source": "mock", "desc": f"模拟定位：{row['name']}附近步道"}
        c = conn.execute("SELECT AVG(lat), AVG(lng) FROM seats").fetchone()
        return {"lat": c[0] or 31.2300, "lng": c[1] or 121.4700,
                "source": "mock", "desc": "模拟定位：公园南门"}
    finally:
        conn.close()


def distance_m(lat1, lng1, lat2, lng2):
    """Haversine 估算，单位米。"""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 1)


def nearest_seats(limit=5):
    pos = current_position()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id,code,name,park,location,lat,lng,material,usable,fixture FROM seats"
        ).fetchall()
    finally:
        conn.close()
    items = []
    for r in rows:
        d = dict(r)
        d["distance_m"] = distance_m(pos["lat"], pos["lng"], r["lat"], r["lng"])
        items.append(d)
    items.sort(key=lambda x: x["distance_m"])
    return {"position": pos, "seats": items[:limit]}
