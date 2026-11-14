"""晨光早餐店：程式進入點，負責和人互動的對話迴圈。

執行：.venv/bin/python main.py

一輪對話就是把四樣東西交給 Runner：
    Runner.run(agent, text, context=ctx, session=session)
        agent    誰來回答（agent.py，prompt 在 prompts.py、工具在 tools.py）
        text     客人這次說的話
        context  本次執行的狀態：客人名字、購物車（context.py）
        session  歷史對話，存在資料庫（history.py）
"""

from __future__ import annotations

import asyncio
import uuid

from agents import (
    Agent,
    InputGuardrailTripwireTriggered,
    MaxTurnsExceeded,
    OutputGuardrailTripwireTriggered,
    Runner,
    ToolApprovalItem,
    trace,
)
from agents.items import HandoffOutputItem, ToolCallItem, ToolCallOutputItem

import config
from agent import starting_agent
from context import ShopContext
from history import MySQLSession

DIM, RESET = "\033[2m", "\033[0m"


def show_items(items, seen: set) -> None:
    """把這一輪發生的轉接與工具呼叫印出來（學習用）。"""
    for it in items:
        raw = it.raw_item
        key = (type(it).__name__, getattr(raw, "id", None) or getattr(raw, "call_id", None) or (raw.get("call_id") if isinstance(raw, dict) else None) or id(raw))
        if key in seen:
            continue
        seen.add(key)
        if isinstance(it, HandoffOutputItem):
            print(f"  {DIM}↪ 轉接：{it.source_agent.name} → {it.target_agent.name}{RESET}")
        elif isinstance(it, ToolCallItem):
            print(f"  {DIM}🔧 [{it.agent.name}] {getattr(raw, 'name', '?')}({getattr(raw, 'arguments', '')}){RESET}")
        elif isinstance(it, ToolCallOutputItem):
            out = str(it.output)
            print(f"  {DIM}   ↳ {out[:160]}{'…' if len(out) > 160 else ''}{RESET}")


def ask_staff(item: ToolApprovalItem, ctx: ShopContext) -> tuple[bool, str]:
    """Human-in-the-loop：工具需要核准時，問坐在電腦前的店員。"""
    print(f"\n  🧑‍🍳 【需要店員核准】{item.agent.name} 想執行 {item.tool_name}({item.raw_item.arguments})")
    print(f"     客人：{ctx.customer_name}｜購物車：{ctx.cart_qty} 份、{ctx.cart_total} 元")
    ok = input("     店員：核准嗎？[y/N] ").strip().lower() == "y"
    note = "" if ok else input("     店員：拒絕原因（可留空）：").strip()
    return ok, note


async def main() -> None:
    config.require_api_key()
    name = input("客人名字（試試「小美」，她昨天有一張訂單 B0412）：").strip() or "小美"

    # Context：這位客人這次來店的狀態，整個對話共用同一份
    ctx = ShopContext(customer_name=name)
    # 歷史對話：用客人名字當 session id，下次來還記得
    session = MySQLSession(f"customer-{name}")
    # 目前由哪個 agent 接待；轉接後會換人
    current: Agent = starting_agent

    group_id = f"breakfast-{name}-{uuid.uuid4().hex[:6]}"
    print(f"{DIM}模型：{config.MODEL}｜Tracing：https://platform.openai.com/traces（group_id={group_id}）")
    print(f"指令：/reset 清除記憶、/quit 離開{RESET}\n")

    while True:
        text = input("你：").strip()
        if not text:
            continue
        if text in ("/quit", "/q", "exit"):
            break
        if text == "/reset":
            await session.clear_session()
            ctx.cart.clear()
            current = starting_agent
            print(f"{DIM}（記憶已清除）{RESET}")
            continue

        seen: set = set()
        # 每一輪對話一條 trace；同一位客人用同一個 group_id 串起來
        with trace("晨光早餐店", group_id=group_id):
            try:
                result = await Runner.run(current, text, context=ctx, session=session, max_turns=20)
                # Human-in-the-loop：run 暫停 → 問店員 → 從同一個狀態恢復
                while result.interruptions:
                    show_items(result.new_items, seen)
                    state = result.to_state()
                    for item in result.interruptions:
                        ok, note = ask_staff(item, ctx)
                        if ok:
                            state.approve(item)
                        else:
                            state.reject(item, rejection_message=f"店員沒有核准。{note}")
                    result = await Runner.run(current, state, session=session, max_turns=20)
                show_items(result.new_items, seen)
                current = result.last_agent  # 下一句話直接從最後接手的 agent 開始
                print(f"[{current.name}] {result.final_output}\n")
            except InputGuardrailTripwireTriggered as e:
                info = e.guardrail_result.output.output_info
                print(f"[櫃台] 不好意思～我們這邊只能幫您處理早餐店的事喔！ {DIM}(input guardrail：{info.reason}){RESET}\n")
            except OutputGuardrailTripwireTriggered as e:
                print(f"[櫃台] 抱歉，這邊幫您再確認一下，請稍等再說一次您的需求 🙏 {DIM}(output guardrail 攔下：{e.guardrail_result.output.output_info}){RESET}\n")
            except MaxTurnsExceeded:
                print(f"[櫃台] 抱歉我有點忙不過來，可以再說一次嗎？ {DIM}(max_turns){RESET}\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        print("\n掰掰～")
