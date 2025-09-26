from flask import Flask, render_template, request, send_from_directory, jsonify
import os
import time
import threading
import re
import subprocess
from jinja2 import ChoiceLoader, FileSystemLoader
from gtts import gTTS
from gpiozero import Buzzer  # ✅ instead of RPi.GPIO

# --- Paths ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOCAL_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")
AUDIO_DIR = os.path.join(BASE_DIR, "static/audio")
os.makedirs(AUDIO_DIR, exist_ok=True)

# --- Flask setup ---
app = Flask(__name__)
app.jinja_loader = ChoiceLoader([
    FileSystemLoader(BASE_DIR),
    FileSystemLoader(LOCAL_TEMPLATES)
])

# --- Buzzer Setup ---
BUZZER_PIN = 21
buzzer = Buzzer(BUZZER_PIN)  # ✅ gpiozero handles Pi 5 automatically

def buzz_async(duration=1):
    """Non-blocking buzzer beep using threading"""
    def _buzz():
        buzzer.on()
        time.sleep(duration)
        buzzer.off()
    threading.Thread(target=_buzz, daemon=True).start()


# --- Bad words list ---
BAD_WORDS = ["fuck", "shit"]
pattern = re.compile(r"\b(" + "|".join(BAD_WORDS) + r")\b", re.IGNORECASE)

def contains_badword(text):
    return bool(pattern.search(text))


# --- Unified TTS Backend (Google gTTS) ---
def tts_generate(text, lang="en", filepath=None):
    """
    Generates speech using Google Text-to-Speech (gTTS).
    - text: string
    - lang: language code (e.g., 'en', 'tl', 'ja')
    - filepath: if provided, saves audio to file
    """
    try:
        tts = gTTS(text=text, lang=lang, slow=False)
        if filepath:
            tts.save(filepath)
        else:
            tmpfile = "/tmp/temp.mp3"
            tts.save(tmpfile)
            subprocess.run(["mpg123", tmpfile], check=True)
        return True
    except Exception as e:
        print(f"TTS error: {e}")
        return False


# --- Routes ---
@app.route("/")
def homepage():
    return render_template("web/index.html",
                           active_activity=8,
                           content="Home",
                           css_file="css/index.css")


@app.route("/act8")
def act8_page():
    return render_template("web/act8.html")


# 🔊 Speak text immediately
@app.route("/act8/speak")
def act8_speak():
    text = request.args.get("text", "")
    lang = request.args.get("lang", "en")

    if not text:
        return "No text provided", 400

    if contains_badword(text):
        buzz_async(0.5)

    if tts_generate(text, lang):
        return f"Spoken ({lang}): {text}"
    else:
        return "TTS error", 500


# 💾 Save spoken text to file
@app.route("/act8/save")
def act8_save():
    text = request.args.get("text", "")
    lang = request.args.get("lang", "en")

    if not text:
        return jsonify({"error": "No text provided"}), 400

    if contains_badword(text):
        buzz_async(0.5)

    filename = f"tts_{int(time.time())}.mp3"
    filepath = os.path.join(AUDIO_DIR, filename)

    if not tts_generate(text, lang, filepath=filepath):
        return jsonify({"error": "TTS error"}), 500

    return jsonify({
        "filename": filename,
        "download_url": f"/act8/download/{filename}",
        "play_url": f"/static/audio/{filename}"
    })


# 📂 List all saved audio files
@app.route("/act8/list")
def act8_list():
    files = [f for f in os.listdir(AUDIO_DIR) if f.endswith(".mp3")]
    files.sort(key=lambda x: os.path.getmtime(os.path.join(AUDIO_DIR, x)), reverse=True)
    return jsonify(files)


# 📥 Download saved audio
@app.route("/act8/download/<filename>")
def act8_download(filename):
    return send_from_directory(AUDIO_DIR, filename, as_attachment=True)


# ▶️ Play saved audio directly on server speaker
@app.route("/act8/play/<filename>")
def act8_play(filename):
    filepath = os.path.join(AUDIO_DIR, filename)
    if os.path.exists(filepath):
        threading.Thread(
            target=lambda: subprocess.run(["mpg123", filepath]),
            daemon=True
        ).start()
        return f"Playing {filename}"
    return "File not found", 404


# ❌ Delete saved audio file
@app.route("/act8/delete/<filename>", methods=["DELETE"])
def act8_delete(filename):
    filepath = os.path.join(AUDIO_DIR, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        return jsonify({"deleted": filename})
    return jsonify({"error": "File not found"}), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
