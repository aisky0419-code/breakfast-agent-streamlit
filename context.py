"""Context：每位客人一份的執行期狀態。

重點觀念：
- Context「不是」給模型看的，模型看不到也改不了這個物件。
- 它是給「你的程式碼」用的：main.py 建立一份，交給 Runner.run(..., context=ctx)，
  SDK 再把同一份傳進每個工具、guardrail 與動態 instructions（包在 RunContextWrapper 裡，
  用 wrapper.context 取出）。
- 所以「這位客人是誰」「購物車裡有什麼」這類事實由程式掌握，不靠模型記、也不怕模型亂講。

三種狀態放哪裡：
    Context（這個檔案）  本次執行的暫時狀態：客人名字、購物車     → 記憶體
    歷史對話（history.py）客人和 agent 說過的話                    → 資料庫 agent_messages
    訂單（db.py）         已確認成立的訂單                         → 資料庫 orders
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CartLine:
    """購物車或訂單裡的一行品項。"""

    item: str
    qty: int
    options: list[str] = field(default_factory=list)
    unit_price: int = 0

    @property
    def subtotal(self) -> int:
        return self.unit_price * self.qty


@dataclass
class ShopContext:
    customer_name: str
    cart: list[CartLine] = field(default_factory=list)
    # 這次對話中成立的訂單編號
    placed_orders: list[str] = field(default_factory=list)
    # 只有店員核准過的優惠才允許 agent 在回覆裡承諾；output guardrail 會檢查
    discount_approved: bool = False

    @property
    def cart_total(self) -> int:
        return sum(line.subtotal for line in self.cart)

    @property
    def cart_qty(self) -> int:
        return sum(line.qty for line in self.cart)
