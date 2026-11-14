"""查看資料庫裡的歷史對話：把 agent_sessions、agent_messages 兩張資料表照「欄位」原樣印出來。

只讀取、不修改。內容沒有經過整理，看到的就是資料表裡實際存的樣子
（等同在 Workbench 執行 SELECT * FROM agent_sessions / agent_messages）。

用法：到檔案最下面的 main 區塊修改參數，然後直接按 Run 執行這個檔案。

兩張表的關係：
    agent_sessions   一列 = 一位客人的整段對話
    agent_messages   一列 = 對話中的一個 item（客人的話、agent 的回覆、工具呼叫、工具結果）
                     用 session_id 欄位指回 agent_sessions
"""

from __future__ import annotations

import unicodedata

import db


def _width(text: str) -> int:
    """字串在終端機佔的寬度：中文字佔 2 格。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def print_table(name: str, columns: list[str], rows: list[dict], max_width: int | None) -> None:
    """把查詢結果畫成表格：第一列是欄位名稱，之後每一列是一筆資料。"""
    print(f"\n資料表 {name}（{len(rows)} 列）")
    cells = [[str(row[col]) for col in columns] for row in rows]
    if max_width:  # 太長的欄位值只顯示開頭
        cells = [[c if len(c) <= max_width else c[:max_width] + "…" for c in line] for line in cells]
    widths = [max(_width(col), *(_width(line[i]) for line in cells)) if cells else _width(col)
              for i, col in enumerate(columns)]

    def draw(values: list[str]) -> str:
        return "| " + " | ".join(v + " " * (w - _width(v)) for v, w in zip(values, widths)) + " |"

    border = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    print(border)
    print(draw(columns))
    print(border)
    for line in cells:
        print(draw(line))
    print(border)


def show(customer: str | None = None, max_width: int | None = 80) -> None:
    """customer：只看這位客人，None 代表全部。max_width：每個欄位最多顯示幾個字，None 代表不截斷。"""
    where, params = "", ()
    if customer:
        where, params = " WHERE session_id = %s", (f"customer-{customer}",)

    with db.transaction() as (_, cur):
        for table, order_by in (("agent_sessions", "created_at"), ("agent_messages", "id")):
            cur.execute(f"SELECT * FROM {table}{where} ORDER BY {order_by}", params)
            columns = list(cur.column_names)  # 欄位名稱直接從資料庫取得
            print_table(table, columns, cur.fetchall(), max_width)


if __name__ == "__main__":
    # ── 在這裡修改參數，然後按 Run ──────────────────────────
    CUSTOMER = None  # 要看誰的對話：填名字例如 "小美"；None 代表全部客人
    MAX_WIDTH = 80   # 每個欄位最多顯示幾個字（message_data 很長）；None 代表完整顯示
    # ────────────────────────────────────────────────────────
    show(customer=CUSTOMER, max_width=MAX_WIDTH)
