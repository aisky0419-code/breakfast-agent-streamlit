-- ============================================================
-- 晨光早餐店：breakfast 資料庫還原檔（Aiven MySQL 8）
--
-- 這是整個專案「唯一」定義資料表結構的地方，要改結構只改這個檔案。
-- 用途：把 breakfast 資料庫重設回上課的初始狀態。
-- 注意：會刪除 breakfast 裡現有的訂單與對話紀錄，再重新建立。
--
-- 方法一：MySQL Workbench
--   1. 連上 Aiven（Connection 的 SSL 分頁選 Require）
--   2. File → Open SQL Script… 選這個檔案
--   3. 按閃電圖示（Execute）執行整份檔案
--   4. 左側 Schemas 按重新整理，就會看到 breakfast
--
-- 方法二：python setup_db.py（做的事完全一樣，就是執行這個檔案）
-- ============================================================

SET NAMES utf8mb4;

CREATE DATABASE IF NOT EXISTS `breakfast`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE `breakfast`;

-- 先刪有外鍵的子表，再刪父表
DROP TABLE IF EXISTS `agent_messages`;
DROP TABLE IF EXISTS `agent_sessions`;
DROP TABLE IF EXISTS `orders`;

-- ── 訂單 ─────────────────────────────────────────────────────
CREATE TABLE `orders` (
  `order_id`    VARCHAR(16)  NOT NULL,
  `customer`    VARCHAR(100) NOT NULL,
  `lines`       JSON         NOT NULL,                      -- 品項、數量、選項、單價
  `pickup_time` CHAR(5)      NOT NULL,                      -- HH:MM
  `total`       INT          NOT NULL,
  `status`      VARCHAR(20)  NOT NULL DEFAULT '已確認',
  `refunded`    INT          NOT NULL DEFAULT 0,            -- 累計退款金額
  `created_at`  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`order_id`),
  INDEX `idx_orders_customer` (`customer`),
  CONSTRAINT `chk_orders_total` CHECK (`total` >= 0),
  CONSTRAINT `chk_orders_refunded` CHECK (`refunded` >= 0 AND `refunded` <= `total`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Agents SDK 對話紀錄 ──────────────────────────────────────
CREATE TABLE `agent_sessions` (
  `session_id` VARCHAR(191) NOT NULL,
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`session_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `agent_messages` (
  `id`           BIGINT       NOT NULL AUTO_INCREMENT,
  `session_id`   VARCHAR(191) NOT NULL,
  `message_data` LONGTEXT     NOT NULL,                     -- 一則對話項目的 JSON
  `created_at`   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  INDEX `idx_agent_messages_session` (`session_id`, `id`),
  CONSTRAINT `fk_agent_messages_session` FOREIGN KEY (`session_id`)
    REFERENCES `agent_sessions` (`session_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── 初始資料：教材的示範舊單 ─────────────────────────────────
INSERT INTO `orders`
  (`order_id`, `customer`, `lines`, `pickup_time`, `total`, `status`, `refunded`, `created_at`)
VALUES
  ('B0412', '小美',
   '[{"item": "豬肉漢堡", "qty": 1, "options": ["加蛋"], "unit_price": 65}, {"item": "大冰奶", "qty": 1, "options": ["微糖", "去冰"], "unit_price": 35}]',
   '07:30', 100, '已取餐', 0, '2026-11-11 14:37:55'),
  ('B0413', '大塊',
   '[{"item": "玉米蛋餅", "qty": 1, "options": [], "unit_price": 50}, {"item": "紅茶", "qty": 1, "options": [], "unit_price": 20}]',
   '07:40', 70, '已確認', 0, '2026-11-11 14:37:55');

-- 確認結果：應該看到 2 筆訂單
SELECT `order_id`, `customer`, `total`, `pickup_time`, `status`, `refunded` FROM `orders`;
