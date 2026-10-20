"""欣隆瓦斯帳單監控：出新一期帳單時，傳 Telegram 通知。

網站的數字驗證碼只在瀏覽器端檢查，伺服器登入只需要用戶編號 + 手機末三碼，
所以直接呼叫網站 API，不用處理驗證碼。

排程規則：
  - 每天跑一次，但距離上一期「計費區間結束日（抄表日）」不到 50 天時直接跳過
    （兩次抄表間隔約 57～63 天，帳單在抄表後才會出來）
  - 查到新一期帳單（帳單月份變了）就通知，記下這期的抄表日，然後又休息到 50 天後

環境變數：
  GAS_URL          簡訊裡的帳單網址，例如 https://www.e-bgl.biz/h6JOw
  GAS_PHONE        手機末三碼
  TG_BOT_TOKEN     Telegram Bot token
  TG_CHAT_ID       你的 Telegram chat id
參數：
  --test           直接把目前這期帳單傳出去（測試用）
  --manual         手動執行：忽略「還沒到 50 天」的限制
  --dry            只印出訊息，不傳 Telegram、不寫 state（本機測試用）
"""
import datetime
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gas_state.json")
TW = datetime.timezone(datetime.timedelta(hours=8))
WAIT_DAYS = 50
BASE = "https://www.e-bgl.biz"
COMPANIES = {"h": "/shinlung"}  # 短網址第一個字代表瓦斯公司（網站前端的規則）

_ctx = None


def fetch(url, data=None, headers=None):
    """這個網站的 SSL 憑證鏈不完整，正常驗證失敗時改用不驗證的連線。"""
    global _ctx
    h = {"User-Agent": "Mozilla/5.0"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ctx) as r:
            return r.read()
    except urllib.error.URLError as e:
        if _ctx is None and isinstance(e.reason, ssl.SSLCertVerificationError):
            _ctx = ssl._create_unverified_context()
            return fetch(url, data, headers)
        raise


def multipart(fields):
    boundary = uuid.uuid4().hex
    body = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in fields.items()
    ) + f"--{boundary}--\r\n".encode()
    return body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


class Gas:
    def __init__(self, short_url, phone):
        code = urllib.parse.urlparse(short_url).path.strip("/")
        self.prefix = BASE + COMPANIES[code[0]]
        uid = fetch(f"{self.prefix}/API/url/{code[1:]}").decode().strip()
        body, h = multipart({"username": uid, "password": phone})
        r = json.loads(fetch(f"{self.prefix}/API/login", body, h))
        if not r.get("token"):
            raise RuntimeError(f"登入失敗：{r.get('msg')}")
        self.auth = {"Authorization": f"Bearer {r['token']}"}

    def get(self, path):
        return json.loads(fetch(f"{self.prefix}/API/info/{path}", headers=self.auth))

    def post(self, path, fields):
        body, h = multipart(fields)
        h.update(self.auth)
        return json.loads(fetch(f"{self.prefix}/API/info/{path}", body, h))


def period_end(home):
    """「本期計費區間 :共63天，115/06/04至115/08/06」→ date(2026, 8, 6)"""
    m = re.search(r"至\s*(\d{2,3})/(\d{1,2})/(\d{1,2})", home.get("period") or "")
    if not m:
        return None
    y, mo, d = map(int, m.groups())
    return datetime.date(y + 1911, mo, d)


def build_message(home, meter, label=""):
    clean = lambda s: re.sub(r"\s+", " ", s or "").strip()
    # 照網站主頁的文字與順序：本期計費區間 → 應繳總金額 → 繳費期限
    lines = [f"🔥 瓦斯費新帳單{label}（收費年月 {home['yearMonth']}）", ""]
    lines.append(clean(home.get("period")))
    lines.append(f"{clean(home.get('totalAmount'))} 元")
    lines.append(f"繳費期限:{home.get('deadline')}")
    lines.append("")
    for row in meter.get("meter", []):
        if re.match(r"\s*(上期累計度|本期累計度|收費度數|計費單價|基本費|天然氣費)", row):
            lines.append(clean(row))
    if home.get("smDeadline"):
        lines.append("")
        lines.append(f"超商繳費截止日期:{home['smDeadline']}")
    if home.get("overdue"):
        lines.append("⚠️ 有逾期未繳")
    lines.append("")
    lines.append(os.environ["GAS_URL"])
    return "\n".join(lines)


def send(msg):
    data = urllib.parse.urlencode({
        "chat_id": os.environ["TG_CHAT_ID"],
        "text": msg,
        "disable_web_page_preview": "true",
    }).encode()
    urllib.request.urlopen(
        f"https://api.telegram.org/bot{os.environ['TG_BOT_TOKEN']}/sendMessage", data, timeout=30)


def main():
    today = datetime.datetime.now(TW).date()
    test, dry = "--test" in sys.argv, "--dry" in sys.argv
    manual = "--manual" in sys.argv or test

    first_run = not os.path.exists(STATE_FILE)
    state = {} if first_run else json.load(open(STATE_FILE, encoding="utf-8"))

    if not manual and state.get("period_end"):
        start = datetime.date.fromisoformat(state["period_end"]) + datetime.timedelta(days=WAIT_DAYS)
        if today < start:
            print(f"上期抄表日 {state['period_end']}，{start} 才開始檢查新帳單")
            return

    gas = Gas(os.environ["GAS_URL"], os.environ["GAS_PHONE"])
    home = gas.get("home")
    ym = home.get("yearMonth")
    end = period_end(home)
    print(f"目前帳單：{ym}，抄表日：{end}，已通知過：{state.get('last')}")

    if ym and (test or (ym != state.get("last") and not first_run)):
        meter = gas.post("meter", {"minguoDate": ym})
        msg = build_message(home, meter, "（測試）" if test else "")
        if dry:
            print(msg)
        else:
            send(msg)
        print("已通知：", ym)
    elif first_run:
        print("第一次執行，記錄目前這期帳單（不發通知）")

    if test or dry:
        return
    new_state = {"last": ym, "period_end": end.isoformat() if end else None}
    if new_state != state:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(new_state, f, indent=0, sort_keys=True)


if __name__ == "__main__":
    main()
