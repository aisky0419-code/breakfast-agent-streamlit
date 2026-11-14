"""把 Aiven 上的 breakfast 資料庫重設回初始狀態：做的事就是執行 breakfast.sql。

執行：.venv/bin/python setup_db.py

和在 MySQL Workbench 開啟 breakfast.sql 按執行完全一樣；資料表結構只寫在那個檔案。
注意：會清空 breakfast 裡現有的訂單與對話紀錄。
"""

from __future__ import annotations

from pathlib import Path

import db

SQL_FILE = Path(__file__).resolve().parent / "breakfast.sql"


def restore(database: str = db.DB_NAME) -> list[dict]:
    """執行 breakfast.sql，回傳檔案最後那句 SELECT 的結果（初始訂單）。

    database 可以換成別的名稱，測試用它建立暫時的資料庫。
    """
    sql = SQL_FILE.read_text(encoding="utf-8").replace("`breakfast`", f"`{database}`")
    conn = db.connect(database=None)
    try:
        rows: list[dict] = []
        with conn.cursor(dictionary=True) as cur:
            cur.execute(sql)  # 一次送出整份檔案的多句 SQL
            while True:  # 逐句取回結果，全部讀完才算執行完成
                if cur.with_rows:
                    rows = cur.fetchall()
                if not cur.nextset():
                    break
        conn.commit()
        return rows
    finally:
        conn.close()


if __name__ == "__main__":
    print(f"這會清空 Aiven 上 {db.DB_NAME} 的訂單與對話紀錄，並重設回初始狀態。")
    if input("確定要繼續嗎？[y/N] ").strip().lower() != "y":
        raise SystemExit("已取消")
    for row in restore():
        print("  ", row)
    print(f"✓ {db.DB_NAME} 已還原")
