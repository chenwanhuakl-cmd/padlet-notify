"""上班日早上的降雨提醒：看今天 06:00～19:00 基隆市中山區會不會下雨，傳 Telegram。

資料來源：
  - 有設定 CWA_KEY → 中央氣象署 鄉鎮天氣預報（基隆市，逐 3 小時降雨機率）
  - 沒有設定或抓取失敗 → Open-Meteo（不需授權碼，逐小時降雨機率）

只在上班日發（依台灣政府行事曆，含補班日，跟電子聯絡簿用同一套判斷）。
GitHub 排程常會延遲，所以排在 06:30 啟動，程式等到 07:05 才發；
如果啟動時已經超過 07:05，就馬上發。

環境變數：
  CWA_KEY          氣象署開放資料授權碼（選填）
  TG_BOT_TOKEN     Telegram Bot token
  TG_CHAT_ID       你的 Telegram chat id
參數：
  --now            不等到 07:05、不管是不是假日，馬上發
  --dry            只印出訊息，不傳 Telegram（本機測試用）
"""
import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request

from check import is_school_day

TW = datetime.timezone(datetime.timedelta(hours=8))
SEND_AT = datetime.time(7, 5)
FROM_HOUR, TO_HOUR = 6, 19
RAIN_POP = 30  # 降雨機率達到這個 % 就提醒關窗
DISTRICT = "中山區"
CWA_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-049"
LAT, LON = 25.1318, 121.7476  # 基隆市中山區


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _get(d, *keys):
    """氣象署新舊格式的欄位大小寫不同（StartTime / startTime），兩種都試。"""
    for k in keys:
        for kk in (k, k[0].lower() + k[1:]):
            if kk in d:
                return d[kk]
    return None


def _find_location(o):
    if isinstance(o, dict):
        if (_get(o, "LocationName") or "").strip() == DISTRICT:
            return o
        o = list(o.values())
    if isinstance(o, list):
        for v in o:
            r = _find_location(v)
            if r:
                return r
    return None


def _first_value(time_entry):
    v = _get(time_entry, "ElementValue") or _get(time_entry, "Parameter") or []
    v = v[0] if isinstance(v, list) and v else v
    if isinstance(v, dict):
        v = next(iter(v.values()), None)
    return v


def cwa_slots(today):
    """回傳 [(開始時間, 結束時間, 降雨機率, 天氣描述)]，只留跟今天 06～19 點重疊的時段。"""
    q = urllib.parse.urlencode({"Authorization": os.environ["CWA_KEY"], "format": "JSON",
                                "LocationName": DISTRICT})
    loc = _find_location(fetch(f"{CWA_URL}?{q}"))
    if not loc:
        raise RuntimeError("氣象署資料裡找不到" + DISTRICT)
    pop, wx = {}, {}
    for el in _get(loc, "WeatherElement") or []:
        name = _get(el, "ElementName") or ""
        target = pop if ("降雨機率" in name or name.startswith("PoP")) else \
            wx if name in ("天氣現象", "Wx") else None
        if target is None:
            continue
        for t in _get(el, "Time") or []:
            start = datetime.datetime.fromisoformat(_get(t, "StartTime", "DataTime"))
            end = datetime.datetime.fromisoformat(_get(t, "EndTime") or _get(t, "StartTime", "DataTime"))
            target.setdefault((start, end), _first_value(t))
    lo = datetime.datetime.combine(today, datetime.time(FROM_HOUR), TW)
    hi = datetime.datetime.combine(today, datetime.time(TO_HOUR), TW)
    slots = []
    for (start, end), p in sorted(pop.items()):
        if start < hi and end > lo and str(p).strip().isdigit():
            desc = next((w for (s, e), w in wx.items() if s <= start < e), "") or ""
            slots.append((start, end, int(p), str(desc)))
    if not slots:
        raise RuntimeError("氣象署資料裡沒有今天的降雨機率")
    return slots


def open_meteo_slots(today):
    q = urllib.parse.urlencode({"latitude": LAT, "longitude": LON, "timezone": "Asia/Taipei",
                                "hourly": "precipitation_probability,precipitation",
                                "start_date": today.isoformat(), "end_date": today.isoformat()})
    h = fetch(f"https://api.open-meteo.com/v1/forecast?{q}")["hourly"]
    slots = []
    for t, p, mm in zip(h["time"], h["precipitation_probability"], h["precipitation"]):
        start = datetime.datetime.fromisoformat(t).replace(tzinfo=TW)
        if FROM_HOUR <= start.hour < TO_HOUR and p is not None:
            slots.append((start, start + datetime.timedelta(hours=1), int(p), f"{mm} mm" if mm else ""))
    return slots


def build_message(today, slots, source):
    peak = max(p for _, _, p, _ in slots)
    rainy = [s for s in slots if s[2] >= RAIN_POP]
    if rainy:
        head = (f"☔ 今天 {FROM_HOUR:02d}:00～{TO_HOUR}:00 可能下雨（最高降雨機率 {peak}%）\n"
                f"出門前記得把門窗關好！")
    else:
        head = f"☀️ 今天 {FROM_HOUR:02d}:00～{TO_HOUR}:00 不太會下雨（最高降雨機率 {peak}%）"
    lines = [head, ""]
    # 逐小時資料太長，只列有機會下雨的時段；都不會下雨就不列
    for start, end, p, desc in rainy:
        lines.append(f"{start:%H:%M}～{end:%H:%M}　{p}%　{desc}".rstrip())
    lines.append(f"\n{today.month}/{today.day} 基隆市{DISTRICT}・資料來源：{source}")
    return "\n".join(lines).replace("\n\n\n", "\n\n")


def send(msg):
    data = urllib.parse.urlencode({"chat_id": os.environ["TG_CHAT_ID"], "text": msg}).encode()
    urllib.request.urlopen(
        f"https://api.telegram.org/bot{os.environ['TG_BOT_TOKEN']}/sendMessage", data, timeout=30)


def main():
    now = datetime.datetime.now(TW)
    today = now.date()
    if "--now" not in sys.argv and not is_school_day(today):
        print("今天放假，不發降雨提醒")
        return

    slots, source = None, ""
    if os.environ.get("CWA_KEY"):
        try:
            slots, source = cwa_slots(today), "中央氣象署"
        except Exception as e:
            print("氣象署資料抓取失敗，改用 Open-Meteo：", e)
    if not slots:
        slots, source = open_meteo_slots(today), "Open-Meteo"

    msg = build_message(today, slots, source)
    print(msg)
    if "--dry" in sys.argv:
        return

    wait = (datetime.datetime.combine(today, SEND_AT, TW) - datetime.datetime.now(TW)).total_seconds()
    if wait > 0 and "--now" not in sys.argv:
        print(f"等 {int(wait)} 秒到 07:05 再發")
        time.sleep(wait)
    send(msg)
    print("已傳送")


if __name__ == "__main__":
    main()
