# 晨光早餐店

用 OpenAI Agents SDK 做的早餐店對話機器人，前台是 Streamlit 網頁。訂單與歷史對話存在 Aiven MySQL 的 `breakfast` 資料庫（以 `mysql-connector-python` 連線）。

## 檔案架構

一個檔案只負責一件事，由上往下讀就是一個對話機器人的組成：

| 檔案 | 負責什麼 |
| --- | --- |
| `app.py` | 進入點（Streamlit 前台）：網頁對話介面，把 agent、context、歷史對話交給 `Runner.run` |
| `main.py` | 終端機版的進入點：同一套 agent，用 `input()` / `print()` 互動（對照用） |
| `prompts.py` | **Prompt**：每個 agent 的角色說明與結構化輸出格式（只有文字，沒有邏輯） |
| `agent.py` | **Agent**：把 prompt、工具、guardrail 組成五個 agent，並設定誰能轉接給誰 |
| `tools.py` | **Function tools**：agent 能做的事（查菜單、購物車、下單、修改訂單、退款） |
| `context.py` | **Context**：這位客人本次對話的狀態（名字、購物車），由程式掌握、模型看不到 |
| `history.py` | **歷史對話**：把對話存進資料庫，重開程式還記得 |
| `db.py` | **DB**：連線到 Aiven，以及訂單的新增、查詢、修改 |
| `menu.py` | 菜單、庫存等固定的示範資料 |
| `config.py` | 讀機密（`secrets.toml` 或 `.env`）、模型名稱、需要店員核准的門檻 |
| `.streamlit/config.toml` | Streamlit 的外觀與伺服器設定（非機密，會進 git） |
| `.streamlit/secrets.toml` | Streamlit 用的機密（不進 git；範本是 `secrets.toml.example`） |
| `breakfast.sql` | 資料庫還原檔，也是資料表結構**唯一**的定義處 |
| `setup_db.py` | 用 Python 執行 `breakfast.sql`（和在 Workbench 執行一樣） |

三種「狀態」放在不同地方：

| 狀態 | 檔案 | 存在哪裡 | 重開程式後 |
| --- | --- | --- | --- |
| 客人名字、購物車 | `context.py` | 記憶體 | 重置 |
| 客人和 agent 說過的話 | `history.py` | 資料表 `agent_sessions`、`agent_messages` | 保留 |
| 已成立的訂單 | `db.py` | 資料表 `orders` | 保留 |

## 安裝與啟動

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

1. 複製 `.env.example` 成 `.env`，填入 `OPENAI_API_KEY` 與 Aiven 的連線資訊（`DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`）。
2. 建立資料庫：用 Workbench 執行 `breakfast.sql`（見下一節），或執行 `.venv/bin/python setup_db.py`。
3. 開始對話：`.venv/bin/python -m streamlit run app.py`，瀏覽器會開啟 http://localhost:8501
   （終端機版：`.venv/bin/python main.py`）

Aiven 只接受加密（TLS）連線。若在 `.env` 設定 `DB_SSL_CA`（從 Aiven 主控台下載的 `ca.pem` 路徑），會另外驗證伺服器憑證；沒設定時連線仍然加密，但不驗證伺服器身分。

## 部署到 Streamlit Cloud

Streamlit 用兩個 TOML 檔，都放在 `.streamlit/`：

| 檔案 | 內容 | 進 git？ |
| --- | --- | --- |
| `config.toml` | 外觀主題、伺服器行為 | 會 |
| `secrets.toml` | `OPENAI_API_KEY`、Aiven 連線資訊 | **不會**（列在 `.gitignore`） |

`config.py` 啟動時會把 `secrets.toml` 的值放進環境變數，所以其他程式照常用 `os.getenv()` 讀，不必分辨來源；同一個 key 兩邊都有時以 `secrets.toml` 為準。終端機版沒有 `secrets.toml` 也能跑，會改讀 `.env`。

1. 把專案推上 GitHub（先確認 `git status` 裡沒有 `.env` 與 `.streamlit/secrets.toml`）。
2. 到 <https://share.streamlit.io> → Create app，選這個 repo，Main file path 填 `app.py`。
3. Advanced settings：Python 版本選 **3.12**；Secrets 欄貼上本機 `.streamlit/secrets.toml` 的內容。
4. Aiven 主控台若有設定 IP 白名單，要開放給 Streamlit Cloud（它沒有固定 IP，通常得設成 `0.0.0.0/0`）。
5. 資料庫要先建好：部署前在本機執行過一次 `setup_db.py` 即可，雲端與本機連的是同一個 Aiven。

## Streamlit 前台

`app.py` 和 `main.py` 用的是同一套 agent、工具、Context 與歷史對話，只有「怎麼和人互動」不同：

