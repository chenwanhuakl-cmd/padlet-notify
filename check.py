"""Padlet 電子聯絡簿監控：有新貼文或內容被修改時，傳 Telegram 通知。

排程規則（台灣時間）：
  - 上課日（依台灣政府行事曆，含補班日）12:50 後才檢查
  - 當天的聯絡簿（標題是今天日期，例如 10/19）有內容且已傳送後，當天就不再檢查

環境變數：
  FEED_URL         Padlet RSS 網址（含 token）
  TG_BOT_TOKEN     Telegram Bot token
  TG_CHAT_ID       你的 Telegram chat id
參數：
  --test           直接把最新一篇傳出去（測試用）
  --manual         手動執行：忽略時間、假日與「今日已完成」的限制
"""
import datetime
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
TW = datetime.timezone(datetime.timedelta(hours=8))
START_TIME = datetime.time(12, 50)
CALENDAR_URL = "https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/{year}.json"


def fetch(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def is_school_day(today):
    """依台灣政府行事曆判斷是否上班（上課）日；抓不到行事曆時，以週一到週五為準。"""
    try:
        days = json.loads(fetch(CALENDAR_URL.format(year=today.year)))
        for d in days:
            if d["date"] == today.strftime("%Y%m%d"):
                if d["isHoliday"] and d.get("description"):
                    print("今天放假：", d["description"])
                return not d["isHoliday"]
    except Exception as e:
        print("無法取得行事曆，改用週一到週五判斷：", e)
    return today.weekday() < 5


def html_to_text(s):
    s = re.sub(r"<br\s*/?>", "\n", s or "")
    s = re.sub(r"</p>|</li>|</div>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace("\xa0", " ")
    lines = [ln.rstrip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln.strip()).strip()


def load_items(feed_url):
    root = ET.fromstring(fetch(feed_url))
    items = []
    for it in root.iter("item"):
        enc = it.find("enclosure")
        items.append({
            "id": it.findtext("guid") or it.findtext("link"),
            "title": (it.findtext("title") or "").strip(),
            "text": html_to_text(it.findtext("description")),
            "image": enc.get("url") if enc is not None else "",
            "link": it.findtext("link") or "",
        })
    return items


def digest(item):
    return hashlib.sha256((item["title"] + "\n" + item["text"] + "\n" + item["image"]).encode()).hexdigest()


DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*(?:[/／.\-]|月)\s*(\d{1,2})(?!\d)")


def card_date(item):
    """從標題找日期（例如 10/19、10／19、10.19、10月19日、115/10/19），
    標題沒有日期時改看內文第一行。回傳 (月, 日) 或 None。"""
    first_line = item["text"].split("\n", 1)[0]
    for s in (item["title"], first_line):
        for m in DATE_RE.finditer(s):
            month, day = int(m.group(1)), int(m.group(2))
            if 1 <= month <= 12 and 1 <= day <= 31:
                return month, day
    return None


def is_today_card(item, today):
    return card_date(item) == (today.month, today.day)


def send(item, label):
    msg = f"📚 聯絡簿{label}：{item['title']}\n\n{item['text'] or '(無文字內容)'}"
    if item["image"]:
        msg += "\n\n🖼 有附圖，請點下方連結查看"
    msg += f"\n\n{item['link']}"
    data = urllib.parse.urlencode({
        "chat_id": os.environ["TG_CHAT_ID"],
        "text": msg[:4000],
        "disable_web_page_preview": "true",
    }).encode()
    fetch(f"https://api.telegram.org/bot{os.environ['TG_BOT_TOKEN']}/sendMessage", data)


def main():
    now = datetime.datetime.now(TW)
    today = now.date()
    manual = "--manual" in sys.argv or "--test" in sys.argv

    first_run = not os.path.exists(STATE_FILE)
    state = {} if first_run else json.load(open(STATE_FILE, encoding="utf-8"))
    if state and "posts" not in state:  # 舊版格式
        state = {"posts": state}
    posts = state.get("posts", {})

    if not manual:
        if state.get("done") == today.isoformat():
            print("今天的聯絡簿已經傳過了，不再檢查")
            return
        if now.time() < START_TIME:
            print("還沒到 12:50，不檢查")
            return
        if not is_school_day(today):
            print("今天不用上課，不檢查")
            return

    items = load_items(os.environ["FEED_URL"])
    # 除錯用：只印日期，不印內容
    dates = sorted({d for d in map(card_date, items) if d})
    print(f"共 {len(items)} 篇，有日期的卡片 {len(dates)} 篇，"
          f"最近日期：{'%d/%d' % dates[-1] if dates else '無'}，今天：{today.month}/{today.day}")

    if "--test" in sys.argv:
        send(items[-1], "（測試）")
        print("已傳送測試訊息：", items[-1]["title"])
        return

    new_posts = {}
    today_sent = False
    for item in items:
        # 只存雜湊值，不存內容（避免聯絡簿內容外流）
        key = hashlib.sha256(item["id"].encode()).hexdigest()[:16]
        h = digest(item)
        new_posts[key] = h
        has_content = bool(item["text"] or item["image"])

        if is_today_card(item, today) and has_content:
            # 今天的聯絡簿：不論是剛發還是老師之前就先填好，都傳一次
            if not first_run or manual:
                send(item, "（今日）")
                print("已通知今日聯絡簿：", item["title"])
                today_sent = True
            continue

        if first_run or posts.get(key) == h or not has_content:
            continue  # 沒變動，或是老師先建的空白卡片
        label = "更新" if key in posts else "新貼文"
        send(item, label)
        print("已通知：", label, item["title"])

    new_state = {"posts": new_posts}
    if today_sent:
        new_state["done"] = today.isoformat()
    elif state.get("done"):
        new_state["done"] = state["done"]
    if new_state != state:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(new_state, f, indent=0, sort_keys=True)
    if first_run:
        print(f"第一次執行，已記錄 {len(items)} 篇現有貼文（不發通知）")


if __name__ == "__main__":
    main()
