from flask import Flask, render_template, jsonify
import os
import time
import threading
from gpiozero import DistanceSensor, Buzzer
import board
import adafruit_dht
import busio
from PIL import Image, ImageDraw, ImageFont
import adafruit_ssd1306
from jinja2 import ChoiceLoader, FileSystemLoader
import sqlite3

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOCAL_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")

app = Flask(__name__)

app.jinja_loader = ChoiceLoader([
    FileSystemLoader(BASE_DIR),
    FileSystemLoader(LOCAL_TEMPLATES)
])

# --- Sensors ---
dhtDevice = adafruit_dht.DHT11(board.D4, use_pulseio=False)
ultrasonic1 = DistanceSensor(echo=18, trigger=17, max_distance=4)
ultrasonic2 = DistanceSensor(echo=24, trigger=23, max_distance=4)
buzzer = Buzzer(20)

# --- OLED ---
i2c = busio.I2C(board.SCL, board.SDA)
oled = adafruit_ssd1306.SSD1306_I2C(128, 64, i2c)
oled.fill(0)
oled.show()
image = Image.new("1", (oled.width, oled.height))
draw = ImageDraw.Draw(image)
font = ImageFont.load_default()

draw.rectangle((0, 0, oled.width, oled.height), fill=0)
draw.text((20, 20), "System Initializing", font=font, fill=255)
oled.image(image)
oled.show()
time.sleep(2)
oled.fill(0)
oled.show()

# --- Latest readings ---
latest_reading = {
    "temperature": None,
    "humidity": None,
    "distance1_cm": None,
    "distance2_cm": None
}
buzzer_active = False

# --- SQLite setup ---
DB_PATH = os.path.join(BASE_DIR, "act2_data.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS ultrasonic_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            distance1 REAL,
            distance2 REAL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- Functions ---
def update_oled(temp, hum, d1, d2, buzzer_on):
    draw.rectangle((0, 0, oled.width, oled.height), fill=0)
    draw.text((0, 0), f"Temp: {temp if temp else '--'} C", font=font, fill=255)
    draw.text((0, 16), f"Humidity: {hum if hum else '--'} %", font=font, fill=255)
    draw.text((0, 32), f"Sensor1: {d1:.1f} cm", font=font, fill=255)
    draw.text((0, 48), f"Sensor2: {d2:.1f} cm", font=font, fill=255)
    oled.image(image)
    oled.show()

def read_sensors():
    global latest_reading, buzzer_active
    try:
        temperature = dhtDevice.temperature
        humidity = dhtDevice.humidity
        d1 = ultrasonic1.distance * 100
        d2 = ultrasonic2.distance * 100

        if temperature is not None and humidity is not None:
            latest_reading["temperature"] = temperature
            latest_reading["humidity"] = humidity
        latest_reading["distance1_cm"] = round(d1, 2)
        latest_reading["distance2_cm"] = round(d2, 2)
        buzzer_active = d1 >= 12 or d2 >= 12
        update_oled(temperature, humidity, d1, d2, buzzer_active)

        # --- Save distances to SQLite ---
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "INSERT INTO ultrasonic_data (timestamp, distance1, distance2) VALUES (datetime('now','localtime'), ?, ?)",
            (round(d1,2), round(d2,2))
        )
        conn.commit()
        conn.close()

        print(f"[DEBUG] Temp:{temperature} C Hum:{humidity}% D1:{d1:.1f}cm D2:{d2:.1f}cm Buzzer:{buzzer_active}")
    except Exception as e:
        print("[SENSOR ERROR]", e)

# --- Sensor loop ---
def sensor_loop():
    while True:
        read_sensors()
        time.sleep(2)

def buzzer_loop():
    global buzzer_active
    while True:
        if buzzer_active:
            buzzer.on()
            time.sleep(0.3)
            buzzer.off()
            time.sleep(0.3)
        else:
            buzzer.off()
            time.sleep(0.1)

threading.Thread(target=sensor_loop, daemon=True).start()
threading.Thread(target=buzzer_loop, daemon=True).start()

# --- Flask routes ---
@app.route("/")
def homepage():
    active_activity = 2  # Update to match current active Python file
    return render_template("web/index.html", active_activity=active_activity, content='Home', css_file='css/index.css')

@app.route("/act2")
def act2_page():
    return render_template("web/act2.html")

@app.route("/act2/readings")
def act2_readings():
    return jsonify(latest_reading)

# Updated route name to match HTML url_for
@app.route("/act2/history")
def act2_history_page():
    return render_template("web/act2_history.html")

@app.route("/act2/history/data")
def act2_history_data():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT timestamp, distance1, distance2 FROM ultrasonic_data ORDER BY id DESC LIMIT 100")
    rows = c.fetchall()
    conn.close()
    rows = rows[::-1]  # chronological
    timestamps = [row[0] for row in rows]
    distance1 = [row[1] for row in rows]
    distance2 = [row[2] for row in rows]
    return jsonify({
        "timestamps": timestamps,
        "distance1": distance1,
        "distance2": distance2
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
