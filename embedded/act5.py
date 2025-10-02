from flask import Flask, render_template, jsonify
import os
from jinja2 import ChoiceLoader, FileSystemLoader
import sqlite3
import threading
import serial
import time
import re
from gpiozero import Buzzer  # ✅ for buzzer
import adafruit_dht
import board

# --- Paths ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOCAL_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")

# --- Initialize Flask ---
app = Flask(__name__)
app.jinja_loader = ChoiceLoader([
    FileSystemLoader(BASE_DIR),
    FileSystemLoader(LOCAL_TEMPLATES)
])

# --- SQLite Setup ---
DB_PATH = os.path.join(BASE_DIR, "act5_data.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS act5_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            sound_analog INTEGER,
            sound_digital INTEGER,
            rain_analog INTEGER,
            rain_digital INTEGER,
            temperature REAL,
            humidity REAL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- Buzzer Setup ---
buzzer = Buzzer(21)   # GPIO 21

# --- DHT11 Setup ---
dht_sensor = adafruit_dht.DHT11(board.D4)  # GPIO 4

# --- Sensor logic config ---
ACTIVE_LOW = True  # ✅ Set True if sensors use LOW=detected, HIGH=idle

# --- Arduino Serial Config ---
SERIAL_PORT = "/dev/ttyACM0"   # Change if needed (Windows: COM3, etc.)
BAUD_RATE = 9600

def read_from_arduino():
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        time.sleep(2)  # wait for Arduino reset
        ser.reset_input_buffer()
        print("[INFO] Connected to Arduino on", SERIAL_PORT)

        while True:
            try:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue

                print("[RAW]", line)

                match = re.match(
                    r"Sound -> Analog:\s*(\d+)\s*\|\s*Digital:\s*(\d+)\s*\|\|\s*Rain -> Analog:\s*(\d+)\s*\|\s*Digital:\s*(\d+)",
                    line
                )
                if match:
                    sound_analog = int(match.group(1))
                    sound_digital = int(match.group(2))
                    rain_analog  = int(match.group(3))
                    rain_digital = int(match.group(4))

                    print(f"[DEBUG] Sound=({sound_analog},{sound_digital}), Rain=({rain_analog},{rain_digital})")

                    # --- Buzzer logic ---
                    if ACTIVE_LOW:
                        detected = (sound_digital == 0 or rain_digital == 0)
                    else:
                        detected = (sound_digital == 1 or rain_digital == 1)

                    if detected:
                        buzzer.on()
                        print("[BUZZER] ALERT! Sound or Rain detected")
                    else:
                        buzzer.off()

                    # --- Read DHT11 ---
                    try:
                        temperature = dht_sensor.temperature
                        humidity = dht_sensor.humidity
                        print(f"[DHT11] Temp={temperature}°C  Humidity={humidity}%")
                    except Exception as dht_e:
                        temperature = None
                        humidity = None
                        print("[WARN] DHT11 read failed:", dht_e)

                    # --- Save to DB only if temperature & humidity are valid ---
                    if temperature is not None and humidity is not None:
                        conn = sqlite3.connect(DB_PATH)
                        c = conn.cursor()
                        c.execute(
                            "INSERT INTO act5_data (timestamp, sound_analog, sound_digital, rain_analog, rain_digital, temperature, humidity) "
                            "VALUES (datetime('now','localtime'), ?, ?, ?, ?, ?, ?)",
                            (sound_analog, sound_digital, rain_analog, rain_digital, temperature, humidity)
                        )
                        conn.commit()
                        conn.close()
                    else:
                        print("[INFO] Skipping DB insert due to invalid DHT11 reading")

            except Exception as inner_e:
                print("[WARN] Failed to parse line:", inner_e)

    except Exception as e:
        print("[ERROR] Arduino connection failed:", e)
    finally:
        buzzer.off()  # safety off on exit

# Start background thread
threading.Thread(target=read_from_arduino, daemon=True).start()

# --- Flask Routes ---
@app.route("/")
def homepage():
    active_activity = 5
    return render_template("web/index.html", active_activity=active_activity, content='Home', css_file='css/index.css')

@app.route("/act5")
def act5_page():
    return render_template("web/act5.html")

@app.route("/act5/history")
def act5_history_page():
    return render_template("web/act5_history.html")

@app.route("/act5/data")
def act5_data():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT timestamp, sound_analog, sound_digital, rain_analog, rain_digital, temperature, humidity FROM act5_data ORDER BY id DESC LIMIT 100")
    rows = c.fetchall()
    conn.close()

    rows = rows[::-1]  # chronological order

    return jsonify({
        "timestamps": [row[0] for row in rows],
        "sound_analog": [row[1] for row in rows],
        "sound_digital": [row[2] for row in rows],
        "rain_analog": [row[3] for row in rows],
        "rain_digital": [row[4] for row in rows],
        "temperature": [row[5] for row in rows],
        "humidity": [row[6] for row in rows]
    })

@app.route("/act5/history/data")
def act5_history_data():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT timestamp, sound_analog, sound_digital, rain_analog, rain_digital, temperature, humidity FROM act5_data ORDER BY id DESC LIMIT 100")
    rows = c.fetchall()
    conn.close()

    rows = rows[::-1]

    return jsonify({
        "timestamps": [row[0] for row in rows],
        "sound_analog": [row[1] for row in rows],
        "sound_digital": [row[2] for row in rows],
        "rain_analog": [row[3] for row in rows],
        "rain_digital": [row[4] for row in rows],
        "temperature": [row[5] for row in rows],
        "humidity": [row[6] for row in rows]
    })

# --- Main ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
