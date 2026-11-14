"""晨光早餐店：Streamlit 前台（網頁版的對話介面）。

執行：.venv/bin/streamlit run app.py

和 main.py（終端機版）做的事完全一樣，一輪對話還是把四樣東西交給 Runner：
    Runner.run(agent, text, context=ctx, session=session)
差別只在「怎麼和人互動」：

    終端機版 main.py             Streamlit 版 app.py
    ─────────────────────────   ──────────────────────────────────────────
    while True 迴圈              每次互動 Streamlit 會把整個檔案「從頭重跑」一次
    區域變數 ctx / current       放在 st.session_state，重跑之後才不會消失
    input() 問店員要不要核准      把暫停的 run 存進 session_state，畫出核准表單，
                                 店員按下送出（又重跑一次）後再從同一個狀態恢復
    print() 印出工具呼叫          收集成文字，放在回覆下方的「過程」摺疊區
"""

from __future__ import annotations

import asyncio
import os
import threading
import uuid

import streamlit as st
from agents import (
    InputGuardrailTripwireTriggered,
    MaxTurnsExceeded,
    OutputGuardrailTripwireTriggered,
    Runner,
    trace,
)
from agents.items import HandoffCallItem, HandoffOutputItem, ToolCallItem, ToolCallOutputItem

import config
from agent import starting_agent
from context import ShopContext
from history import MySQLSession

st.set_page_config(page_title="晨光早餐店", page_icon="🍳", layout="centered")

AVATARS = {"櫃台": "🛎️", "點餐員": "📝", "過敏諮詢": "🥜", "團購專員": "📦", "客服": "🎧"}
ss = st.session_state


# ════════════════════════════════════════════════════════════
# 在 Streamlit 裡執行 async 程式
# ════════════════════════════════════════════════════════════
# Runner.run 是 async。Streamlit 每次重跑都是普通的同步程式，如果每次都用 asyncio.run()
# 開一個新的 event loop，SDK 內部重複使用的 OpenAI 連線會綁在已經關閉的舊 loop 上而出錯。
# 所以整個 app 只開「一個」event loop，放在背景執行緒一直跑，每次把工作丟給它。


@st.cache_resource  # 只建立一次，所有重跑、所有瀏覽器分頁共用
def _event_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return loop


def run_async(coro):
    """把 coroutine 丟到背景的 event loop 執行，等它做完再回傳結果。"""
    return asyncio.run_coroutine_threadsafe(coro, _event_loop()).result()


async def _run(agent, run_input, ctx: ShopContext, session: MySQLSession, group_id: str):
    # 每一段執行一條 trace；同一位客人用同一個 group_id 串起來
    with trace("晨光早餐店", group_id=group_id):
        if isinstance(run_input, str):  # 客人說的新一句話
            return await Runner.run(agent, run_input, context=ctx, session=session, max_turns=20)
        # 店員核准／拒絕之後，從暫停的狀態恢復（context 已經包在 state 裡）
        return await Runner.run(agent, run_input, session=session, max_turns=20)


# ════════════════════════════════════════════════════════════
# 把一輪對話的結果整理成畫面要顯示的資料
# ════════════════════════════════════════════════════════════


def collect_steps(items, steps: list[str], seen: set) -> None:
    """把這一輪發生的轉接與工具呼叫整理成文字（學習用，對應 main.py 的 show_items）。"""
    for it in items:
        raw = it.raw_item
        key = (
            type(it).__name__,
            getattr(raw, "id", None)
            or getattr(raw, "call_id", None)
            or (raw.get("call_id") if isinstance(raw, dict) else None)
            or id(raw),
        )
        if key in seen:
            continue
        seen.add(key)
        if isinstance(it, HandoffCallItem):
            args = getattr(raw, "arguments", "") or ""
            if args.strip() not in ("", "{}"):  # 轉給客服時會帶結構化的「交接單」
                steps.append(f"📋 交接單：{args}")
        elif isinstance(it, HandoffOutputItem):
            steps.append(f"↪ 轉接：{it.source_agent.name} → {it.target_agent.name}")
        elif isinstance(it, ToolCallItem):
            steps.append(f"🔧 [{it.agent.name}] {getattr(raw, 'name', '?')}({getattr(raw, 'arguments', '')})")
        elif isinstance(it, ToolCallOutputItem):
            out = str(it.output)
            steps.append(f"   ↳ {out[:300]}{'…' if len(out) > 300 else ''}")


