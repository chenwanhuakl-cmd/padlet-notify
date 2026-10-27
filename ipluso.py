"""ipluso 團購平台自動化監控與消費統計腳本

功能說明：
  1. 每日取貨提醒（每天 18:00）：監控 UNPAID 且 ARRIVED 的訂單，提示 19:20 順路取貨。
  2. 每兩週進度統計（每兩週的星期日）：統計最近兩週已付款 (PAID) 的團購金額。
  3. 月結算（每月 1 號）：統計上個月所有 PAID 訂單，依 shipping_serial_id 去重，
     比對每月 $2,500 吃吃喝喝團購預算。

  訂單列表沒有日期，所以新的已付款訂單會再查一次明細的 shipping_payment_time，
  記在 ipluso_state.json（只存雜湊值、金額與付款日），之後不再重查。

環境變數：
  IPLUSO_URL       ipluso API 網址或短網址
  IPLUSO_COOKIE    ipluso 登入後的 Cookie 憑證
  TG_BOT_TOKEN     Telegram Bot token
  TG_CHAT_ID       你的 Telegram chat id

參數：
  --test           同時發送「本月到今天為止」的消費統計（測試用）
  --dry            本機測試：僅印出訊息，不傳送 Telegram
"""

import datetime
import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request

TW = datetime.timezone(datetime.timedelta(hours=8))
GROUP_BUDGET = 2500  # 每月團購吃吃喝喝估算預算
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ipluso_state.json")
DETAIL_URL = "https://www.iplusonego.com/api/index.php/shippings/buyer_shipping_detail/{serial_id}"
BIWEEKLY_ANCHOR = datetime.date(2026, 11, 1)  # 雙週報告的基準星期日，之後每 14 天一次


def fetch_api(url, cookie):
    """呼叫 ipluso API，回傳 data 欄位"""
    # ipluso 網站是用 X-SESSION 標頭驗證登入，值就是 Cookie 裡的 sess；
    # IPLUSO_COOKIE 可以放整串 Cookie，也可以只放 sess 的值
    m = re.search(r"(?:^|;\s*)sess=([^;]+)", cookie)
    sess = urllib.parse.unquote(m.group(1)) if m else cookie.strip()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Cookie": cookie,
        "X-SESSION": sess,
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            res = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"呼叫 ipluso API 失敗：{e}")
    if res.get("code") != 200:
        raise RuntimeError(f"API 回傳錯誤代碼：{res.get('code')}（Cookie 可能過期，請重新登入後更新 IPLUSO_COOKIE）")
    return res.get("data") or {}


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
# 2. 雙週進度與當月結算統計（付款日記在 ipluso_state.json）
# ----------------------------------------------------------------------
def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def payment_date(serial_id, cookie, fallback):
    """查訂單明細的付款時間（API 用 UTC），換成台灣日期；查不到就用 fallback"""
    detail = fetch_api(DETAIL_URL.format(serial_id=serial_id), cookie)
    t = detail.get("shipping_payment_time")
    try:
        utc = datetime.datetime.strptime(t, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
        return utc.astimezone(TW).date()
    except (TypeError, ValueError):
        print(f"訂單沒有付款時間（{t!r}），改用 {fallback}")
        return fallback


def update_paid(records, state, cookie, fallback):
    """把新變成 PAID 的訂單記上付款日（以 shipping_serial_id 去重，每筆只查一次明細）"""
    paid = dict((state or {}).get("paid", {}))
    for order in records:
        if order.get("shipping_payment_status") != "PAID":
            continue
        serial_id = str(order.get("shipping_serial_id"))
        # 只存雜湊值，不存訂單編號
        key = hashlib.sha256(serial_id.encode()).hexdigest()[:16]
        if key not in paid:
            price = int(float(order.get("shipping_total_price") or 0))
            paid[key] = {"price": price, "date": payment_date(serial_id, cookie, fallback).isoformat()}
    return {"paid": paid}


def sum_paid(state, start, end):
    """統計付款日介於 start ～ end（含）的金額"""
    total = 0
    for o in state["paid"].values():
        if o["date"] and start.isoformat() <= o["date"] <= end.isoformat():
            total += o["price"]
    return total


def build_biweekly_report(total_spent, start, end):
    """產生雙週報告"""
    lines = [
        "🛒 【雙週團購消費快報】",
        f"⏱ 統計區間：{start:%m/%d} ～ {end:%m/%d}",
        f"💵 雙週已消費：${total_spent:,} 元",
    ]
    if total_spent <= GROUP_BUDGET / 2:
        lines.append("💡 狀態：進度良好，繼續保持！")
    else:
        lines.append(f"💡 狀態：已超過半個月預算 ${GROUP_BUDGET // 2:,} 元，要踩一下煞車囉！")
    return "\n".join(lines)


def build_month_end_report(total_spent, start, end):
    """產生月結算報告"""
    diff = GROUP_BUDGET - total_spent
    lines = [
        f"🎯 【{start.month} 月團購總結算報告】",
        f"📅 結算區間：{start:%m/%d} ～ {end:%m/%d}",
        f"💵 本月實際總花費：${total_spent:,} 元",
        f"📊 設定預算額度：${GROUP_BUDGET:,} 元",
    ]
    if diff >= 0:
        lines.append(f"✅ 本月結語：控制在預算內！還剩 ${diff:,} 元。")
    else:
        lines.append(f"⚠️ 本月結語：超支 ${-diff:,} 元。")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 主程式 logic
# ----------------------------------------------------------------------
def main():
    today = datetime.datetime.now(TW).date()
    yesterday = today - datetime.timedelta(days=1)

    is_test = "--test" in sys.argv
    is_dry = "--dry" in sys.argv

    def output(msg):
        print(msg)
        if not is_dry:
            send_telegram(msg)

    # 1. 抓取 API 資料
    # GitHub 沒設定的 Secret 會是空字串，所以用 or 帶入預設值
    api_url = os.environ.get("IPLUSO_URL") or "https://www.iplusonego.com/api/index.php/shippings/buyer_shipping_records?seller=916912472133&time_filter=recent"
    cookie = os.environ.get("IPLUSO_COOKIE", "")

    old_state = load_state()
    try:
        records = fetch_api(api_url, cookie).get("records") or []
        print(f"成功抓取到 {len(records)} 筆紀錄")
        # 2. 更新付款紀錄（新的已付款訂單去查明細的付款時間）
        state = update_paid(records, old_state, cookie, today)
    except RuntimeError as e:
        output(f"⚠️ ipluso 團購監控失敗\n{e}")
        sys.exit(1)
    if state != old_state and not is_dry:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=0, sort_keys=True)

    # (A) 每日取貨提醒（有未付款到貨才發）
    notice_msg = build_pickup_notice(records)
    if notice_msg:
        output(notice_msg)

    # (B) 每月 1 號：結算上個月（月底最後一晚 19:20 取貨付的錢，要隔天才算得到）
    if today.day == 1 or is_test:
        end = yesterday if today.day == 1 else today
        start = end.replace(day=1)
        output(build_month_end_report(sum_paid(state, start, end), start, end))

    # (C) 每兩週的星期日：統計最近 14 天
    elif (today - BIWEEKLY_ANCHOR).days % 14 == 0:
        start = today - datetime.timedelta(days=14)
        output(build_biweekly_report(sum_paid(state, start, yesterday), start, yesterday))


if __name__ == "__main__":
    main()
