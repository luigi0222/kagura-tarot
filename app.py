import os
import sys
import json
import threading
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import anthropic
import stripe
from flask import Flask, redirect, render_template, request
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
TAROT_DATA = json.loads((BASE_DIR / "tarot_data.json").read_text(encoding="utf-8"))
SYSTEM_PROMPT = (BASE_DIR / "tarot_system_prompt.md").read_text(encoding="utf-8")

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")

_processed: set[str] = set()

app = Flask(__name__)

PROBLEM_LABELS = {
    "片思い":       "片思い中で、相手があなたをどう思っているか",
    "復縁":         "別れた相手との復縁を願っている状況",
    "浮気・秘密の恋": "公には言えない秘密の関係・複雑な恋愛状況",
    "相手の本音":   "今付き合っているまたは気になる相手の本当の気持ち",
}


def generate_free(card: dict, is_upright: bool, user_problem: str) -> str:
    orient = "正位置" if is_upright else "逆位置"
    meaning = card["upright"] if is_upright else card["reversed"]
    prob_desc = PROBLEM_LABELS.get(user_problem, user_problem)

    user_msg = f"""
【引いたカード】{card['name']}（{card['name_en']}） / {orient}
【悩みカテゴリー】{user_problem}（{prob_desc}）
【カードの意味 — {orient}】
- キーワード：{', '.join(meaning['keywords'])}
- 恋愛での意味：{meaning['love']}
- 相手の本音：{meaning['truth']}
- 未来：{meaning['future']}

①導入と②展開を出力し、③の有料壁テンプレートで締めてください。
""".strip()

    client = anthropic.Anthropic()
    result = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=2500,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_msg}],
    )
    return result.content[0].text


def generate_paid(card: dict, is_upright: bool, user_problem: str) -> str:
    orient = "正位置" if is_upright else "逆位置"
    meaning = card["upright"] if is_upright else card["reversed"]

    system = f"""あなたはタロット占い師・神楽です。
有料鑑定として以下の2章を生成してください（合計1,500文字以上）。

【あの人の隠れた本音】（700〜800文字）
カードの意味を核に、相手が隠している本音を断言スタイルで読み解く。
悩みカテゴリー「{user_problem}」の状況に合わせた具体的なシナリオ。

【二人の最終的な結末】（700〜800文字）
3〜6ヶ月後の展開を「〇月頃に〇〇が起きる」という具体性で描く。
希望を持てる形で締める。

口調はカジュアルで辛口。AIっぽさ禁止。断言で語る。"""

    user_msg = f"""【カード】{card['name']}（{orient}）
【キーワード】{', '.join(meaning['keywords'])}
【本音の意味】{meaning['truth']}
【未来の意味】{meaning['future']}
【悩み】{user_problem}"""

    client = anthropic.Anthropic()
    result = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=3000,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    return result.content[0].text


def send_email(to_email: str, card: dict, is_upright: bool, content: str):
    orient = "正位置" if is_upright else "逆位置"
    body_html = ""
    for line in content.split("\n"):
        s = line.strip()
        if s.startswith("【") and "】" in s:
            body_html += f'<h2 style="color:#c9a84c;margin-top:28px;border-left:3px solid #7c3aed;padding-left:10px;">{s}</h2>\n'
        elif s:
            body_html += f'<p style="margin:0 0 1em;line-height:2.1;">{s}</p>\n'

    html = f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#030308;font-family:'Hiragino Mincho ProN',Georgia,serif;">
<div style="max-width:640px;margin:0 auto;padding:40px 20px;">
  <div style="text-align:center;margin-bottom:32px;padding-bottom:24px;border-bottom:1px solid rgba(201,168,76,0.2);">
    <div style="font-size:1.6em;color:#c9a84c;margin-bottom:10px;">🔮</div>
    <h1 style="color:#c9a84c;font-size:1.4em;font-weight:normal;letter-spacing:0.15em;margin:0 0 8px;">タロット完全鑑定書</h1>
    <p style="color:#b090d0;font-size:0.85em;margin:0;">{card['name']}（{card['name_en']}）/ {orient}</p>
  </div>
  <div style="color:#ede0c4;line-height:2.1;font-size:0.98em;">{body_html}</div>
  <div style="text-align:center;margin-top:40px;padding-top:20px;border-top:1px solid rgba(201,168,76,0.2);color:#b090d0;font-size:0.82em;">
    タロット占い師 神楽（かぐら）
  </div>
</div></body></html>"""

    from_addr = os.environ.get("FROM_EMAIL", os.environ["SMTP_USER"])
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🔮【タロット完全鑑定】{card['name']}が示す「あの人の本音と最終結末」"
    msg["From"] = f"タロット占い師 神楽 <{from_addr}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.sendmail(from_addr, to_email, msg.as_string())


def generate_and_send(card_id: int, is_upright: bool, user_problem: str, email: str):
    try:
        card = TAROT_DATA["cards"][card_id]
        content = generate_paid(card, is_upright, user_problem)
        send_email(email, card, is_upright, content)
        print(f"[OK] 送信完了 → {email}", flush=True)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr, flush=True)


@app.route("/")
@app.route("/tarot")
def index():
    cards_json = json.dumps(TAROT_DATA["cards"])
    return render_template("tarot.html", cards_json=cards_json)


@app.route("/tarot/reading", methods=["POST"])
def reading():
    card_id = int(request.form.get("card_id", 0))
    is_upright = request.form.get("is_upright", "true").lower() == "true"
    user_problem = request.form.get("user_problem", "相手の本音")
    email = request.form.get("email", "")

    card = TAROT_DATA["cards"][card_id]
    result = generate_free(card, is_upright, user_problem)

    return render_template("tarot_result.html",
        reading=result,
        card=card,
        is_upright=is_upright,
        orient_label="正位置" if is_upright else "逆位置",
        user_problem=user_problem,
        email=email,
        card_id=card_id,
    )


@app.route("/order-tarot", methods=["POST"])
def order():
    email = request.form.get("email", "")
    card_id = request.form.get("card_id", "0")
    is_upright = request.form.get("is_upright", "true")
    user_problem = request.form.get("user_problem", "")
    card_name = TAROT_DATA["cards"][int(card_id)]["name"]

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "jpy",
                "product_data": {
                    "name": f"タロット完全鑑定｜{card_name}が示すあの人の本音と最終結末",
                    "description": "あの人の隠れた本音 + 二人の最終的な結末を完全公開",
                },
                "unit_amount": 500,
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url=request.host_url + "success?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=request.host_url,
        customer_email=email or None,
        metadata={
            "card_id": card_id,
            "is_upright": is_upright,
            "user_problem": user_problem,
            "email": email,
        },
    )
    return redirect(session.url, 303)


@app.route("/success")
def success():
    session_id = request.args.get("session_id", "")
    if session_id and session_id not in _processed:
        try:
            sess = stripe.checkout.Session.retrieve(session_id)
            if sess.payment_status == "paid":
                _processed.add(session_id)
                meta = sess.metadata
                threading.Thread(
                    target=generate_and_send,
                    args=(int(meta["card_id"]), meta["is_upright"] == "true",
                          meta["user_problem"], meta["email"]),
                    daemon=True,
                ).start()
        except stripe.error.StripeError as e:
            print(f"[ERROR] Stripe: {e}", file=sys.stderr)
    return render_template("success.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
