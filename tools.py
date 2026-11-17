"""Function tools：agent 能「做」的事。

每個工具就是一個加了 @function_tool 的 Python 函式：
- 參數的型別提示與 docstring 會自動變成給模型看的 schema 與說明。
- 第一個參數寫 ctx: Ctx 的工具會拿到 Context（見 context.py）。ctx 不會出現在 schema 裡，
  模型不知道它存在；ctx.context 就是 main.py 建立的那份 ShopContext。
- 回傳字串（這裡統一回 JSON），模型讀了之後決定下一步。

分區：菜單與庫存 → 購物車（用 Context）→ 訂單（用資料庫）→ 廚房 → 退款
"""

from __future__ import annotations

import json
import re

import mysql.connector
from agents import RunContextWrapper, function_tool
from pydantic import BaseModel

import config
import db
from context import CartLine, ShopContext
from menu import (
    CURRENT_QUEUE_MIN,
    DRINK_ONLY_OPTIONS,
    FREE_OPTIONS,
    GRIDDLES,
    INVENTORY,
    MENU,
    PAID_OPTIONS,
    SHOP_NOW,
)

Ctx = RunContextWrapper[ShopContext]

TIME_FORMAT = r"(?:[01]\d|2[0-3]):[0-5]\d"  # HH:MM


# ── 小工具（不是給模型用的）──────────────────────────────


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _find_item(name: str) -> str | None:
    """容忍模型或客人的寫法差異：「鮪魚 蛋餅」「鮪魚蛋餅」「蛋餅(鮪魚)」"""
    key = re.sub(r"[\s()（）]", "", name)
    if key in MENU:
        return key
    for item in MENU:
        if item in key or key in item:
            return item
    return None


def _make_line(item: str, qty: int, options: list[str]) -> CartLine:
    """檢查品項、數量、選項，算出單價。不合理就丟 ValueError。"""
    name = _find_item(item)
    if not name:
        raise ValueError(f"菜單上沒有「{item}」")
    if qty <= 0:
        raise ValueError("數量必須大於 0")
    is_drink = MENU[name]["category"] == "飲料"
    bad = [
        o
        for o in options
        if o not in PAID_OPTIONS and o not in FREE_OPTIONS or (o in DRINK_ONLY_OPTIONS and not is_drink)
    ]
    if bad:
        raise ValueError(f"{name} 不支援這些選項：{bad}")
    unit = MENU[name]["price"] + sum(PAID_OPTIONS.get(o, 0) for o in options)
    return CartLine(name, qty, options, unit)


def _order_json(o: db.Order) -> dict:
    return {
        "order_id": o.order_id,
        "customer": o.customer,
        "items": [
            {"item": c.item, "qty": c.qty, "options": c.options, "unit_price": c.unit_price, "subtotal": c.subtotal}
            for c in o.lines
        ],
        "total": o.total,
        "refunded": o.refunded,
        "status": o.status,
        "pickup_time": o.pickup_time,
    }


def _cart_json(shop: ShopContext) -> str:
    return _j(
        {
            "lines": [
                {"no": i, "item": c.item, "qty": c.qty, "options": c.options, "unit_price": c.unit_price, "subtotal": c.subtotal}
                for i, c in enumerate(shop.cart, 1)
            ],
            "total": shop.cart_total,
            "total_qty": shop.cart_qty,
            "needs_staff_approval": cart_needs_approval(shop),
        }
    )


# ════════════════════════════════════════════════════════════
# 菜單 / 過敏原 / 庫存：不需要 Context
# ════════════════════════════════════════════════════════════


@function_tool
def get_menu() -> str:
    """查看完整菜單、價格、是否賣完，以及可加的客製選項。"""
    return _j(
        [
            {"item": k, "price": v["price"], "category": v["category"], "sold_out": INVENTORY[k] == 0}
            for k, v in MENU.items()
        ]
        + [{"paid_options": PAID_OPTIONS, "free_options": sorted(FREE_OPTIONS)}]
    )


