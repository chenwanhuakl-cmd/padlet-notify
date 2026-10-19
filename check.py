"""Padlet 電子聯絡簿監控：有新貼文或內容被修改時，傳 Telegram 通知。

環境變數：
  FEED_URL         Padlet RSS 網址（含 token）
  TG_BOT_TOKEN     Telegram Bot token
  TG_CHAT_ID       你的 Telegram chat id
參數：
  --test           直接把最新一篇傳出去（測試用）
"""
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


def fetch(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


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


def send(item, label):
    msg = f"📚 聯絡簿{label}：{item['title']}\n\n{item['text'] or '(無文字內容)'}"
    if item["image"]:
        msg += f"\n\n🖼 附圖：{item['image']}"
    msg += f"\n\n{item['link']}"
    data = urllib.parse.urlencode({
        "chat_id": os.environ["TG_CHAT_ID"],
        "text": msg[:4000],
        "disable_web_page_preview": "true",
    }).encode()
    fetch(f"https://api.telegram.org/bot{os.environ['TG_BOT_TOKEN']}/sendMessage", data)


def main():
    items = load_items(os.environ["FEED_URL"])

    if "--test" in sys.argv:
        send(items[-1], "（測試）")
        print("已傳送測試訊息：", items[-1]["title"])
        return

    first_run = not os.path.exists(STATE_FILE)
    state = {} if first_run else json.load(open(STATE_FILE, encoding="utf-8"))

    new_state = {}
    for item in items:
        # 只存雜湊值，不存內容（避免聯絡簿內容外流）
        key = hashlib.sha256(item["id"].encode()).hexdigest()[:16]
        h = digest(item)
        new_state[key] = h
        if first_run or state.get(key) == h:
            continue
        if not item["text"] and not item["image"]:
            continue  # 老師先建的空白卡片，等有內容再通知
        label = "更新" if key in state else "新貼文"
        send(item, label)
        print("已通知：", label, item["title"])

    if new_state != state:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(new_state, f, indent=0, sort_keys=True)
    if first_run:
        print(f"第一次執行，已記錄 {len(items)} 篇現有貼文（不發通知）")


if __name__ == "__main__":
    main()
