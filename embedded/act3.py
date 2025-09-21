from flask import Flask, render_template, jsonify
import time
import board
import adafruit_dht
import threading
import os
import subprocess
from datetime import datetime
from jinja2 import ChoiceLoader, FileSystemLoader
import sqlite3
from gpiozero import MotionSensor, Buzzer
import smtplib
import ssl
from email.message import EmailMessage
import base64

# Define paths
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOCAL_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")

# Flask app setup
app = Flask(__name__)
app.jinja_loader = ChoiceLoader([
    FileSystemLoader(BASE_DIR),
    FileSystemLoader(LOCAL_TEMPLATES)
])

# DHT and PIR setup
dhtDevice = adafruit_dht.DHT11(board.D4, use_pulseio=False)
pir = MotionSensor(21)
buzzer = Buzzer(17)

pir_active = False
buzzer_active = False
buzzer_thread = None

latest_reading = {"temperature": None, "humidity": None, "pir_active": False}

DB_PATH = os.path.join(BASE_DIR, "act3_data.db")

# Initialize database
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS temp_hum_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                temperature REAL,
                humidity REAL,
                pir_active INTEGER DEFAULT 0,
                image_blob BLOB
            )
        ''')
init_db()

# Send email with image (from memory, no file)
def send_email_with_image_bytes(image_bytes, image_filename):
    EMAIL_SENDER = "erifulglen@gmail.com"
    EMAIL_PASSWORD = "rmef frky kjms vjqn"
    EMAIL_RECEIVER = "Ianjameslebrino09@gmail.com"

    msg = EmailMessage()
    msg["Subject"] = "Motion Detected - Image Captured"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg.set_content("Motion has been detected. See attached image.")

    try:
        msg.add_attachment(image_bytes, maintype="image", subtype="jpeg", filename=image_filename)

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)

        print(f"[EMAIL] Sent image to {EMAIL_RECEIVER}")
    except Exception as e:
        print(f"[EMAIL ERROR] Failed to send email: {e}")

# Buzzer functions
def buzzer_beep():
    global buzzer_active
    while buzzer_active:
        buzzer.on()
        time.sleep(0.3)
        buzzer.off()
        time.sleep(0.3)

def start_buzzer():
    global buzzer_active, buzzer_thread
    if not buzzer_active:
        buzzer_active = True
        buzzer_thread = threading.Thread(target=buzzer_beep, daemon=True)
        buzzer_thread.start()
        print("[BUZZER] Started beeping")

def stop_buzzer():
    global buzzer_active
    if buzzer_active:
        buzzer_active = False
        buzzer.off()
        print("[BUZZER] Stopped beeping")

# PIR motion detection
def pir_motion_detected():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_filename = f"motion_{timestamp}.jpg"
    print("[PIR] Motion detected! Capturing image into database...")

    start_buzzer()
    image_blob = None

    try:
        # Capture image into memory (stdout instead of file)
        result = subprocess.run(
            ["rpicam-still", "-o", "-", "-t", "1000"],  # Output to stdout
            capture_output=True,
            check=True
        )
        image_blob = result.stdout

        # Send email with captured image
        send_email_with_image_bytes(image_blob, image_filename)
        print("[INFO] Image captured and stored in database")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to capture image: {e}")
    except FileNotFoundError:
        print("[ERROR] rpicam-still not found.")
    except Exception as e:
        print(f"[ERROR] Could not capture image: {e}")

    try:
        temperature = latest_reading["temperature"]
        humidity = latest_reading["humidity"]

        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO temp_hum_data (timestamp, temperature, humidity, pir_active, image_blob) VALUES (datetime('now','localtime'), ?, ?, ?, ?)",
                (temperature, humidity, 1, image_blob)
            )
            conn.commit()
            print("[DB] Data with image BLOB logged.")
    except Exception as e:
        print(f"[DB ERROR] Could not log motion data: {e}")

    threading.Timer(5.0, stop_buzzer).start()

# Read temperature and humidity
def read_sensor():
    global latest_reading, pir_active
    try:
        temperature = dhtDevice.temperature
        humidity = dhtDevice.humidity

        if temperature is not None and humidity is not None:
            latest_reading["temperature"] = temperature
            latest_reading["humidity"] = humidity

            if temperature >= 38:
                if not pir_active:
                    pir_active = True
                    pir.when_motion = pir_motion_detected
                    print("[INFO] PIR sensor activated")
            else:
                if pir_active:
                    pir_active = False
                    pir.when_motion = None
                    print("[INFO] PIR sensor deactivated")

            latest_reading["pir_active"] = pir_active

            # Log readings (without image)
            with sqlite3.connect(DB_PATH) as conn:
                c = conn.cursor()
                c.execute(
                    "INSERT INTO temp_hum_data (timestamp, temperature, humidity, pir_active) VALUES (datetime('now','localtime'), ?, ?, ?)",
                    (temperature, humidity, int(pir_active))
                )
                conn.commit()

            print(f"[DEBUG] Temp:{temperature}°C Hum:{humidity}% PIR:{pir_active}")
    except RuntimeError as e:
        print("[SENSOR ERROR]", e)

# Run sensor loop
def sensor_loop():
    while True:
        read_sensor()
        time.sleep(5)

threading.Thread(target=sensor_loop, daemon=True).start()

# ==================== Flask Routes ====================

@app.route("/")
def homepage():
    active_activity = 3
    return render_template("web/index.html", active_activity=active_activity, content='Home', css_file='css/index.css')

@app.route("/act3")
def act3_page():
    return render_template("web/act3.html")

@app.route("/act3/readings")
def act3_readings():
    data = latest_reading.copy()
    if "pir_active" not in data:
        data["pir_active"] = pir_active
    return jsonify(data)

@app.route("/act3/history")
def act3_history_page():
    return render_template("web/act3_history.html")

@app.route("/act3/history/data")
def act3_history_data():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT timestamp, temperature, humidity, pir_active, image_blob FROM temp_hum_data ORDER BY id DESC LIMIT 100")
        rows = c.fetchall()

    rows = rows[::-1]

    timestamps = []
    temperatures = []
    humidities = []
    pir_active_list = []
    images_base64 = []

    for row in rows:
        timestamps.append(row[0])
        temperatures.append(row[1])
        humidities.append(row[2])
        pir_active_list.append(row[3])

        image_blob = row[4]
        if image_blob:
            image_base64 = base64.b64encode(image_blob).decode('utf-8')
        else:
            image_base64 = None
        images_base64.append(image_base64)

    return jsonify({
        "timestamps": timestamps,
        "temperature": temperatures,
        "humidity": humidities,
        "pir_active": pir_active_list,
        "images_base64": images_base64
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