@function_tool
def get_item_info(item: str) -> str:
    """查單一品項的價格、過敏原、庫存。

    Args:
        item: 品項名稱，例如「鮪魚蛋餅」
    """
    name = _find_item(item)
    if not name:
        return _j({"error": f"菜單上沒有「{item}」"})
    return _j({"item": name, **MENU[name], "stock": INVENTORY[name]})


@function_tool
def find_allergen_safe_items(avoid_allergens: list[str]) -> str:
    """找出不含指定過敏原、且還有庫存的品項。

    Args:
        avoid_allergens: 要避開的過敏原，例如 ["花生", "蛋"]。可用值：蛋、魚、麩質、乳製品、大豆、花生、甲殼類
    """
    safe = [
        {"item": k, "price": v["price"], "allergens": v["allergens"]}
        for k, v in MENU.items()
        if INVENTORY[k] > 0 and not set(v["allergens"]) & set(avoid_allergens)
    ]
    return _j({"avoid": avoid_allergens, "safe_items": safe})


@function_tool
def check_stock(item: str, qty: int) -> str:
    """確認某品項庫存夠不夠。

    Args:
        item: 品項名稱
        qty: 需要的份數
    """
    name = _find_item(item)
    if not name:
        return _j({"error": f"菜單上沒有「{item}」"})
    return _j({"item": name, "requested": qty, "stock": INVENTORY[name], "enough": INVENTORY[name] >= qty})


@function_tool
def suggest_alternatives(item: str) -> str:
    """品項賣完或不夠時，找同類別、有庫存的替代品。

    Args:
        item: 原本想要的品項
    """
    name = _find_item(item)
    if not name:
        return _j({"error": f"菜單上沒有「{item}」"})
    cat = MENU[name]["category"]
    alts = [
        {"item": k, "price": v["price"], "stock": INVENTORY[k]}
        for k, v in MENU.items()
        if v["category"] == cat and k != name and INVENTORY[k] > 0
    ]
    return _j({"original": name, "alternatives": alts})


# ════════════════════════════════════════════════════════════
# 購物車：狀態放在 Context（ctx.context.cart），還沒進資料庫
# ════════════════════════════════════════════════════════════


@function_tool
def add_to_cart(ctx: Ctx, item: str, qty: int, options: list[str]) -> str:
    """把品項加入購物車，回傳最新購物車。

    Args:
        item: 品項名稱，例如「起司蛋餅」
        qty: 份數
        options: 客製選項，例如 ["加蛋", "不要蔥"]、飲料用 ["微糖", "去冰"]；沒有就給空陣列
    """
    shop = ctx.context
    try:
        line = _make_line(item, qty, options)
    except ValueError as exc:
        return _j({"ok": False, "error": str(exc)})
    already = sum(c.qty for c in shop.cart if c.item == line.item)
    if INVENTORY[line.item] < already + qty:
        return _j({"ok": False, "error": f"{line.item} 庫存只剩 {INVENTORY[line.item]} 份", "stock": INVENTORY[line.item]})
    shop.cart.append(line)
    return _cart_json(shop)


@function_tool
def remove_from_cart(ctx: Ctx, line_no: int) -> str:
    """移除購物車中的某一項。

    Args:
        line_no: view_cart 顯示的編號（從 1 開始）
    """
    shop = ctx.context
    if not 1 <= line_no <= len(shop.cart):
        return _j({"ok": False, "error": f"沒有第 {line_no} 項"})
    shop.cart.pop(line_no - 1)
    return _cart_json(shop)


@function_tool
def view_cart(ctx: Ctx) -> str:
    """查看目前購物車與總價。"""
    return _cart_json(ctx.context)


# ════════════════════════════════════════════════════════════
# 訂單：寫進資料庫（db.py）
# ════════════════════════════════════════════════════════════


def cart_needs_approval(shop: ShopContext) -> bool:
    """大額或大量訂單需要店員核准。"""
    return shop.cart_total >= config.APPROVAL_AMOUNT or shop.cart_qty >= config.APPROVAL_QTY


async def _place_order_needs_approval(ctx: Ctx, _params: dict, _call_id: str) -> bool:
    return cart_needs_approval(ctx.context)


