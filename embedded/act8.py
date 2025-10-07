from flask import Flask, render_template, jsonify, request, send_from_directory, url_for
import os, signal, sys, atexit, subprocess, json
from gtts import gTTS
import speech_recognition as sr
from langdetect import detect
from googletrans import Translator
from jinja2 import ChoiceLoader, FileSystemLoader
from gpiozero import Buzzer
from time import sleep
from datetime import datetime

# ------------------------------
# PATH SETUP
# ------------------------------
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOCAL_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")
AUDIO_DIR = os.path.expanduser("~/Documents/embedded/audio")
os.makedirs(AUDIO_DIR, exist_ok=True)
SAVED_JSON = os.path.join(AUDIO_DIR, "saved_audios.json")

app = Flask(__name__)
app.jinja_loader = ChoiceLoader([FileSystemLoader(BASE_DIR), FileSystemLoader(LOCAL_TEMPLATES)])

latest_reading = {"temperature": None, "humidity": None}
translator = Translator()

# ------------------------------
# GPIO SETUP
# ------------------------------
buzzer = Buzzer(21)

def buzz_alert(duration=0.5, repeat=2):
    """Buzz pattern for bad words."""
    for _ in range(repeat):
        buzzer.on()
        sleep(duration)
        buzzer.off()
        sleep(0.2)

# ------------------------------
# CLEANUP HANDLING
# ------------------------------
def cleanup():
    print("[CLEANUP] Releasing resources...")
    buzzer.off()

def handle_signal(sig, frame):
    cleanup()
    sys.exit(0)

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)
atexit.register(cleanup)

# ------------------------------
# UTILITIES
# ------------------------------
def tts_generate(text, lang="en", filename="translated.mp3"):
    try:
        filepath = os.path.join(AUDIO_DIR, filename)
        tts = gTTS(text=text, lang=lang)
        tts.save(filepath)
        print(f"[TTS SAVED] {filepath}")
        return filepath
    except Exception as e:
        print("[TTS ERROR]", e)
        return None

def tts_play(filepath):
    try:
        subprocess.run(["mpg123", "-q", filepath], check=True)
    except Exception as e:
        print("[PLAY ERROR]", e)

def load_saved_audios():
    if os.path.exists(SAVED_JSON):
        with open(SAVED_JSON, "r") as f:
            return json.load(f)
    return []

def save_audio_metadata(entry):
    audios = load_saved_audios()
    audios.append(entry)
    with open(SAVED_JSON, "w") as f:
        json.dump(audios, f, indent=2)

# ------------------------------
# BAD WORD DETECTOR
# ------------------------------
BAD_WORDS = ["badword1", "badword2", "fuck", "shit", "bitch", "asshole"]

def contains_bad_word(text):
    text_lower = text.lower()
    return any(word in text_lower for word in BAD_WORDS)

# ------------------------------
# TRANSLATE & SPEAK
# ------------------------------
@app.route("/act8/translate")
def act8_translate():
    text = request.args.get("text", "")
    target = request.args.get("target", "en")
    save_audio = request.args.get("save", "false").lower() == "true"

    if not text:
        return jsonify({"status": "error", "message": "No text provided"})

    try:
        detected_lang = detect(text)
        translation = translator.translate(text, src=detected_lang, dest=target)
        translated_text = translation.text

        if contains_bad_word(text):
            print("[WARNING] Bad word detected in text!")
            buzz_alert()

        filename = f"translated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3" if save_audio else "translated.mp3"
        filepath = tts_generate(translated_text, lang=target, filename=filename)

        if filepath:
            tts_play(filepath)
            audio_url = url_for('serve_audio', filename=filename)

            if save_audio:
                save_audio_metadata({
                    "filename": filename,
                    "text": translated_text,
                    "language": target,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })

            return jsonify({
                "status": "success",
                "detected_language": detected_lang,
                "translated_text": translated_text,
                "target_language": target,
                "audio_url": audio_url,
                "saved": save_audio
            })

        return jsonify({"status": "error", "message": "TTS generation failed"})

    except Exception as e:
        print("[TRANSLATE ERROR]", e)
        return jsonify({"status": "error", "message": str(e)})

# ------------------------------
# VOICE TO SPEECH
# ------------------------------
@app.route("/act8/listen")
def act8_listen():
    recognizer = sr.Recognizer()
    mic = sr.Microphone()

    try:
        with mic as source:
            recognizer.adjust_for_ambient_noise(source)
            print("Listening... Speak now.")
            audio = recognizer.listen(source, timeout=5)

        text = recognizer.recognize_google(audio)
        detected_lang = detect(text)

        if contains_bad_word(text):
            print("[WARNING] Bad word detected from speech!")
            buzz_alert()

        filename = "voice_temp.mp3"
        filepath = tts_generate(text, lang=detected_lang, filename=filename)

        if filepath:
            tts_play(filepath)
            audio_url = url_for('serve_audio', filename=filename)
            return jsonify({
                "status": "success",
                "recognized_text": text,
                "detected_language": detected_lang,
                "audio_url": audio_url
            })

        return jsonify({"status": "error", "message": "Failed to generate voice output"})

    except sr.UnknownValueError:
        return jsonify({"status": "error", "message": "Could not understand audio"})
    except sr.RequestError:
        return jsonify({"status": "error", "message": "Speech recognition service unavailable"})
    except Exception as e:
        print("[VTS ERROR]", e)
        return jsonify({"status": "error", "message": str(e)})

# ------------------------------
# CHATBOT SPEECH
# ------------------------------
@app.route("/act8/speak")
def bot_speak():
    text = request.args.get("text", "")
    if not text:
        return jsonify({"status": "error", "message": "No text provided"})

    filename = "bot_speech.mp3"
    filepath = tts_generate(text, lang="en", filename=filename)
    if filepath:
        tts_play(filepath)
        audio_url = url_for('serve_audio', filename=filename)
        return jsonify({"status": "success", "audio_url": audio_url})

    return jsonify({"status": "error", "message": "TTS generation failed"})

# ------------------------------
# LOAD SAVED AUDIO LIST
# ------------------------------
@app.route("/act8/saved_audios")
def get_saved_audios():
    audios = load_saved_audios()
    return jsonify({"status": "success", "audios": audios})

# ------------------------------
# AUDIO FILE SERVE ENDPOINT
# ------------------------------
@app.route("/audio/<path:filename>")
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)

# ------------------------------
# ROUTES
# ------------------------------
@app.route("/")
def homepage():
    return render_template("web/index.html", active_activity=8, content='Home', css_file='css/index.css')

@app.route("/act8")
def act8_page():
    return render_template("web/act8.html")

@app.route("/act8/readings")
def act8_readings():
    return jsonify(latest_reading)

# ------------------------------
# MAIN
# ------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
