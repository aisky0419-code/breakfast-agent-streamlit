"""資料庫：連線到 Aiven MySQL，以及 orders 資料表的新增、查詢、修改。

- 連線資訊放在 .env（DB_HOST / DB_PORT / DB_USER / DB_PASSWORD），不寫進程式碼。
- 資料表結構只定義在 breakfast.sql，這裡沒有任何 CREATE TABLE。
- 每個函式自己開一條連線、做完一次交易就關閉，容易理解也不會互相干擾。
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass

import mysql.connector
from dotenv import load_dotenv

from context import CartLine

load_dotenv()

DB_NAME = "breakfast"  # 與 breakfast.sql 裡的名稱一致；測試會換成暫時的資料庫

ORDER_STATUSES = ("已確認", "製作中", "已取餐", "已取消")
# update_order 允許修改的欄位（白名單，欄位名稱不會來自外部輸入）
EDITABLE_COLUMNS = ("customer", "lines", "pickup_time", "total", "status", "refunded")

# 訂單編號去掉開頭的 B 之後的數字，用來排序與分配下一號。
_NUMBER = "CAST(SUBSTRING(order_id, 2) AS UNSIGNED)"


@dataclass
class Order:
    order_id: str
    customer: str
    lines: list[CartLine]
    pickup_time: str
    total: int
    status: str = "已確認"
    refunded: int = 0


# ── 連線 ─────────────────────────────────────────────────────


def connect(database: str | None = ""):
    """連到 Aiven。database 預設用 DB_NAME；傳 None 則不選資料庫（還原資料庫時用）。"""
    missing = [k for k in ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD") if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            f"缺少資料庫設定：{', '.join(missing)}（參考 .env.example 或 .streamlit/secrets.toml.example）"
        )
    ca = os.getenv("DB_SSL_CA")
    # Aiven 只接受 TLS 連線。有 CA 憑證就完整驗證；沒有則只加密、不驗證伺服器身分。
    tls = dict(ssl_ca=ca, ssl_verify_cert=True, ssl_verify_identity=True) if ca else {}
    return mysql.connector.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=DB_NAME if database == "" else database,
        charset="utf8mb4",
        connection_timeout=10,
        ssl_disabled=False,
        **tls,
    )


@contextmanager
def transaction():
    """一次交易：正常結束就 commit，丟例外就 rollback，最後一定關閉連線。

    用法：with transaction() as (conn, cur): cur.execute(...)
    cursor 每列回傳 dict；buffered 會把結果一次讀完，同一條連線才能接著下一句 SQL。
    """
    conn = connect()
    try:
        with conn.cursor(dictionary=True, buffered=True) as cur:
            yield conn, cur
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── orders 資料表 ────────────────────────────────────────────


def _lines_json(lines: list[CartLine]) -> str:
    return json.dumps([asdict(line) for line in lines], ensure_ascii=False)


def _to_order(row: dict) -> Order:
    return Order(
        row["order_id"], row["customer"],
        [CartLine(**line) for line in json.loads(row["lines"])],
        row["pickup_time"], row["total"], row["status"], row["refunded"],
    )


def create_order(customer: str, lines: list[CartLine], pickup_time: str) -> Order:
    with transaction() as (conn, cur):
        # 具名鎖：把「查最大編號 → 新增」排成一次一個，避免同時下單拿到相同編號。
        cur.execute("SELECT GET_LOCK(%s, 10) AS ok", (f"{DB_NAME}.order_id",))
        if cur.fetchone()["ok"] != 1:
            raise mysql.connector.OperationalError("等待訂單編號鎖逾時")
        try:
            cur.execute(f"SELECT COALESCE(MAX({_NUMBER}), 412) + 1 AS n FROM orders")
            order = Order(f"B{int(cur.fetchone()['n']):04d}", customer, lines, pickup_time,
                          sum(line.subtotal for line in lines))
            cur.execute(
                """INSERT INTO orders (order_id, customer, `lines`, pickup_time, total, status, refunded)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (order.order_id, order.customer, _lines_json(order.lines),
                 order.pickup_time, order.total, order.status, order.refunded),
            )
            conn.commit()
        finally:
            cur.execute("SELECT RELEASE_LOCK(%s)", (f"{DB_NAME}.order_id",))
    return order


def get_order(order_id: str) -> Order | None:
    with transaction() as (_, cur):
        cur.execute("SELECT * FROM orders WHERE order_id = %s", (order_id.strip().upper(),))
        row = cur.fetchone()
    return _to_order(row) if row else None


def get_customer_orders(customer: str) -> list[Order]:
    with transaction() as (_, cur):
        cur.execute(f"SELECT * FROM orders WHERE customer = %s ORDER BY {_NUMBER}", (customer,))
        rows = cur.fetchall()
    return [_to_order(row) for row in rows]


def update_order(order_id: str, **changes) -> Order:
    """修改訂單的任何欄位，例如 update_order("B0412", status="已取消", pickup_time="08:00")。

    可改的欄位見 EDITABLE_COLUMNS；lines 傳 list[CartLine]。資料不合理時丟 ValueError，不會寫入。
    """
    unknown = set(changes) - set(EDITABLE_COLUMNS)
    if unknown or not changes:
        raise ValueError(f"可修改的欄位：{', '.join(EDITABLE_COLUMNS)}")
    with transaction() as (_, cur):
        # FOR UPDATE 鎖住這一筆，檢查與寫入之間不會被別人改掉。
        cur.execute("SELECT * FROM orders WHERE order_id = %s FOR UPDATE", (order_id.strip().upper(),))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"查無訂單 {order_id}")
        old = _to_order(row)
        new = Order(**{**old.__dict__, **changes})
        if new.status not in ORDER_STATUSES:
            raise ValueError(f"狀態只能是：{'、'.join(ORDER_STATUSES)}")
        if new.total < 0 or not 0 <= new.refunded <= new.total:
            raise ValueError(f"金額不合理：總價 {new.total}、已退款 {new.refunded}（退款不能超過總價）")
        values = {k: _lines_json(v) if k == "lines" else v for k, v in changes.items()}
        cur.execute(
            f"UPDATE orders SET {', '.join(f'`{k}` = %s' for k in values)} WHERE order_id = %s",
            (*values.values(), old.order_id),
        )
    return new


def refund_order(order_id: str, amount: int) -> Order:
    """退款：累加 refunded，總額不可超過訂單總價。"""
    order = get_order(order_id)
    if order is None:
        raise ValueError(f"查無訂單 {order_id}")
    if amount <= 0 or amount > order.total - order.refunded:
        raise ValueError(f"可退金額上限 {order.total - order.refunded} 元")
    with transaction() as (_, cur):
        # 條件寫在 UPDATE 裡：就算兩筆退款同時進來，也不會退超過總價。
        cur.execute(
            "UPDATE orders SET refunded = refunded + %s WHERE order_id = %s AND refunded + %s <= total",
            (amount, order.order_id, amount),
        )
        if cur.rowcount != 1:
            raise ValueError("退款失敗：可退金額已變動，請重新查詢訂單")
    order.refunded += amount
    return order