# needs_approval：回傳 True 時，Runner 會「暫停」整個 run 並回傳 interruptions，
# 等店員決定後再從同一個狀態繼續（見 main.py）。
@function_tool(needs_approval=_place_order_needs_approval)
def place_order(ctx: Ctx, pickup_time: str) -> str:
    """把購物車存入資料庫成為「已確認」訂單，並回傳廚房工單（會扣庫存，客人確認後才能呼叫）。

    Args:
        pickup_time: 取餐時間，格式 HH:MM，例如 "08:30"
    """
    shop = ctx.context
    if not shop.cart:
        return _j({"ok": False, "error": "購物車是空的"})
    if not re.fullmatch(TIME_FORMAT, pickup_time):
        return _j({"ok": False, "error": "取餐時間格式要是 HH:MM"})
    for line in shop.cart:
        need = sum(c.qty for c in shop.cart if c.item == line.item)
        if INVENTORY[line.item] < need:
            return _j({"ok": False, "error": f"{line.item} 庫存不足（剩 {INVENTORY[line.item]}）"})
    try:
        order = db.create_order(shop.customer_name, list(shop.cart), pickup_time)
    except mysql.connector.Error:
        # 資料庫沒寫成功：不扣庫存、不清購物車，客人可以重試
        return _j({"ok": False, "error": "訂單儲存失敗，尚未成立訂單，請稍後重試。"})
    for line in shop.cart:
        INVENTORY[line.item] -= line.qty
    shop.placed_orders.append(order.order_id)
    shop.cart = []
    ticket = [f"{c.qty} x {c.item} {' '.join(c.options)}".strip() for c in order.lines]
    return _j({"ok": True, "order_id": order.order_id, "status": order.status, "total": order.total,
               "pickup_time": pickup_time, "kitchen_ticket": ticket})


@function_tool
def lookup_order(order_id: str) -> str:
    """用訂單編號查訂單內容。

    Args:
        order_id: 例如 "B0412"
    """
    order = db.get_order(order_id)
    if not order:
        return _j({"error": f"查無訂單 {order_id}"})
    return _j(_order_json(order))


@function_tool
def get_customer_history(ctx: Ctx) -> str:
    """查這位客人以前的所有訂單（熟客說「照舊」、或要找自己的訂單編號時用）。"""
    # 查誰的訂單由 Context 決定，不是模型說了算
    return _j([_order_json(o) for o in db.get_customer_orders(ctx.context.customer_name)])


class OrderItem(BaseModel):
    item: str
    qty: int
    options: list[str]


async def _update_order_needs_approval(_ctx: Ctx, params: dict, _call_id: str) -> bool:
    # 直接改金額（總價、已退款）一定要店員核准；改品項、時間、狀態不用
    return params.get("total") is not None or params.get("refunded") is not None


