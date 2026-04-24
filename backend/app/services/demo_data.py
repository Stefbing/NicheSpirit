from __future__ import annotations

from datetime import datetime, timedelta


def dashboard_snapshot() -> dict:
    return {
        "home_name": "AutoHome 控制中心",
        "scene": {
            "title": "今晚已自动巡检",
            "subtitle": "喂食器、猫厕所与体脂数据都已同步",
        },
        "overview": [
            {"label": "在线设备", "value": 4, "trend": "+1"},
            {"label": "今日喂食", "value": "6 次", "trend": "计划正常"},
            {"label": "猫厕访问", "value": "11 次", "trend": "清理 2 次"},
            {"label": "最近体重", "value": "67.8 kg", "trend": "-0.4"},
        ],
        "rooms": [
            {"name": "客厅", "count": 1},
            {"name": "阳台", "count": 1},
            {"name": "宠物角", "count": 2},
        ],
    }


def hotspot_items() -> list[dict]:
    return [
        {"id": "hot-1", "title": "AI 终端设备控制体验升级", "source": "GitHub", "hotness": "9.8k", "tag": "dev"},
        {"id": "hot-2", "title": "智能家居交互设计趋势", "source": "微博", "hotness": "12.1w", "tag": "trend"},
        {"id": "hot-3", "title": "短视频品牌页交互改版", "source": "抖音", "hotness": "8.4w", "tag": "ux"},
        {"id": "hot-4", "title": "X 热门开源项目更新", "source": "X", "hotness": "4.6k", "tag": "open"},
    ]


def petkit_devices() -> list[dict]:
    return [
        {
            "id": "petkit-t4-01",
            "name": "主猫厕所",
            "model": "PetKit Pura Max 2",
            "status": "online",
            "sand_level": 72,
            "odor_level": "低",
            "last_cleaned_at": "18:20",
            "stats": {
                "today_visits": 11,
                "avg_duration": "3m 12s",
                "last_pet_weight": 4.7,
            },
        }
    ]


def cloudpets_device() -> dict:
    return {
        "id": "cloudpets-feeder-01",
        "name": "主喂食器",
        "status": "online",
        "food_level": 61,
        "battery": 84,
        "servings_today": 6,
        "plans": [
            {"id": "plan-1", "time": "07:30", "serving": 1, "days": [1, 2, 3, 4, 5, 6, 7], "enable": True},
            {"id": "plan-2", "time": "18:30", "serving": 2, "days": [1, 2, 3, 4, 5, 6, 7], "enable": True},
            {"id": "plan-3", "time": "22:30", "serving": 1, "days": [1, 2, 3, 4, 5], "enable": False},
        ],
    }


def device_seed_rows() -> list[dict]:
    return [
        {
            "platform": "petkit",
            "device_type": "litterbox",
            "device_name": "主猫厕所",
            "device_uid": "petkit-t4-01",
            "source_type": "wifi",
            "bind_status": "bound",
            "supports_control": True,
            "is_active": True,
        },
        {
            "platform": "cloudpets",
            "device_type": "feeder",
            "device_name": "主喂食器",
            "device_uid": "cloudpets-feeder-01",
            "source_type": "wifi",
            "bind_status": "bound",
            "supports_control": True,
            "is_active": True,
        },
        {
            "platform": "xiaomi",
            "device_type": "scale",
            "device_name": "体脂秤",
            "device_uid": "xiaomi-scale-01",
            "source_type": "wifi",
            "bind_status": "bound",
            "supports_control": False,
            "is_active": True,
        },
    ]


def weight_history() -> list[dict]:
    now = datetime.now()
    return [
        {
            "id": index + 1,
            "timestamp": int((now - timedelta(days=index)).timestamp() * 1000),
            "weight": round(67.8 + index * 0.2, 1),
            "bmi": round(22.1 + index * 0.1, 1),
            "body_fat": round(18.2 + index * 0.2, 1),
            "muscle": round(51.0 - index * 0.1, 1),
            "water": round(57.2 - index * 0.15, 1),
            "xiaomi_pushed": index < 3,
        }
        for index in range(7)
    ]
