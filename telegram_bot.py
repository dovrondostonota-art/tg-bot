# -*- coding: utf-8 -*-
"""Telegram bot — OpenMontage assistant.
Long-polling, no external deps beyond requests.
Commands: /start /help /render /status /video <text>
"""
import json
import os
import subprocess
import sys
import threading
import time
import requests

# .env dan o'qish (yoki environment variables)
def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            ".env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()
TOKEN = os.environ.get("TG_TOKEN", "")
GROQ_KEY = os.environ.get("GROQ_KEY", "")
API = f"https://api.telegram.org/bot{TOKEN}"
ROOT = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable  # bulut/Linux rejim
OWNER_FILE = os.path.join(ROOT, "allowed.json")


def load_owners():
    try:
        return set(json.load(open(OWNER_FILE, encoding="utf-8")))
    except Exception:
        return set()


def save_owners(ids):
    json.dump(sorted(ids), open(OWNER_FILE, "w", encoding="utf-8"))


offset = 0


def send(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    requests.post(f"{API}/sendMessage", json=payload, timeout=30)


def send_doc(chat_id, path, caption=""):
    with open(path, "rb") as f:
        requests.post(f"{API}/sendDocument",
                      data={"chat_id": chat_id, "caption": caption},
                      files={"document": f}, timeout=120)


INBOX = os.path.join(ROOT, "assets", "inbox")
os.makedirs(INBOX, exist_ok=True)


def download_from_link(chat_id, url):
    """yt-dlp bilan YouTube/Instagram linkdan video yuklab oladi."""
    send(chat_id, "⏳ Video yuklab olinmoqda...")
    out_tmpl = os.path.join(INBOX, "dl_%(id)s.%(ext)s")
    # bir necha format variantini ketma-ket sinab ko'ramiz
    attempts = [
        ["-f", "bv*[height<=720]+ba/b[height<=720]/b",
         "--merge-output-format", "mp4"],
        ["-f", "bestvideo*+bestaudio/best", "--merge-output-format", "mp4"],
        ["-f", "best"],
    ]
    last_err = ""
    for extra in attempts:
        try:
            r = subprocess.run(
                [PY, "-m", "yt_dlp", *extra, "--no-playlist",
                 "-o", out_tmpl, url],
                capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                last_err = (r.stderr or "")[-300:]
                continue
        except subprocess.TimeoutExpired:
            send(chat_id, "❌ Yuklash 10 daqiqadan oshdi — qayta urinib ko'ring.")
            return
        except Exception as e:
            send(chat_id, f"❌ Xato: {e}")
            return
        # topilgan faylni qidiramiz
        import glob
        files = sorted(glob.glob(os.path.join(INBOX, "dl_*")),
                       key=os.path.getmtime)
        vids = [f for f in files if f.lower().endswith(
            (".mp4", ".mkv", ".webm", ".mov"))]
        if vids:
            path = vids[-1]
            send_doc(chat_id, path,
                     f"✅ Yuklandi: {os.path.basename(path)}")
            return
        last_err = "download OK lekin fayl topilmadi"
    send(chat_id, "❌ Video yuklab bo'lmadi.\n" + last_err)


def save_tg_file(chat_id, msg):
    """Foydalanuvchi yuborgan video/faylni saqlaydi."""
    obj = msg.get("video") or msg.get("document")
    if not obj:
        return False
    file_id = obj["file_id"]
    fname = obj.get("file_name") or f"file_{file_id[-8:]}.mp4"
    try:
        info = requests.get(f"{API}/getFile",
                            params={"file_id": file_id}, timeout=30).json()
        fpath = info["result"]["file_path"]
        r = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{fpath}",
                         timeout=300)
        dest = os.path.join(INBOX, fname)
        with open(dest, "wb") as f:
            f.write(r.content)
        mb = os.path.getsize(dest) / 1e6
        send(chat_id, f"💾 Saqlandi: {fname} ({mb:.1f} MB)\n"
                      f"Joy: assets/inbox/{fname}")
        return True
    except Exception as e:
        send(chat_id, f"❌ Fayl saqlanmadi: {e}")
        return False


def kb(*rows):
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in r]
                                for r in rows]}


