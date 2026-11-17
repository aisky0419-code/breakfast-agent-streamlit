"""Agent：把 prompt（prompts.py）、工具（tools.py）、守門規則組合成五個 agent，並接好轉接關係。

這個檔案只做「宣告」：有哪些 agent、各自有什麼工具、誰可以轉接給誰。
agent loop、handoff 路由、guardrail 執行、暫停與恢復都交給 SDK。

（檔名是單數 agent.py；`from agents import ...` 的 agents 是 OpenAI Agents SDK 套件，兩者不同。）

    櫃台 ──┬─→ 點餐員
           ├─→ 過敏諮詢
           ├─→ 團購專員 ──（當工具呼叫）──→ 廚房調度
           └─→ 客服
"""

from __future__ import annotations

import re

from agents import (
    Agent,
    GuardrailFunctionOutput,
    RunContextWrapper,
    Runner,
    handoff,
    input_guardrail,
    output_guardrail,
)
from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX

import config
import db
import prompts as P
import tools as T
from context import ShopContext
from menu import SHOP_NOW

Ctx = RunContextWrapper[ShopContext]

# ════════════════════════════════════════════════════════════
# 1. Guardrails：輸入先審查、輸出再檢查
# ════════════════════════════════════════════════════════════

guard_agent = Agent(
    name="輸入審查",
    instructions=P.INPUT_GUARD,
    output_type=P.InputCheck,  # 結構化輸出：直接拿到 pydantic 物件
    model=config.GUARDRAIL_MODEL,
)


def _latest_user_text(inp) -> str:
    """有歷史對話時 guardrail 會拿到整段歷史；只審查最新一句。"""
    if isinstance(inp, str):
        return inp
    for item in reversed(inp):
        if isinstance(item, dict) and item.get("role") == "user":
            c = item.get("content")
            return c if isinstance(c, str) else str(c)
    return str(inp)


@input_guardrail  # 預設和主 agent「平行」執行；觸發時 SDK 會中止主 agent 的 run
async def shop_input_guard(ctx: Ctx, agent: Agent, inp) -> GuardrailFunctionOutput:
    r = await Runner.run(guard_agent, _latest_user_text(inp), context=ctx.context)
    check: P.InputCheck = r.final_output
    return GuardrailFunctionOutput(output_info=check, tripwire_triggered=check.is_off_topic or check.is_manipulation)


def check_reply_policy(reply: str, shop: ShopContext) -> str | None:
    """用程式（不是模型）檢查回覆。回傳 None 代表通過；否則回傳違規原因。

    1. 不能自己承諾免費、打折（除非 Context 裡記錄店員核准過）。
    2. 不能「幻想」訂單編號：回覆裡出現的 Bxxxx 必須真的在資料庫裡。
       （模型很常在沒呼叫 place_order 的情況下說「已幫您下單，編號 B0999」）
    """
    if not shop.discount_approved and re.search(r"免費|招待|打[0-9一二三四五六七八九]折|折扣|送你|送您", reply):
        return "回覆承諾了未經核准的優惠"
    for oid in re.findall(r"B\d{4,}", reply):
        if db.get_order(oid) is None:
            return f"回覆提到不存在的訂單編號 {oid}"
    return None


@output_guardrail
async def policy_guard(ctx: Ctx, agent: Agent, output) -> GuardrailFunctionOutput:
    reason = check_reply_policy(str(output), ctx.context)
    return GuardrailFunctionOutput(output_info=reason, tripwire_triggered=reason is not None)


GUARDS = dict(input_guardrails=[shop_input_guard], output_guardrails=[policy_guard])

# ════════════════════════════════════════════════════════════
# 2. Agents
# ════════════════════════════════════════════════════════════


def with_context(prompt: str):
    """動態 instructions：每一輪都從 Context 讀出最新狀態，接在 prompt 後面。

    模型看不到 Context 物件本身，這裡是我們「選擇」把哪些事實寫成文字給它看。
    """

    def instructions(ctx: Ctx, agent: Agent) -> str:
        shop = ctx.context
        return (
            f"{RECOMMENDED_PROMPT_PREFIX}\n\n{prompt}\n\n"
            f"[現在 {SHOP_NOW}｜客人：{shop.customer_name}｜購物車 {len(shop.cart)} 項、共 {shop.cart_total} 元｜"
            f"本次已成立訂單：{shop.placed_orders or '無'}]"
        )

    return instructions