@function_tool(needs_approval=_update_order_needs_approval)
def update_order(
    ctx: Ctx,
    order_id: str,
    customer: str | None = None,
    pickup_time: str | None = None,
    status: str | None = None,
    items: list[OrderItem] | None = None,
    total: int | None = None,
    refunded: int | None = None,
) -> str:
    """修改一張已成立訂單的資料。只填要改的欄位，其他給 null；回傳修改前後的訂單。

    Args:
        order_id: 要修改的訂單編號，例如 "B0412"
        customer: 新的客人名字
        pickup_time: 新的取餐時間，格式 HH:MM
        status: 新的狀態，只能是「已確認」「製作中」「已取餐」「已取消」（取消訂單就改成「已取消」）
        items: 新的完整品項清單（會整個取代原本的品項，總價依菜單重新計算）
        total: 直接指定總價（一般不用填；只有店員同意調整金額時才用）
        refunded: 直接指定累計已退款金額（一般退款請用 issue_refund）
    """
    shop = ctx.context
    order = db.get_order(order_id)
    if not order:
        return _j({"ok": False, "error": f"查無訂單 {order_id}"})
    # 用 Context 確認身分：客人只能改自己的訂單，模型無法假冒別人
    if order.customer != shop.customer_name:
        return _j({"ok": False, "error": f"訂單 {order.order_id} 不是 {shop.customer_name} 的，不能修改"})

    changes: dict = {}
    try:
        if customer is not None:
            if not customer.strip():
                raise ValueError("客人名字不能是空的")
            changes["customer"] = customer.strip()
        if pickup_time is not None:
            if not re.fullmatch(TIME_FORMAT, pickup_time):
                raise ValueError("取餐時間格式要是 HH:MM")
            changes["pickup_time"] = pickup_time
        if status is not None:
            changes["status"] = status
        if items is not None:
            if not items:
                raise ValueError("品項不能是空的；要取消訂單請把 status 改成「已取消」")
            changes["lines"] = [_make_line(i.item, i.qty, i.options) for i in items]
            changes["total"] = sum(line.subtotal for line in changes["lines"])
        if total is not None:
            changes["total"] = total
        if refunded is not None:
            changes["refunded"] = refunded
        if not changes:
            raise ValueError("沒有指定要修改的欄位")
        updated = db.update_order(order.order_id, **changes)
    except ValueError as exc:
        return _j({"ok": False, "error": str(exc)})
    except mysql.connector.Error:
        return _j({"ok": False, "error": "訂單修改失敗，資料沒有變動，請稍後重試。"})
    return _j({"ok": True, "before": _order_json(order), "after": _order_json(updated)})


# ════════════════════════════════════════════════════════════
# 廚房（給「廚房調度」agent 用，客人碰不到）
# ════════════════════════════════════════════════════════════


def _hm_to_min(hm: str) -> int:
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


class KitchenItem(BaseModel):
    item: str
    qty: int


@function_tool
def estimate_kitchen_load(items: list[KitchenItem], ready_by: str) -> str:
    """估算一批品項在目前排隊狀況下，幾點能做完。

    煎台總分鐘數 = 目前排隊 + Σ(單份製作分鐘 × 份數)，除以煎台數量。飲料不佔煎台。

    Args:
        items: 品項與份數
        ready_by: 需要完成的時間 HH:MM
    """
    griddle_min = CURRENT_QUEUE_MIN
    breakdown = []
    for it in items:
        name = _find_item(it.item)
        if not name:
            return _j({"error": f"菜單上沒有「{it.item}」"})
        minutes = MENU[name]["prep_min"] * it.qty
        griddle_min += minutes
        breakdown.append({"item": name, "qty": it.qty, "griddle_minutes": minutes})
    ready_at = _hm_to_min(SHOP_NOW) + -(-griddle_min // GRIDDLES)  # 無條件進位
    return _j(
        {
            "now": SHOP_NOW,
            "queue_minutes_before_this_order": CURRENT_QUEUE_MIN,
            "griddles": GRIDDLES,
            "breakdown": breakdown,
            "estimated_ready": f"{ready_at // 60:02d}:{ready_at % 60:02d}",
            "ready_by": ready_by,
            "feasible": ready_at <= _hm_to_min(ready_by),
            "prep_minutes_per_item": {k: v["prep_min"] for k, v in MENU.items()},
        }
    )


# ════════════════════════════════════════════════════════════
# 退款
# ════════════════════════════════════════════════════════════


async def _refund_needs_approval(_ctx: Ctx, params: dict, _call_id: str) -> bool:
    return int(params.get("amount", 0)) > config.REFUND_APPROVAL_AMOUNT


@function_tool(needs_approval=_refund_needs_approval)
def issue_refund(order_id: str, amount: int, reason: str) -> str:
    """退款給客人。

    Args:
        order_id: 訂單編號
        amount: 退款金額（元）
        reason: 退款原因
    """
    try:
        order = db.refund_order(order_id, amount)
    except ValueError as exc:
        return _j({"ok": False, "error": str(exc)})
    except mysql.connector.Error:
        return _j({"ok": False, "error": "退款儲存失敗，請稍後重試。"})
    return _j({"ok": True, "order_id": order.order_id, "refunded": amount, "reason": reason})