| | 終端機版 `main.py` | Streamlit 版 `app.py` |
| --- | --- | --- |
| 執行方式 | `while True` 迴圈 | 每次互動整個檔案從頭重跑 |
| Context、目前的 agent | 區域變數 | `st.session_state`（重跑後仍保留） |
| 店員核准（Human-in-the-loop） | `input()` 問 y/N | 暫停的 run 存進 `session_state`，畫出核准表單，送出後恢復 |
| 轉接與工具呼叫 | `print()` | 每則回覆下方的「過程」摺疊區 |
| `/reset`、`/quit` | 輸入指令 | 側邊欄的「清除記憶」「換一位客人」按鈕 |

畫面上還有：

- 側邊欄即時顯示購物車與本次成立的訂單（直接讀 `ShopContext`，不是模型說的）。
- 輸入名字後會從資料庫讀出這位客人以前的對話並顯示。
- `Runner.run` 是 async，Streamlit 是同步重跑，所以 `app.py` 在背景執行緒開一個固定的 event loop（`st.cache_resource`），每輪對話都丟給它執行。

菜單庫存（`menu.py` 的 `INVENTORY`）是程式記憶體裡的示範資料，所有瀏覽器分頁共用，重啟 Streamlit 才會重置。

## 用 Workbench 還原資料庫

`breakfast.sql` 是獨立的還原檔，不需要 Python 就能把 `breakfast` 重設回上課的初始狀態（三張資料表 + 示範訂單 `B0412` 小美、`B0413` 大塊）。

1. 用 MySQL Workbench 連上 Aiven（Connection 的 SSL 分頁選 Require）
2. File → Open SQL Script… 選 `breakfast.sql`
3. 按閃電圖示執行整份檔案，再到左側 Schemas 按重新整理

注意：這個檔案會先刪除 `breakfast` 裡現有的訂單與對話紀錄，再重新建立。

資料表結構只寫在這個檔案，Python 程式裡沒有任何 `CREATE TABLE`；要改結構只改這裡，再重新執行一次。

## Context 怎麼用

`main.py` 為每位客人建立一份 `ShopContext`，交給 `Runner.run(..., context=ctx)`。SDK 會把同一份傳到三個地方：

- **工具**（`tools.py`）：第一個參數寫 `ctx: RunContextWrapper[ShopContext]`，用 `ctx.context` 取出。購物車就放在這裡；`update_order` 也用它確認「這張訂單是不是這位客人的」。
- **動態 instructions**（`agent.py` 的 `with_context`）：每一輪把客人名字、購物車金額寫成文字接在 prompt 後面。
- **Guardrail**（`agent.py`）：檢查回覆有沒有承諾未核准的優惠。

Context 不會送給模型，模型也改不了它，所以身分與購物車內容不會被模型講錯或被客人用話術騙過。

## 訂單相關的工具

| 工具 | 作用 |
| --- | --- |
| `place_order` | 客人確認後，把購物車存成「已確認」訂單。資料庫寫入成功才扣庫存、清空購物車 |
| `lookup_order`、`get_customer_history` | 用編號查訂單、查這位客人的所有訂單 |
| `update_order` | 修改已成立訂單的任何欄位：客人名字、取餐時間、狀態、品項、總價、已退款金額 |
| `issue_refund` | 退款（累加已退款金額，不能超過總價） |

`update_order` 的規則：

- 只填要改的欄位。改品項時會整個取代原本的品項，總價依菜單重新計算。
- 取消訂單就是把狀態改成「已取消」。狀態只能是「已確認」「製作中」「已取餐」「已取消」。
- 客人只能改自己的訂單（用 Context 裡的客人名字比對）。
- 直接改總價或已退款金額時，會暫停並請店員核准（Human-in-the-loop）；改品項、時間、狀態不用。
- 資料不合理（時間格式錯、菜單沒有的品項、退款超過總價）會回傳錯誤，資料庫不會變動。
- 修改已成立訂單的品項不會回頭調整示範庫存（庫存只是記憶體裡的示範資料）。

大單（1000 元或 20 份以上）下單、超過 100 元的退款，同樣會請店員核准。

可用任何 MySQL 工具查看訂單：

```sql
SELECT order_id, customer, total, pickup_time, status, refunded, created_at
FROM breakfast.orders ORDER BY created_at DESC;
```

## 測試

```sh
.venv/bin/python -m unittest discover -s tests -v
```

測試會連到 Aiven，用 `breakfast.sql` 建立一個暫時的 `breakfast_test_xxxxxxxx` 資料庫、結束後刪除，不會動到 `breakfast` 的資料。不需 API key（直接呼叫工具，不經過模型），涵蓋 SQL 還原、購物車、下單、編號並行分配、查單、修改訂單、退款、儲存失敗處理及歷史對話。整套約需一分半鐘。