def run_render(chat_id):
    if not os.path.exists(os.path.join(ROOT, "autopilot.json")):
        send(chat_id, "❌ Render bu rejimda mavjud emas — "
                      "u sizning noutbukda ishlaydi.")
        return
    try:
        r = subprocess.run([PY, "autopilot.py"], cwd=ROOT,
                           capture_output=True, text=True, timeout=1800)
        out = os.path.join(ROOT, "renders", "final.mp4")
        if os.path.exists(out):
            send_doc(chat_id, out, "✅ Video tayyor — renders/final.mp4")
        else:
            send(chat_id, "❌ Render xatosi:\n" + (r.stderr or r.stdout)[-800:])
    except Exception as e:
        send(chat_id, f"❌ Xato: {e}")


def handle(cb):
    data = cb.get("data", "")
    chat_id = cb["message"]["chat"]["id"]
    if data == "render":
        send(chat_id, "⏳ Render boshlanmoqda...")
        threading.Thread(target=run_render, args=(chat_id,), daemon=True).start()
    elif data == "status":
        out = os.path.join(ROOT, "renders", "final.mp4")
        if os.path.exists(out):
            send(chat_id, f"📹 Oxirgi video: {os.path.getsize(out)//1024//1024} MB")
        else:
            send(chat_id, "Hali video yo'q.")
    elif data == "help":
        send(chat_id, HELP)


HELP = """<b>OpenMontage Bot</b> 🤖

<b>Buyruqlar:</b>
/start — salom
/help — bu yordam
/render — oxirgi ssenariyni qayta render qilish
/status — video holati
/say &lt;matn&gt; — ayol ovozida audio javob
/saym &lt;matn&gt; — erkak ovozida audio javob
/voice on|off — har javobni ovozli qilish

<b>Qo'shimcha:</b>
🎙 Ovozli xabar yuboring — bot tinglaydi va javob beradi
🔗 YouTube/Instagram/TikTok linki — video yuklab oladi
📎 Video fayl — saqlaydi

Oddiy suhbat ham mumkin — istalgan mavzu."""


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"

SYSTEM = ("Sen 'Devin' ismli AI yordamchisan — OpenMontage video pipeline "
          "bilan ishlaydigan o'zbekcha Telegram bot. Qisqa, do'stona javob "
          "ber. Foydalanuvchi o'zbek tilida yozadi.\n\n"
          "SENING IMKONIYATLARING:\n"
          "- Oddiy suhbat (har qanday mavzu)\n"
          "- VIDEO YUKLAB OLISH: foydalanuvchi YouTube, Instagram, TikTok, "
          "Vimeo, Twitch linkini yuborsa — bot yt-dlp bilan yuklab oladi "
          "va video qaytaradi. Agar video yuklash haqida so'rasa — "
          "'YouTube/Instagram linkini yuboring, men yuklab olaman' deb "
          "javob ber.\n"
          "- VIDEO FAYL QABUL QILISH: foydalanuvchi video/document "
          "yuborsa — bot assets/inbox ga saqlaydi.\n"
          "- /render — oxirgi video ssenariyni qayta render qiladi\n"
          "- /status — video holati\n"
          "- /help — buyruqlar\n"
          "- OVOZ: foydalanuvchi OVOZLI xabar yuborsa — bot uni tinglaydi "
          "(Whisper) va javob beradi.\n"
          "- MATNNI OVOZGA: /say <matn> — ayol ovozida audio, "
          "/saym <matn> — erkak ovozida audio javob yuboradi.\n"
          "- /voice on — har matn javobiga audio ham qo'shiladi; "
          "/voice off — o'chiradi.\n"
          "Agar foydalanuvchi 'matnni ovozga aylantira olasanmi' deb "
          "so'rsa — '/say buyrug'i bilan ayta olaman, yoki /voice on "
          "qiling' deb javob ber.\n\n"
          "Agar foydalanuvchi video haqida gapirsa — bu imkoniyatlarni "
          "eslat. JavaScript/veb-sayt yaratish, dasturlash, tarjima — "
          "bularni ham bilasan.")

# chat_id -> {"user": str, "history": [{"role","content"}, ...]}
CHATS = {}
HISTORY_MAX = 12