def add_message(role: str, content: str, agent: str = "", steps: list[str] | None = None, note: str = "") -> None:
    ss.messages.append({"role": role, "content": content, "agent": agent, "steps": steps or [], "note": note})


def run_turn(run_input, steps: list[str], seen: set) -> None:
    """執行（或恢復）一輪對話，把結果寫進 session_state。"""
    ss.pending = None
    try:
        result = run_async(_run(ss.current, run_input, ss.ctx, ss.session, ss.group_id))
        collect_steps(result.new_items, steps, seen)
        if result.interruptions:
            # Human-in-the-loop：run 暫停了。把結果存起來，下次重跑時畫出核准表單。
            ss.pending = {"result": result, "steps": steps, "seen": seen}
            ss.approval_round += 1
            return
        ss.current = result.last_agent  # 下一句話直接從最後接手的 agent 開始
        add_message("assistant", str(result.final_output), agent=ss.current.name, steps=steps)
    except InputGuardrailTripwireTriggered as e:
        info = e.guardrail_result.output.output_info
        add_message("assistant", "不好意思～我們這邊只能幫您處理早餐店的事喔！", agent="櫃台", steps=steps,
                    note=f"input guardrail：{info.reason}")
    except OutputGuardrailTripwireTriggered as e:
        add_message("assistant", "抱歉，這邊幫您再確認一下，請稍等再說一次您的需求 🙏", agent="櫃台", steps=steps,
                    note=f"output guardrail 攔下：{e.guardrail_result.output.output_info}")
    except MaxTurnsExceeded:
        add_message("assistant", "抱歉我有點忙不過來，可以再說一次嗎？", agent="櫃台", steps=steps, note="max_turns")
    except Exception as e:  # 網路、API key、資料庫等問題：顯示在畫面上，不要讓整頁壞掉
        add_message("assistant", "系統出了點狀況，請稍後再試一次。", agent="櫃台", steps=steps,
                    note=f"{type(e).__name__}：{e}")


def load_past_messages(session: MySQLSession) -> list[dict]:
    """從資料庫讀出這位客人以前的對話，只挑「客人說的話」和「agent 的回覆」來顯示。

    agent_messages 裡還有工具呼叫、工具結果等 item，模型看得到，但畫面上不需要。
    """
    past = []
    for item in run_async(session.get_items()):
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            continue
        content = item.get("content")
        if isinstance(content, list):  # agent 的回覆是一串 {"type": "output_text", "text": ...}
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if content and str(content).strip():
            past.append({"role": item["role"], "content": str(content), "agent": "", "steps": [], "note": ""})
    return past


def render_steps(steps: list[str], expanded: bool = False) -> None:
    if steps:
        with st.expander("過程（轉接與工具呼叫）", expanded=expanded):
            st.code("\n".join(steps), language=None, wrap_lines=True)


# ════════════════════════════════════════════════════════════
# 畫面 1：還沒有客人 → 先問名字
# ════════════════════════════════════════════════════════════

if not os.getenv("OPENAI_API_KEY"):
    st.error(
        "找不到 OPENAI_API_KEY：本機請把 `.streamlit/secrets.toml.example` 複製成 `secrets.toml` 並填值；"
        "Streamlit Cloud 請到 App settings → Secrets 貼上同樣的內容。"
    )
    st.stop()

if "ctx" not in ss:
    st.title("🍳 晨光早餐店")
    with st.form("login"):
        name = st.text_input("客人名字", value="小美", help="試試「小美」，她昨天有一張訂單 B0412")
        if st.form_submit_button("開始點餐", type="primary"):
            name = name.strip() or "小美"
            # 歷史對話：用客人名字當 session id，下次來還記得
            session = MySQLSession(f"customer-{name}")
            try:
                past = load_past_messages(session)
            except Exception as e:
                st.error(f"連不上資料庫：{e}")
                st.stop()
            # Context：這位客人這次來店的狀態，整個對話共用同一份
            ss.ctx = ShopContext(customer_name=name)
            ss.session = session
            ss.current = starting_agent  # 目前由哪個 agent 接待；轉接後會換人
            ss.group_id = f"breakfast-{name}-{uuid.uuid4().hex[:6]}"
            ss.messages = past
            ss.pending = None  # 等店員核准中的 run
            ss.approval_round = 0
            st.rerun()
    st.stop()

# ════════════════════════════════════════════════════════════
# 畫面 2：對話
# ════════════════════════════════════════════════════════════

ctx: ShopContext = ss.ctx

