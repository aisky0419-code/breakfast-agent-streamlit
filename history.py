"""歷史對話：把客人和 agent 的對話存進資料庫（agent_sessions / agent_messages）。

用法（見 main.py）：
    session = MySQLSession("customer-小美")
    await Runner.run(agent, text, session=session)

Runner 每一輪會自動做兩件事：開始前呼叫 get_items() 把舊對話接在前面，
結束後呼叫 add_items() 把新對話存起來。所以關掉程式再開，agent 仍記得之前說過什麼。

Agents SDK 只要求 session 物件有 get_items / add_items / pop_item / clear_session
四個 async 方法（Session protocol），存到哪裡由我們決定。
資料庫操作是同步的，用 asyncio.to_thread 丟到執行緒，才不會卡住 event loop。
"""

from __future__ import annotations

import asyncio
import json

import db


class MySQLSession:
    session_settings = None

    def __init__(self, session_id: str):
        self.session_id = session_id

    def _get(self, limit: int | None) -> list:
        with db.transaction() as (_, cur):
            if limit is None:
                cur.execute(
                    "SELECT message_data FROM agent_messages WHERE session_id = %s ORDER BY id",
                    (self.session_id,),
                )
                rows = cur.fetchall()
            else:  # 取最新 N 筆，再轉回時間順序
                cur.execute(
                    "SELECT message_data FROM agent_messages WHERE session_id = %s ORDER BY id DESC LIMIT %s",
                    (self.session_id, limit),
                )
                rows = cur.fetchall()[::-1]
        return [json.loads(row["message_data"]) for row in rows]

    def _add(self, items: list) -> None:
        with db.transaction() as (_, cur):
            # session 不存在就建立；已存在就更新 updated_at
            cur.execute(
                """INSERT INTO agent_sessions (session_id) VALUES (%s)
                   ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP""",
                (self.session_id,),
            )
            cur.executemany(
                "INSERT INTO agent_messages (session_id, message_data) VALUES (%s, %s)",
                [(self.session_id, json.dumps(item, ensure_ascii=False)) for item in items],
            )

    def _pop(self):
        with db.transaction() as (_, cur):
            cur.execute(
                """SELECT id, message_data FROM agent_messages
                   WHERE session_id = %s ORDER BY id DESC LIMIT 1 FOR UPDATE""",
                (self.session_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute("DELETE FROM agent_messages WHERE id = %s", (row["id"],))
        return json.loads(row["message_data"])

    def _clear(self) -> None:
        with db.transaction() as (_, cur):
            # agent_messages 設了 ON DELETE CASCADE，刪 session 就會一併清掉訊息
            cur.execute("DELETE FROM agent_sessions WHERE session_id = %s", (self.session_id,))

    # ── SDK 會呼叫的四個方法 ──────────────────────────────────

    async def get_items(self, limit: int | None = None) -> list:
        return await asyncio.to_thread(self._get, limit)

    async def add_items(self, items: list) -> None:
        if items:
            await asyncio.to_thread(self._add, items)

    async def pop_item(self):
        return await asyncio.to_thread(self._pop)

    async def clear_session(self) -> None:
        await asyncio.to_thread(self._clear)
