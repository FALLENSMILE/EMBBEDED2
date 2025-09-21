from flask import Flask, render_template, jsonify
import time
import threading
from gpiozero import Buzzer, DigitalInputDevice, LED
import os
from jinja2 import ChoiceLoader, FileSystemLoader
import sqlite3
import serial
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- Setup paths ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOCAL_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")

# --- Flask app ---
app = Flask(__name__)
app.jinja_loader = ChoiceLoader([
    FileSystemLoader(BASE_DIR),
    FileSystemLoader(LOCAL_TEMPLATES)
])

# --- Hardware setup ---
vibration_sensor = DigitalInputDevice(21)
buzzer = Buzzer(20)
led = LED(16)

# --- Serial setup for Arduino MQ-2 sensor ---
try:
    ser = serial.Serial('/dev/ttyACM0', 9600, timeout=1)
except serial.SerialException:
    ser = None
    print("[ERROR] Could not open serial port for Arduino")

# --- Global variables ---
latest_reading = {"vibration": 0, "mq2": 0}
vibration_active = False

# --- Email configuration ---
EMAIL_ADDRESS = "erifulglen@gmail.com"
EMAIL_PASSWORD = "rmef frky kjms vjqn"
EMAIL_RECEIVER = "2022-200837@rtu.edu.ph"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# --- Thresholds ---
GAS_THRESHOLD = 400  # Adjust based on environment

# --- Database path and init ---
DB_PATH = os.path.join(BASE_DIR, "act4_data.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS vibration_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            vibration INTEGER
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS mq2_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            mq2_value INTEGER
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- Email alert function ---
def send_email_alert(subject, body):
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_ADDRESS
        msg['To'] = EMAIL_RECEIVER
        msg['Subject'] = subject

        msg.attach(MIMEText(body, 'plain'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)

        print(f"[EMAIL] Sent alert: {subject}")
    except Exception as e:
        print(f"[ERROR] Failed to send email: {e}")

# --- Functions ---
def read_sensor():
    global latest_reading, vibration_active
    vibration = 1 if vibration_sensor.value else 0
    latest_reading["vibration"] = vibration
    vibration_active = vibration == 1

    # Email alert on vibration
    if vibration_active and not getattr(read_sensor, 'alert_sent', False):
        send_email_alert(
            "Vibration Alert Detected!",
            "Vibration has been detected by the sensor!"
        )
        read_sensor.alert_sent = True
    elif not vibration_active:
        read_sensor.alert_sent = False

    # LED indicator
    if vibration_active:
        led.on()
    else:
        led.off()

    # Save to DB
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO vibration_data (timestamp, vibration) VALUES (datetime('now','localtime'), ?)",
        (vibration,)
    )
    conn.commit()
    conn.close()

    print(f"[DEBUG] Vibration detected: {vibration} Buzzer active: {vibration_active}")

def sensor_loop():
    while True:
        read_sensor()
        time.sleep(2)

def buzzer_loop():
    global vibration_active
    while True:
        if vibration_active:
            buzzer.on()
            time.sleep(0.3)
            buzzer.off()
            time.sleep(0.3)
        else:
            buzzer.off()
            time.sleep(0.1)

def read_mq2_sensor():
    global latest_reading, ser
    if not ser:
        print("[WARN] Serial port not available for MQ-2 sensor")
        return
    while True:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8').rstrip()
                if line.isdigit():
                    mq2_val = int(line)
                    latest_reading["mq2"] = mq2_val

                    # Save to DB
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute(
                        "INSERT INTO mq2_data (timestamp, mq2_value) VALUES (datetime('now','localtime'), ?)",
                        (mq2_val,)
                    )
                    conn.commit()
                    conn.close()

                    print(f"[DEBUG] MQ-2 Gas Sensor Value: {mq2_val}")

                    # Email alert on gas threshold breach
                    if mq2_val > GAS_THRESHOLD and not getattr(read_mq2_sensor, 'alert_sent', False):
                        send_email_alert(
                            "Gas Concentration Alert!",
                            f"Gas concentration exceeded threshold! Value: {mq2_val}"
                        )
                        read_mq2_sensor.alert_sent = True
                    elif mq2_val <= GAS_THRESHOLD:
                        read_mq2_sensor.alert_sent = False

            time.sleep(1)
        except Exception as e:
            print(f"[ERROR] Error reading MQ-2 sensor: {e}")
            time.sleep(5)

# --- Start threads ---
threading.Thread(target=sensor_loop, daemon=True).start()
threading.Thread(target=buzzer_loop, daemon=True).start()
threading.Thread(target=read_mq2_sensor, daemon=True).start()

# --- Flask routes ---
@app.route("/")
def homepage():
    active_activity = 4
    return render_template("web/index.html", active_activity=active_activity, content='Home', css_file='css/index.css')

@app.route("/act4")
def act4_page():
    return render_template("web/act4.html")

@app.route("/act4/readings")
def act4_readings():
    return jsonify({
        "vibration": latest_reading.get("vibration", 0),
        "led": vibration_active,
        "mq2": latest_reading.get("mq2", 0)
    })

@app.route("/act4/history")
def act4_history_page():
    return render_template("web/act4_history.html")

@app.route("/act4/history/data")
def act4_history_data():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT timestamp, vibration FROM vibration_data ORDER BY id DESC LIMIT 100")
    vib_rows = c.fetchall()

    c.execute("SELECT timestamp, mq2_value FROM mq2_data ORDER BY id DESC LIMIT 100")
    mq2_rows = c.fetchall()
    conn.close()

    vib_rows = vib_rows[::-1]
    mq2_rows = mq2_rows[::-1]

    vib_timestamps = [row[0] for row in vib_rows]
    vib_values = [row[1] for row in vib_rows]

    mq2_timestamps = [row[0] for row in mq2_rows]
    mq2_values = [row[1] for row in mq2_rows]

    return jsonify({
        "vibration": {
            "timestamps": vib_timestamps,
            "values": vib_values
        },
        "mq2": {
            "timestamps": mq2_timestamps,
            "values": mq2_values
        }
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
