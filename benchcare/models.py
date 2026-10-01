"""领域常量：损坏类型、现场措施、材质图标、状态字典。"""

MATERIALS = {
    "wood":    {"label": "木质", "icon": "🪵"},
    "metal":   {"label": "金属", "icon": "⚙️"},
    "stone":   {"label": "石材", "icon": "⬛"},
    "plastic": {"label": "塑钢", "icon": "🧱"},
}

# 扶手 / 固定件状态
PART_STATUS = {
    "ok": "完好",
    "loose": "松动",
    "missing": "缺失",
    "broken": "损坏",
}

# 报损类型 → 现场措施 / 默认封控方式 / 建议材料
ISSUE_TYPES = {
    "loose_bolt": {
        "label": "松动螺栓",
        "measure": "警示带局部围挡，停用涉事座位；重新紧固或更换膨胀螺栓",
        "cordon": "局部警示带",
        "suggested": [("膨胀螺栓", "套"), ("垫片", "个")],
    },
    "wood_split": {
        "label": "木板断裂",
        "measure": "整椅封控围挡，清除毛刺碎板；更换断裂木板并打磨",
        "cordon": "整椅围挡",
        "suggested": [("防腐木板", "块"), ("木螺钉", "个")],
    },
    "wet_paint": {
        "label": "油漆未干",
        "measure": "保留「油漆未干」告示与隔离，禁止坐用；静置至漆面干燥",
        "cordon": "隔离绳+告示牌",
        "suggested": [("防腐面漆", "升")],
    },
    "moved": {
        "label": "座椅被搬动",
        "measure": "复位至原点位，核对基础孔位后重新锚固",
        "cordon": "复位区域警示带",
        "suggested": [("膨胀螺栓", "套"), ("锚固胶", "支")],
    },
    "removed": {
        "label": "已拆除待重装",
        "measure": "核查基础与预埋件，座椅重装到位并锚固",
        "cordon": "基础坑位盖板+警示带",
        "suggested": [("膨胀螺栓", "套"), ("水泥", "袋")],
    },
}

EVENT_STATUS = {
    "reported":        "待认领",
    "claimed":         "已认领",
    "repairing":       "维修中",
    "awaiting_review": "待复验",
    "verified":        "复验通过",
    "closed":          "已归档",
}

# 油漆干燥所需分钟数（默认）
DEFAULT_PAINT_DRY_MINUTES = 60


def issue_label(key: str) -> str:
    return ISSUE_TYPES.get(key, {}).get("label", key)


def material_label(key: str) -> str:
    return MATERIALS.get(key, {}).get("label", key)
