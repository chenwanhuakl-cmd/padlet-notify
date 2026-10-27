"""ipluso 團購平台自動化監控與消費統計腳本

功能說明：
  1. 每日取貨提醒（每天 18:00）：監控 UNPAID 且 ARRIVED 的訂單，提示 19:20 順路取貨。
  2. 每兩週進度統計（每兩週的星期日）：統計最近兩週已付款 (PAID) 的團購金額。
  3. 月底總結算（當月最後一天）：統計當月所有 PAID 訂單，依 shipping_serial_id 去重，
     比對每月 $2,500 吃吃喝喝團購預算。

環境變數：
  IPLUSO_URL       ipluso API 網址或短網址
  IPLUSO_COOKIE    ipluso 登入後的 Cookie 憑證
  TG_BOT_TOKEN     Telegram Bot token
  TG_CHAT_ID       你的 Telegram chat id

參數：
  --test           直接測試發送「今日取貨提醒」與「本月消費統計」
  --dry            本機測試：僅印出訊息，不傳送 Telegram
"""

import datetime
import json
import os
import sys
import urllib.parse
import urllib.request

TW = datetime.timezone(datetime.timedelta(hours=8))
GROUP_BUDGET = 2500  # 每月團購吃吃喝喝估算預算


def fetch_api(url, cookie):
    """呼叫 ipluso API 抓取買家出貨與訂單紀錄"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Cookie": cookie
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            res = json.loads(response.read().decode("utf-8"))
            if res.get("code") == 200:
                return res.get("data", {}).get("records", [])
            else:
                print(f"API 回傳錯誤代碼：{res.get('code')}")
                return []
    except Exception as e:
        print(f"呼叫 ipluso API 失敗：{e}")
        return []


def send_telegram(msg):
    """發送 Telegram 訊息"""
    url = f"https://api.telegram.org/bot{os.environ['TG_BOT_TOKEN']}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": os.environ["TG_CHAT_ID"],
        "text": msg,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
    urllib.request.urlopen(req, timeout=30)


# ----------------------------------------------------------------------
# 1. 每日 19:20 順路取貨提醒 (UNPAID & ARRIVED)
# ----------------------------------------------------------------------
def build_pickup_notice(records):
    """整理待面交/自取的未付款訂單（以 serial_id 去重）"""
    unpaid_arrived = {}

    for order in records:
        # 只看未付款且已到貨的項目
        if order.get("shipping_payment_status") == "UNPAID" and order.get("shipping_status") == "ARRIVED":
            serial_id = order.get("shipping_serial_id")
            total_price = int(float(order.get("shipping_total_price", 0)))
            items = [item.get("product_title") for item in order.get("shipping_product_items", [])]

            # 以 shipping_serial_id 作為唯一 Key 覆蓋最新狀態，避免多天累積重複計算
            unpaid_arrived[serial_id] = {
                "price": total_price,
                "items": items
            }

    if not unpaid_arrived:
        return None

    grand_total = sum(o["price"] for o in unpaid_arrived.values())
    
    lines = ["🛍️ 【ipluso 團購取貨提醒】", "今天 19:20 順路下車取貨！", "───────────────"]
    for sid, info in unpaid_arrived.items():
        lines.append(f"📦 訂單號：#{sid.split('_')[-1]}")
        for item_title in info["items"]:
            lines.append(f"  • {item_title}")
        lines.append(f"  💰 小計：${info['price']} 元")
        lines.append("")

    lines.append("───────────────")
    lines.append(f"💵 現場預計支付總額：${grand_total} 元")
    lines.append("⏰ 營業時間至 21:00，請記得取貨喔！")

    return "\n".join(lines)


# ----------------------------------------------------------------------
# 2. 雙週進度與當月結算統計 (PAID 訂單去重統計)
# ----------------------------------------------------------------------
def calculate_paid_spending(records):
    """精準統計已付款 (PAID) 訂單，採用 shipping_serial_id 去重"""
    paid_orders = {}

    for order in records:
        if order.get("shipping_payment_status") == "PAID":
            serial_id = order.get("shipping_serial_id")
            price = int(float(order.get("shipping_total_price", 0)))
            items = [item.get("product_title") for item in order.get("shipping_product_items", [])]

            paid_orders[serial_id] = {
                "price": price,
                "items": items
            }

    total_spent = sum(o["price"] for o in paid_orders.values())
    return total_spent, paid_orders


def build_biweekly_report(total_spent, today):
    """產生每兩週報告"""
    lines = [
        f"📊 【團購吃吃喝喝・雙週消費進度】",
        f"📅 統計日期：{today.month}/{today.day}",
        "───────────────",
        f"💵 累計已花費：${total_spent:,} 元",
        f"🎯 月預算額度：${GROUP_BUDGET:,} 元",
        f"💡 剩餘額度：${GROUP_BUDGET - total_spent:,} 元"
    ]
    return "\n".join(lines)


def build_month_end_report(total_spent, today):
    """產生當月最後一天總結算報告"""
    diff = GROUP_BUDGET - total_spent
    lines = [
        f"🎯 【ipluso 團購 {today.month} 月份總結算報告】",
        f"📅 結算區間：{today.month} 月全月",
        "───────────────",
        f"💵 本月實際總花費：${total_spent:,} 元",
        f"📊 估算預算額度：${GROUP_BUDGET:,} 元"
    ]

    if diff >= 0:
        lines.append(f"✅ 控管良好！本月團購還賸餘 ${diff:,} 元。")
    else:
        lines.append(f"⚠️ 本月團購超支 ${abs(diff):,} 元（將從私房錢/好市多預算微調）。")

    return "\n".join(lines)


# ----------------------------------------------------------------------
# 主程式 logic
# ----------------------------------------------------------------------
def main():
    now = datetime.datetime.now(TW)
    today = now.date()
    
    is_test = "--test" in sys.argv
    is_dry = "--dry" in sys.argv

    # 1. 抓取 API 資料
    api_url = os.environ.get("IPLUSO_URL", "https://www.iplusonego.com/api/index.php/shippings/buyer_shipping_records?seller=916912472133&time_filter=recent")
    cookie = os.environ.get("IPLUSO_COOKIE", "")

    records = fetch_api(api_url, cookie)
    print(f"成功抓取到 {len(records)} 筆紀錄")

    # 2. 測試模式直接印出所有報告
    if is_test:
        print("=== 執行測試模式 ===")
        notice = build_pickup_notice(records)
        if notice:
            print("[取貨提醒]\n", notice)
            if not is_dry:
                send_telegram(notice)

        total_spent, _ = calculate_paid_spending(records)
        month_report = build_month_end_report(total_spent, today)
        print("\n[月底結算報告]\n", month_report)
        if not is_dry:
            send_telegram(month_report)
        return

    # 3. 正常排程邏輯判斷：
    
    # (A) 每日取貨提醒（有未付款到貨才發）
    notice_msg = build_pickup_notice(records)
    if notice_msg:
        print("觸發每日取貨提醒")
        if is_dry:
            print(notice_msg)
        else:
            send_telegram(notice_msg)

    # (B) 判斷是否為「當月最後一天」
    tomorrow = today + datetime.timedelta(days=1)
    is_month_end = (tomorrow.day == 1)

    # (C) 判斷是否為「每兩週的星期日」（利用 ISO 週別奇偶數）
    is_biweekly_sunday = (today.weekday() == 6) and (today.isocalendar()[1] % 2 == 0)

    total_spent, _ = calculate_paid_spending(records)

    if is_month_end:
        print("觸發當月最後一天結算")
        msg = build_month_end_report(total_spent, today)
        if is_dry:
            print(msg)
        else:
            send_telegram(msg)

    elif is_biweekly_sunday:
        print("觸發雙週進度檢視")
        msg = build_biweekly_report(total_spent, today)
        if is_dry:
            print(msg)
        else:
            send_telegram(msg)


if __name__ == "__main__":
    main()