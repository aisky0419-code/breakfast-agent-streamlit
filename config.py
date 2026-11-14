"""設定：讀取機密（secrets.toml 或 .env）、模型名稱、需要店員核准的門檻。

機密有兩個來源，程式其他地方一律只用 os.getenv() 讀，不必知道是哪一個：
    .streamlit/secrets.toml   Streamlit 用（本機與 Streamlit Cloud 的 Secrets 都是這個格式）
    .env                      終端機版 main.py、setup_db.py 用
兩邊都有同一個 key 時，以 secrets.toml 為準。
"""

import os

from dotenv import load_dotenv


def _load_streamlit_secrets() -> None:
    """把 secrets.toml 最上層的值放進環境變數；沒有這個檔案就什麼都不做。"""
    try:
        import streamlit as st

        for key, value in st.secrets.items():
            if isinstance(value, (str, int, float)):
                os.environ[key] = str(value)
    except Exception:  # 沒裝 streamlit、沒有 secrets.toml：改用 .env
        pass


_load_streamlit_secrets()
load_dotenv()  # 不會蓋掉已經存在的環境變數

# 不寫死模型名稱：沒設定就沿用 Agents SDK 的預設模型。
from agents.models import get_default_model  # noqa: E402

MODEL = os.getenv("OPENAI_MODEL") or get_default_model()
# guardrail 分類器可以用更便宜、更快的模型
GUARDRAIL_MODEL = os.getenv("GUARDRAIL_MODEL") or MODEL

# 超過這個金額或份數的訂單，必須由店員核准（Human-in-the-loop）
APPROVAL_AMOUNT = 1000
APPROVAL_QTY = 20
# 退款超過這個金額要店員核准
REFUND_APPROVAL_AMOUNT = 100


def require_api_key() -> None:
    """在 CLI 啟動時呼叫；不放在 import 時，測試工具才不需要 API key。"""
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit(
            "找不到 OPENAI_API_KEY：請複製 .env.example 成 .env"
            "（或 .streamlit/secrets.toml.example 成 secrets.toml）並填入你的 key。"
        )