# Agent[ShopContext]：標明這個 agent 的工具與 guardrail 拿到的 Context 型別

# 內部 agent：不直接面對客人，被團購專員「當成工具」呼叫（agents as tools）
kitchen_planner = Agent[ShopContext](
    name="廚房調度",
    instructions=P.KITCHEN_PLANNER,
    tools=[T.estimate_kitchen_load],
    output_type=P.KitchenPlan,
)

front_desk = Agent[ShopContext](name="櫃台", instructions=with_context(P.FRONT_DESK), **GUARDS)

order_taker = Agent[ShopContext](
    name="點餐員",
    handoff_description="一般點餐、修改或取消已成立的訂單、照舊",
    instructions=with_context(P.ORDER_TAKER),
    tools=[
        T.get_menu,
        T.get_item_info,
        T.check_stock,
        T.suggest_alternatives,
        T.add_to_cart,
        T.remove_from_cart,
        T.view_cart,
        T.place_order,
        T.get_customer_history,
        T.lookup_order,
        T.update_order,
    ],
    **GUARDS,
)

allergy_advisor = Agent[ShopContext](
    name="過敏諮詢",
    handoff_description="過敏原、成分、飲食限制",
    instructions=with_context(P.ALLERGY_ADVISOR),
    tools=[T.get_item_info, T.find_allergen_safe_items],
    **GUARDS,
)

catering = Agent[ShopContext](
    name="團購專員",
    handoff_description="20 份以上、公司訂餐、指定時間的大量訂單",
    instructions=with_context(P.CATERING),
    tools=[
        T.get_menu,
        T.check_stock,
        T.add_to_cart,
        T.remove_from_cart,
        T.view_cart,
        T.place_order,
        kitchen_planner.as_tool(
            tool_name="kitchen_planner",
            tool_description="請廚房調度評估一批品項能否在指定時間前做完，並給出已驗算的調整方案。輸入要寫清楚品項、份數、取餐時間。",
        ),
    ],
    **GUARDS,
)

complaint = Agent[ShopContext](
    name="客服",
    handoff_description="做錯、少給、退款、抱怨",
    instructions=with_context(P.COMPLAINT),
    tools=[T.lookup_order, T.get_customer_history, T.issue_refund, T.update_order],
    **GUARDS,
)

# ════════════════════════════════════════════════════════════
# 3. Handoffs：誰可以把客人轉給誰
# ════════════════════════════════════════════════════════════


async def _on_complaint(ctx: Ctx, info: P.ComplaintInfo) -> None:
    # handoff 可以帶結構化參數：轉接前先讓模型整理好「交接單」
    print(f"  \033[2m📋 交接單 → 客服：{info.summary}（訂單：{info.order_id or '未提供'}）\033[0m")


# 坑：handoff 的工具名稱預設是 transfer_to_<agent 名稱>，中文名稱會被轉成 "transfer_to___" 而互相撞名，
# 所以每個 handoff 都要給英文 tool_name_override。
to_order = handoff(order_taker, tool_name_override="transfer_to_order_taker")
to_allergy = handoff(allergy_advisor, tool_name_override="transfer_to_allergy_advisor")
to_catering = handoff(catering, tool_name_override="transfer_to_catering")
to_complaint = handoff(
    complaint, tool_name_override="transfer_to_complaint", on_handoff=_on_complaint, input_type=P.ComplaintInfo
)

# handoffs 互相引用，所以建好 agent 後再接線
front_desk.handoffs = [to_order, to_allergy, to_catering, to_complaint]
order_taker.handoffs = [to_allergy, to_catering, to_complaint]
allergy_advisor.handoffs = [to_order]
catering.handoffs = [to_order]
complaint.handoffs = [to_order]

# 每位客人都從櫃台開始
starting_agent = front_desk