# 先處理「送出核准表單」：表單的值在這次重跑一開始就能從 session_state 讀到，
# 在畫任何東西之前先把 run 恢復，下面的側邊欄與對話就會是最新狀態。
if ss.pending and ss.get(f"approval_submit_{ss.approval_round}"):
    pending, rnd = ss.pending, ss.approval_round
    interruptions = pending["result"].interruptions
    decisions = [ss.get(f"decision_{rnd}_{i}") for i in range(len(interruptions))]
    if all(decisions):
        state = pending["result"].to_state()
        for i, (item, decision) in enumerate(zip(interruptions, decisions)):
            if decision == "核准":
                state.approve(item)
            else:
                note = ss.get(f"note_{rnd}_{i}", "").strip()
                state.reject(item, rejection_message=f"店員沒有核准。{note}")
        with st.spinner("處理中…"):
            run_turn(state, pending["steps"], pending["seen"])
    else:
        st.toast("每一項都要選擇核准或拒絕")

with st.sidebar:
    st.header(f"👤 {ctx.customer_name}")
    # 等店員核准時，接待的是「提出要求的那個 agent」（run 還沒結束，ss.current 尚未更新）
    serving = ss.pending["result"].interruptions[0].agent.name if ss.pending else ss.current.name
    st.caption(f"目前接待：{AVATARS.get(serving, '🤖')} {serving}")

    st.subheader("🛒 購物車")  # 直接讀 Context：程式掌握的事實，不是模型說的
    if ctx.cart:
        st.dataframe(
            [{"品項": c.item, "選項": "、".join(c.options), "數量": c.qty, "小計": c.subtotal} for c in ctx.cart],
            hide_index=True,
            width="stretch",
        )
        st.markdown(f"共 **{ctx.cart_qty}** 份、**{ctx.cart_total}** 元")
    else:
        st.caption("（空的）")

    if ctx.placed_orders:
        st.subheader("🧾 本次成立的訂單")
        st.markdown("、".join(f"`{oid}`" for oid in ctx.placed_orders))

    st.divider()
    if st.button("清除記憶", help="刪除這位客人的歷史對話、清空購物車，從櫃台重新開始", width="stretch"):
        run_async(ss.session.clear_session())
        ctx.cart.clear()
        ss.current = starting_agent
        ss.messages = []
        ss.pending = None
        st.rerun()
    if st.button("換一位客人", width="stretch"):
        ss.clear()
        st.rerun()
    st.caption(f"模型：{config.MODEL}")
    st.caption(f"[Tracing](https://platform.openai.com/traces)　group_id=`{ss.group_id}`")

st.title("🍳 晨光早餐店")


def render_message(m: dict) -> None:
    if m["role"] == "user":
        st.chat_message("user").markdown(m["content"])
        return
    with st.chat_message("assistant", avatar=AVATARS.get(m["agent"], "🍳")):
        if m["agent"]:
            st.caption(m["agent"])
        st.markdown(m["content"])
        if m["note"]:
            st.caption(f"⚠️ {m['note']}")
        render_steps(m["steps"])


for m in ss.messages:
    render_message(m)

if ss.pending:
    # Human-in-the-loop：run 暫停中，畫出表單問店員；送出後由上面那段程式恢復執行
    rnd = ss.approval_round
    with st.chat_message("assistant", avatar="🧑‍🍳"):
        st.markdown("**需要店員核准**")
        st.caption(f"客人：{ctx.customer_name}｜購物車：{ctx.cart_qty} 份、{ctx.cart_total} 元")
        render_steps(ss.pending["steps"])
        with st.form(f"approval_{rnd}"):
            for i, item in enumerate(ss.pending["result"].interruptions):
                st.markdown(f"{item.agent.name} 想執行 `{item.tool_name}`")
                st.code(getattr(item.raw_item, "arguments", ""), language="json", wrap_lines=True)
                st.radio("店員決定", ["核准", "拒絕"], index=None, horizontal=True, key=f"decision_{rnd}_{i}")
                st.text_input("拒絕原因（可留空）", key=f"note_{rnd}_{i}")
            st.form_submit_button("送出", type="primary", key=f"approval_submit_{rnd}")

text = st.chat_input("想吃什麼？", disabled=bool(ss.pending))
if text and text.strip():
    add_message("user", text.strip())
    render_message(ss.messages[-1])
    with st.chat_message("assistant", avatar=AVATARS.get(ss.current.name, "🍳")), st.spinner("處理中…"):
        run_turn(text.strip(), [], set())
    st.rerun()  # 重跑一次，讓回覆、側邊欄的購物車、核准表單都用最新狀態畫出來
