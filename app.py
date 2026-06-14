import os
import sys
import json
import uuid
import threading
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import anthropic
from flask import Flask, redirect, render_template, request
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
TAROT_DATA = json.loads((BASE_DIR / "tarot_data.json").read_text(encoding="utf-8"))
SYSTEM_PROMPT = (BASE_DIR / "tarot_system_prompt.md").read_text(encoding="utf-8")

_readings: dict[str, dict] = {}

app = Flask(__name__)

PROBLEM_LABELS = {
    "復縁":       "一度離れた縁を再び結び直したい。元恋人との復縁を望んでいる",
    "新たな恋":   "次に出会う運命の相手と時期を知りたい。新しい恋愛への準備がある",
    "浮ついた心": "複雑な関係の中にいる。誰にも言えない迷いや秘密の恋がある",
    "運命の人":   "魂の片割れ、ツインレイを探している。運命的な繋がりへの強い確信がある",
}

PAID_MARKER = "---PAID_BOUNDARY---"


def generate_reading(card: dict, is_upright: bool, user_problem: str) -> tuple[str, str]:
    orient = "正位置" if is_upright else "逆位置"
    meaning = card["upright"] if is_upright else card["reversed"]
    prob_desc = PROBLEM_LABELS.get(user_problem, user_problem)

    user_msg = f"""【引いたカード】{card['name']}（{card['name_en']}） / {orient}
【悩みカテゴリー】{user_problem}（{prob_desc}）
【カードの意味 — {orient}】
- キーワード：{', '.join(meaning['keywords'])}
- 恋愛での意味：{meaning['love']}
- 相手の本音：{meaning['truth']}
- 未来：{meaning['future']}

上記の情報をもとに、指定の2部構成（合計3,000文字以上）で鑑定文を出力してください。"""

    client = anthropic.Anthropic()
    result = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=5000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_msg}],
    )
    full_text = result.content[0].text

    if PAID_MARKER in full_text:
        parts = full_text.split(PAID_MARKER, 1)
        return parts[0].strip(), parts[1].strip()
    # Fallback: split at midpoint
    mid = len(full_text) // 2
    for i in range(mid, len(full_text)):
        if full_text[i] == "\n":
            return full_text[:i].strip(), full_text[i:].strip()
    return full_text[:mid].strip(), full_text[mid:].strip()


def build_email_html(card: dict, is_upright: bool, user_problem: str, paid_reading: str) -> str:
    orient = "正位置" if is_upright else "逆位置"
    body = ""
    for line in paid_reading.split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("【") and "】" in s:
            body += f'<h2 style="color:#c9a84c;margin:28px 0 10px;border-left:3px solid #7c3aed;padding-left:12px;font-size:1.0em;">{s}</h2>\n'
        else:
            body += f'<p style="margin:0 0 1em;line-height:2.1;">{s}</p>\n'

    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#030308;font-family:'Hiragino Mincho ProN',Georgia,serif;">
<div style="max-width:640px;margin:0 auto;padding:40px 20px;">
  <div style="text-align:center;margin-bottom:32px;padding-bottom:24px;border-bottom:1px solid rgba(201,168,76,0.2);">
    <div style="font-size:2rem;margin-bottom:12px;">🔮</div>
    <h1 style="color:#c9a84c;font-size:1.4em;font-weight:normal;letter-spacing:0.15em;margin:0 0 8px;">神楽タロット 完全鑑定書</h1>
    <p style="color:#b090d0;font-size:0.82em;margin:0;">
      {card['name']}（{card['name_en']}）/ {orient}<br>
      テーマ：{user_problem}
    </p>
  </div>
  <div style="color:#ede0c4;line-height:2.1;font-size:0.97em;">
    {body}
  </div>
  <div style="text-align:center;margin-top:48px;padding-top:24px;border-top:1px solid rgba(201,168,76,0.2);color:#b090d0;font-size:0.8em;line-height:2.0;">
    ✦ この鑑定はあなただけのために読まれた言葉です ✦<br>
    タロット占い師 神楽（かぐら）
  </div>
</div></body></html>"""


def send_reading_email(email: str, card: dict, is_upright: bool,
                       user_problem: str, paid_reading: str):
    try:
        html = build_email_html(card, is_upright, user_problem, paid_reading)
        orient = "正位置" if is_upright else "逆位置"
        from_addr = os.environ.get("FROM_EMAIL", os.environ["SMTP_USER"])

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🔮【完全鑑定書】{card['name']}が示す「あの人の本音と二人の最終関係」"
        msg["From"] = f"タロット占い師 神楽 <{from_addr}>"
        msg["To"] = email
        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))) as s:
            s.starttls()
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.sendmail(from_addr, email, msg.as_string())
        print(f"[OK] メール送信完了 → {email}", flush=True)
    except Exception as e:
        print(f"[ERROR] send_reading_email: {e}", file=sys.stderr, flush=True)


@app.route("/")
@app.route("/tarot")
def index():
    return render_template("tarot.html", cards_json=json.dumps(TAROT_DATA["cards"]))


@app.route("/tarot/reading", methods=["POST"])
def reading():
    card_id = int(request.form.get("card_id", 0))
    is_upright = request.form.get("is_upright", "true").lower() == "true"
    user_problem = request.form.get("user_problem", "復縁")

    card = TAROT_DATA["cards"][card_id]
    free_part, paid_part = generate_reading(card, is_upright, user_problem)

    # Cache paid part with UUID token
    if len(_readings) > 200:
        oldest = next(iter(_readings))
        del _readings[oldest]
    token = str(uuid.uuid4())
    _readings[token] = {
        "paid": paid_part,
        "card": card,
        "is_upright": is_upright,
        "user_problem": user_problem,
    }

    return render_template("tarot_result.html",
        free_reading=free_part,
        card=card,
        is_upright=is_upright,
        orient_label="正位置" if is_upright else "逆位置",
        user_problem=user_problem,
        token=token,
    )


@app.route("/register", methods=["POST"])
def register():
    email = request.form.get("email", "").strip()
    token = request.form.get("token", "")
    data = _readings.pop(token, None)

    if data and email:
        threading.Thread(
            target=send_reading_email,
            args=(email, data["card"], data["is_upright"],
                  data["user_problem"], data["paid"]),
            daemon=True,
        ).start()

    return render_template("email_sent.html", email=email)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
