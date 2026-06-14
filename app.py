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
from flask import Flask, jsonify, render_template, request
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

    user_msg = (
        f"【引いたカード】{card['name']}（{card['name_en']}） / {orient}\n"
        f"【悩みカテゴリー】{user_problem}（{prob_desc}）\n"
        f"【カードの意味 — {orient}】\n"
        f"- キーワード：{', '.join(meaning['keywords'])}\n"
        f"- 恋愛での意味：{meaning['love']}\n"
        f"- 相手の本音：{meaning['truth']}\n"
        f"- 未来：{meaning['future']}\n\n"
        "上記の情報をもとに、指定の2部構成（合計3,000文字以上）で鑑定文を出力してください。"
    )

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    result = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=5000,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_msg}],
    )
    full_text = result.content[0].text

    if PAID_MARKER in full_text:
        parts = full_text.split(PAID_MARKER, 1)
        return parts[0].strip(), parts[1].strip()

    # Fallback: split at nearest newline after midpoint
    mid = len(full_text) // 2
    for i in range(mid, len(full_text)):
        if full_text[i] == "\n":
            return full_text[:i].strip(), full_text[i:].strip()
    return full_text[:mid].strip(), full_text[mid:].strip()


def build_email_html(card: dict, is_upright: bool, user_problem: str, paid: str) -> str:
    orient = "正位置" if is_upright else "逆位置"
    body = ""
    for line in paid.split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("【") and "】" in s:
            body += f'<h2 style="color:#c9a84c;margin:28px 0 10px;border-left:3px solid #7c3aed;padding-left:12px;">{s}</h2>\n'
        else:
            body += f'<p style="margin:0 0 1em;line-height:2.1;">{s}</p>\n'

    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#030308;font-family:'Hiragino Mincho ProN',Georgia,serif;">
<div style="max-width:640px;margin:0 auto;padding:40px 20px;">
  <div style="text-align:center;margin-bottom:32px;padding-bottom:24px;border-bottom:1px solid rgba(201,168,76,.2);">
    <div style="font-size:2rem;margin-bottom:12px;">🔮</div>
    <h1 style="color:#c9a84c;font-size:1.4em;font-weight:normal;letter-spacing:0.15em;margin:0 0 8px;">神楽タロット 完全鑑定書</h1>
    <p style="color:#b090d0;font-size:0.82em;margin:0;">{card['name']} / {orient} — {user_problem}</p>
  </div>
  <div style="color:#ede0c4;line-height:2.1;font-size:0.97em;">{body}</div>
  <div style="text-align:center;margin-top:48px;padding-top:24px;border-top:1px solid rgba(201,168,76,.2);color:#b090d0;font-size:0.8em;line-height:2.0;">
    タロット占い師 神楽（かぐら）
  </div>
</div></body></html>"""


def send_reading_email(email: str, card: dict, is_upright: bool,
                       user_problem: str, paid: str):
    try:
        html = build_email_html(card, is_upright, user_problem, paid)
        orient = "正位置" if is_upright else "逆位置"
        from_addr = os.environ.get("FROM_EMAIL", os.environ.get("SMTP_USER", ""))

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🔮【完全鑑定書】{card['name']}が示す「あの人の本音と二人の最終関係」"
        msg["From"] = f"タロット占い師 神楽 <{from_addr}>"
        msg["To"] = email
        msg.attach(MIMEText(html, "html", "utf-8"))

        smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.starttls()
            s.login(os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASS", ""))
            s.sendmail(from_addr, email, msg.as_string())
        print(f"[OK] 送信完了 → {email}", flush=True)
    except Exception as e:
        print(f"[ERROR] send_reading_email: {e}", file=sys.stderr, flush=True)


# ── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
@app.route("/tarot")
def index():
    return render_template("tarot.html", cards_json=json.dumps(TAROT_DATA["cards"]))


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "has_anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "has_smtp": bool(os.environ.get("SMTP_HOST")),
        "cards": len(TAROT_DATA["cards"]),
    })


@app.route("/api/reading", methods=["POST"])
def api_reading():
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return jsonify({"ok": False,
                            "error": "ANTHROPIC_API_KEY が設定されていません。Renderの環境変数を確認してください。"}), 500

        card_id = int(request.form.get("card_id", 0))
        is_upright = request.form.get("is_upright", "true").lower() == "true"
        user_problem = request.form.get("user_problem", "復縁")

        cards = TAROT_DATA["cards"]
        if card_id < 0 or card_id >= len(cards):
            return jsonify({"ok": False, "error": f"無効なcard_id: {card_id}"}), 400

        card = cards[card_id]
        free_part, paid_part = generate_reading(card, is_upright, user_problem)

        if len(_readings) > 300:
            oldest = next(iter(_readings))
            del _readings[oldest]

        token = str(uuid.uuid4())
        _readings[token] = {
            "paid": paid_part,
            "card": card,
            "is_upright": is_upright,
            "user_problem": user_problem,
        }

        return jsonify({
            "ok": True,
            "free": free_part,
            "token": token,
            "card": {
                "name": card["name"],
                "name_en": card["name_en"],
                "image_url": card["image_url"],
            },
            "is_upright": is_upright,
            "orient_label": "正位置" if is_upright else "逆位置",
            "user_problem": user_problem,
        })

    except anthropic.AuthenticationError:
        return jsonify({"ok": False,
                        "error": "APIキーが無効です。Renderの環境変数 ANTHROPIC_API_KEY を確認してください。"}), 500
    except anthropic.RateLimitError:
        return jsonify({"ok": False,
                        "error": "APIのレート制限に達しました。少し待ってから再試行してください。"}), 429
    except Exception as e:
        print(f"[ERROR] api_reading: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return jsonify({"ok": False, "error": f"サーバーエラー: {str(e)}"}), 500


@app.route("/api/register", methods=["POST"])
def api_register():
    try:
        email = request.form.get("email", "").strip()
        token = request.form.get("token", "")

        if not email:
            return jsonify({"ok": False, "error": "メールアドレスを入力してください。"}), 400

        data = _readings.pop(token, None)
        if data:
            threading.Thread(
                target=send_reading_email,
                args=(email, data["card"], data["is_upright"],
                      data["user_problem"], data["paid"]),
                daemon=True,
            ).start()

        return jsonify({"ok": True})
    except Exception as e:
        print(f"[ERROR] api_register: {e}", file=sys.stderr, flush=True)
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
