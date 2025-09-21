from flask import Flask, render_template, jsonify
import time
import board
import adafruit_dht
import threading
from gpiozero import Buzzer
import os
from jinja2 import ChoiceLoader, FileSystemLoader
import sqlite3

# --- Paths ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))   # parent folder
LOCAL_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")      # act1/templates

# --- Initialize Flask ---
app = Flask(__name__)
app.jinja_loader = ChoiceLoader([
    FileSystemLoader(BASE_DIR),
    FileSystemLoader(LOCAL_TEMPLATES)
])

# --- Initialize Sensors ---
dhtDevice = adafruit_dht.DHT11(board.D4, use_pulseio=False)
buzzer = Buzzer(20)

# --- Latest readings ---
latest_reading = {"temperature": None, "humidity": None}
temp_buzzer_active = False  # flag for buzzer

# --- SQLite Setup ---
DB_PATH = os.path.join(BASE_DIR, "act1_data.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS temp_hum_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            temperature REAL,
            humidity REAL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- Functions ---
def read_sensor():
    global latest_reading, temp_buzzer_active
    try:
        temperature = dhtDevice.temperature
        humidity = dhtDevice.humidity

        if temperature is not None and humidity is not None:
            latest_reading["temperature"] = temperature
            latest_reading["humidity"] = humidity
            temp_buzzer_active = temperature >= 38

            # Save to SQLite
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute(
                "INSERT INTO temp_hum_data (timestamp, temperature, humidity) VALUES (datetime('now','localtime'), ?, ?)",
                (temperature, humidity)
            )
            conn.commit()
            conn.close()

            print(f"[DEBUG] Temp:{temperature} C Hum:{humidity}% Buzzer:{temp_buzzer_active}")

    except RuntimeError as e:
        print("[SENSOR ERROR]", e)

def sensor_loop():
    while True:
        read_sensor()
        time.sleep(2)

def temp_buzzer_loop():
    """Make the buzzer beep repeatedly when temperature is high"""
    global temp_buzzer_active
    while True:
        if temp_buzzer_active:
            buzzer.on()
            time.sleep(0.3)
            buzzer.off()
            time.sleep(0.3)
        else:
            buzzer.off()
            time.sleep(0.1)

# --- Start background threads ---
threading.Thread(target=sensor_loop, daemon=True).start()
threading.Thread(target=temp_buzzer_loop, daemon=True).start()

# --- Flask Routes ---
@app.route("/")
def homepage():
    active_activity = 1  # Changed to 1 as well
    return render_template("web/index.html", active_activity=active_activity, content='Home', css_file='css/index.css')

@app.route("/act1")
def act1_page():
    return render_template("web/act1.html")

@app.route("/act1/readings")
def act1_readings():
    return jsonify(latest_reading)

@app.route("/act1/history")
def act1_history_page():
    return render_template("web/act1_history.html")

@app.route("/act1/history/data")
def act1_history_data():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT timestamp, temperature, humidity FROM temp_hum_data ORDER BY id DESC LIMIT 100")
    rows = c.fetchall()
    conn.close()

    # reverse to chronological order
    rows = rows[::-1]
    timestamps = [row[0] for row in rows]
    temperature = [row[1] for row in rows]
    humidity = [row[2] for row in rows]

    return jsonify({
        "timestamps": timestamps,
        "temperature": temperature,
        "humidity": humidity
    })

# --- Main ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
