"""店裡的固定資料：菜單、客製選項、示範庫存、廚房產能。

這些是寫在程式裡的示範資料（重開程式會重置）；正式訂單存在資料庫，見 db.py。
"""

from __future__ import annotations

# ── 菜單 ──────────────────────────────────────────────
# category 用來推薦替代品；prep_min 是單份在煎台上的製作分鐘數（飲料 0 表示另一條線做）
MENU: dict[str, dict] = {
    "鮪魚蛋餅": {"price": 60, "category": "蛋餅", "allergens": ["蛋", "魚", "麩質"], "prep_min": 4},
    "起司蛋餅": {"price": 55, "category": "蛋餅", "allergens": ["蛋", "乳製品", "麩質"], "prep_min": 4},
    "培根蛋餅": {"price": 60, "category": "蛋餅", "allergens": ["蛋", "麩質"], "prep_min": 4},
    "玉米蛋餅": {"price": 50, "category": "蛋餅", "allergens": ["蛋", "麩質"], "prep_min": 4},
    "豬肉漢堡": {"price": 55, "category": "漢堡", "allergens": ["麩質", "蛋", "大豆"], "prep_min": 5},
    "卡啦雞腿堡": {"price": 75, "category": "漢堡", "allergens": ["麩質", "蛋", "大豆"], "prep_min": 6},
    "花生厚片": {"price": 35, "category": "吐司", "allergens": ["花生", "麩質", "乳製品"], "prep_min": 2},
    "火腿起司吐司": {"price": 45, "category": "吐司", "allergens": ["麩質", "乳製品", "蛋"], "prep_min": 3},
    "蘿蔔糕": {"price": 40, "category": "煎台", "allergens": ["甲殼類"], "prep_min": 5},
    "黑胡椒鐵板麵": {"price": 65, "category": "煎台", "allergens": ["麩質", "大豆", "蛋"], "prep_min": 5},
    "大冰奶": {"price": 35, "category": "飲料", "allergens": ["乳製品"], "prep_min": 0},
    "豆漿": {"price": 25, "category": "飲料", "allergens": ["大豆"], "prep_min": 0},
    "紅茶": {"price": 20, "category": "飲料", "allergens": [], "prep_min": 0},
}

# 加價選項；其他選項（不要蔥、微糖、去冰…）免費
PAID_OPTIONS = {"加蛋": 10, "加起司": 10}
FREE_OPTIONS = {"不要蔥", "不要美乃滋", "醬多", "醬少", "切開", "無糖", "微糖", "半糖", "全糖", "去冰", "少冰", "正常冰", "熱"}
DRINK_ONLY_OPTIONS = {"無糖", "微糖", "半糖", "全糖", "去冰", "少冰", "正常冰", "熱"}

# ── 庫存（份）。鮪魚已經賣完，是故事裡的關鍵轉折 ──────
INVENTORY: dict[str, int] = {
    "鮪魚蛋餅": 0,
    "起司蛋餅": 40,
    "培根蛋餅": 25,
    "玉米蛋餅": 30,
    "豬肉漢堡": 20,
    "卡啦雞腿堡": 12,
    "花生厚片": 30,
    "火腿起司吐司": 25,
    "蘿蔔糕": 30,
    "黑胡椒鐵板麵": 15,
    "大冰奶": 80,
    "豆漿": 60,
    "紅茶": 100,
}

# ── 廚房 ─────────────────────────────────────────────
GRIDDLES = 2  # 兩個煎台同時做
CURRENT_QUEUE_MIN = 18  # 目前排隊中的單，換算成煎台分鐘數
SHOP_NOW = "07:40"  # 故事設定的「現在時間」，讓結果可重現