def transcribe_voice(chat_id, msg):
    """Voice message -> Groq Whisper -> matn."""
    obj = msg.get("voice") or msg.get("audio")
    if not obj:
        return None
    try:
        info = requests.get(f"{API}/getFile",
                            params={"file_id": obj["file_id"]}, timeout=30).json()
        fpath = info["result"]["file_path"]
        audio = requests.get(
            f"https://api.telegram.org/file/bot{TOKEN}/{fpath}",
            timeout=120).content
        tmp = os.path.join(INBOX, "voice.ogg")
        with open(tmp, "wb") as f:
            f.write(audio)
        with open(tmp, "rb") as f:
            r = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_KEY}"},
                files={"file": ("voice.ogg", f, "audio/ogg")},
                data={"model": "whisper-large-v3",
                      "language": "uz"}, timeout=120)
        txt = r.json().get("text", "").strip()
        return txt or None
    except Exception as e:
        print("whisper xato:", e)
        return None


VOICE_STATE = {}  # chat_id -> bool (auto voice reply)


def tts_send(chat_id, text, voice="uz-UZ-MadinaNeural"):
    """edge-tts bilan matnni ovozga aylantirib audio yuboradi."""
    import asyncio
    tmp = os.path.join(INBOX, "tts_tmp.mp3")
    async def gen():
        import edge_tts
        com = edge_tts.Communicate(text, voice)
        await com.save(tmp)
    try:
        asyncio.run(gen())
        with open(tmp, "rb") as f:
            requests.post(f"{API}/sendAudio",
                          data={"chat_id": chat_id,
                                "title": "Devin",
                                "performer": voice},
                          files={"audio": ("reply.mp3", f, "audio/mpeg")},
                          timeout=120)
    except Exception as e:
        send(chat_id, f"❌ TTS xatosi: {e}")


def llm_reply(text, chat_id):
    """Groq API — fast free tier, with per-chat memory."""
    chat = CHATS.setdefault(chat_id, {"user": "", "history": []})
    sys_msg = SYSTEM
    if chat["user"]:
        sys_msg += f" Foydalanuvchi: {chat['user']}."
    messages = [{"role": "system", "content": sys_msg}]
    messages += chat["history"]
    messages.append({"role": "user", "content": text})
    try:
        r = requests.post(GROQ_URL, headers={
            "Authorization": f"Bearer {GROQ_KEY}",
            "Content-Type": "application/json",
        }, json={
            "model": GROQ_MODEL,
            "messages": messages,
            "max_tokens": 800,
            "temperature": 0.7,
        }, timeout=60)
        data = r.json()
        if "choices" in data:
            ans = data["choices"][0]["message"]["content"].strip()
            chat["history"] += [{"role": "user", "content": text},
                                {"role": "assistant", "content": ans}]
            chat["history"] = chat["history"][-HISTORY_MAX:]
            return ans[:4000]
        return f"Groq xatosi: {str(data)[:300]}"
    except Exception as e:
        return f"LLM xatosi: {e}"


def main():
    global offset
    print("Bot ishga tushdi...")
    me = requests.get(f"{API}/getMe", timeout=30).json()
    print("Bot:", me.get("result", {}).get("username"))
    while True:
        try:
            r = requests.get(f"{API}/getUpdates",
                             params={"offset": offset, "timeout": 30},
                             timeout=60).json()
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                if "callback_query" in upd:
                    handle(upd["callback_query"])
                    continue
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                chat_id = msg["chat"]["id"]
                text = (msg.get("text") or "").strip()

                # ovozli xabar — Whisper bilan matnga aylantiramiz
                if not text and (msg.get("voice") or msg.get("audio")):
                    send(chat_id, "🎙 Ovoz qabul qilindi — tinglanyapti...")
                    text = transcribe_voice(chat_id, msg) or ""
                    if not text:
                        send(chat_id, "❌ Ovozni tushunmadim — qayta ayting.")
                        continue
                    text = "[ovozli xabar]: " + text

                if not text and not msg.get("video") \
                        and not msg.get("document"):
                    continue

                # video/fayl yuborilgan bo'lsa — saqlash
                if msg.get("video") or msg.get("document"):
                    save_tg_file(chat_id, msg)
                    continue

                # link yuborilgan bo'lsa — yt-dlp bilan yuklab olish
                import re as _re
                url_m = _re.search(r"https?://\S+", text)
                if url_m and any(d in url_m.group(0) for d in
                                 ("youtube", "youtu.be", "instagram",
                                  "tiktok", "vimeo", "twitch")):
                    url = url_m.group(0).rstrip(")>.,;")
                    send(chat_id, "🔗 Link qabul qilindi — yuklab olinmoqda...")
                    threading.Thread(target=download_from_link,
                                     args=(chat_id, url),
                                     daemon=True).start()
                    continue

                if text.startswith("/start"):
                    send(chat_id,
                         "Assalomu alaykum! 👋 OpenMontage video botiga xush kelibsiz.\n"
                         "Yuklab oling: /help",
                         reply_markup=kb([("🎬 Render", "render"),
                                         ("📊 Status", "status"),
                                         ("❓ Yordam", "help")]))
                elif text.startswith("/help"):
                    send(chat_id, HELP,
                         reply_markup=kb([("🎬 Render", "render"),
                                         ("📊 Status", "status")]))
                elif text.startswith("/render"):
                    send(chat_id, "⏳ Render boshlanmoqda...")
                    threading.Thread(target=run_render,
                                     args=(chat_id,), daemon=True).start()
                elif text.startswith("/status"):
                    out = os.path.join(ROOT, "renders", "final.mp4")
                    if os.path.exists(out):
                        mb = os.path.getsize(out) / 1e6
                        send(chat_id, f"📹 Oxirgi video tayyor: <b>{mb:.1f} MB</b>")
                    else:
                        send(chat_id, "Hali video yo'q — /render bosing.")
                elif text.startswith("/video"):
                    send(chat_id, "🚧 /video buyruq tez orada — hozircha "
                                  "/render bilan oxirgi ssenariyni qayta "
                                  "chiqarishingiz mumkin.")
                elif text.startswith("/say"):
                    say_text = text[4:].strip()
                    if say_text:
                        send(chat_id, "🔊 Ovozga aylantirilmoqda...")
                        threading.Thread(
                            target=tts_send,
                            args=(chat_id, say_text,
                                  "uz-UZ-MadinaNeural"),
                            daemon=True).start()
                    else:
                        send(chat_id, "Foydalanish: /say <matn> — ayol ovozida.\n"
                                      "/saym <matn> — erkak ovozida.")
                elif text.startswith("/saym"):
                    say_text = text[5:].strip()
                    if say_text:
                        send(chat_id, "🔊 Ovozga aylantirilmoqda...")
                        threading.Thread(
                            target=tts_send,
                            args=(chat_id, say_text,
                                  "uz-UZ-SardorNeural"),
                            daemon=True).start()
                    else:
                        send(chat_id, "Foydalanish: /saym <matn> — erkak ovozida.")
                elif text.startswith("/voice"):
                    arg = text[6:].strip().lower()
                    if arg in ("on", "yoq"):
                        VOICE_STATE[chat_id] = True
                        send(chat_id, "🔊 Ovozli javoblar yoqildi — "
                                      "har javob audio bo'lib keladi.")
                    elif arg in ("off", "o'chir"):
                        VOICE_STATE[chat_id] = False
                        send(chat_id, "🔇 Ovozli javoblar o'chirildi.")
                    else:
                        state = "yoqilgan" if VOICE_STATE.get(chat_id) else "o'chirilgan"
                        send(chat_id, f"Ovozli javoblar: {state}.\n"
                                      "/voice on — yoqish, /voice off — o'chirish")
                else:
                    # foydalanuvchi profilini eslab qolamiz
                    frm = msg.get("from") or {}
                    uname = frm.get("username")
                    fname = " ".join(filter(None, [
                        frm.get("first_name"), frm.get("last_name")]))
                    label = fname or uname or str(chat_id)
                    if uname:
                        label += f" (@{uname})"
                    CHATS.setdefault(chat_id,
                                      {"user": "", "history": []})
                    CHATS[chat_id]["user"] = label
                    requests.post(f"{API}/sendChatAction",
                                  json={"chat_id": chat_id,
                                        "action": "typing"}, timeout=10)
                    ans = llm_reply(text, chat_id)
                    # oddiy matn — parse_mode ishlatmaymiz
                    requests.post(f"{API}/sendMessage",
                                  json={"chat_id": chat_id,
                                        "text": ans[:4000]}, timeout=30)
                    # ovozli javob yoqilgan bo'lsa — audio ham yubor
                    if VOICE_STATE.get(chat_id, False) and len(ans) < 1500:
                        threading.Thread(
                            target=tts_send, args=(chat_id, ans[:800],
                                                   "uz-UZ-MadinaNeural"),
                            daemon=True).start()
        except Exception as e:
            print("poll xato:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
