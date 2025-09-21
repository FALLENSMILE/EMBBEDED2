from flask import Flask, render_template, jsonify
import os
from jinja2 import ChoiceLoader, FileSystemLoader
import serial
import pynmea2
import threading
import time
import board, busio
import digitalio
from PIL import Image, ImageDraw, ImageFont
import adafruit_ssd1306
import RPi.GPIO as GPIO
from adafruit_bus_device import i2c_device

# --- Paths ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))   # parent folder
LOCAL_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")      # act6/templates

# --- Initialize Flask ---
app = Flask(__name__)
app.jinja_loader = ChoiceLoader([
    FileSystemLoader(BASE_DIR),
    FileSystemLoader(LOCAL_TEMPLATES)
])

# --- GPIO Setup ---
BUZZER_PIN = 20
LED_PIN = 21
GPIO.setmode(GPIO.BCM)
GPIO.setup(BUZZER_PIN, GPIO.OUT)
GPIO.setup(LED_PIN, GPIO.OUT)
GPIO.output(BUZZER_PIN, GPIO.LOW)
GPIO.output(LED_PIN, GPIO.LOW)

# --- I2C Bus (OLED + MPU6500) ---
i2c = busio.I2C(board.SCL, board.SDA)

# --- OLED Setup ---
oled = adafruit_ssd1306.SSD1306_I2C(128, 64, i2c)
oled.fill(0)
oled.show()
image = Image.new("1", (oled.width, oled.height))
draw = ImageDraw.Draw(image)
font = ImageFont.load_default()

def oled_display(lines):
    """Display a list of text lines on OLED"""
    draw.rectangle((0, 0, oled.width, oled.height), fill=0)
    for i, line in enumerate(lines):
        draw.text((0, i * 12), line, font=font, fill=255)
    oled.image(image)
    oled.show()

# --- MPU6500 Minimal Driver ---
class MPU6500:
    ADDRESS = 0x68
    WHO_AM_I = 0x75
    WHO_AM_I_VALS = (0x68, 0x70)
    ACCEL_XOUT_H = 0x3B
    GYRO_XOUT_H = 0x43
    TEMP_OUT_H = 0x41

    def __init__(self, i2c, address=ADDRESS):
        self.i2c_device = i2c_device.I2CDevice(i2c, address)
        chip_id = self._read_u8(self.WHO_AM_I)
        if chip_id not in self.WHO_AM_I_VALS:
            raise RuntimeError(f"Unknown MPU chip 0x{chip_id:02X}")
        self.chip_id = chip_id
        self._write_u8(0x6B, 0x00)  # wake up
        time.sleep(0.1)

    def _read_u8(self, reg):
        with self.i2c_device as i2c:
            i2c.write(bytes([reg]))
            buf = bytearray(1)
            i2c.readinto(buf)
            return buf[0]

    def _read_s16(self, reg):
        with self.i2c_device as i2c:
            i2c.write(bytes([reg]))
            buf = bytearray(2)
            i2c.readinto(buf)
        val = (buf[0] << 8) | buf[1]
        if val & 0x8000:
            val -= 65536
        return val

    def _write_u8(self, reg, val):
        with self.i2c_device as i2c:
            i2c.write(bytes([reg, val & 0xFF]))

    @property
    def acceleration(self):
        return (
            self._read_s16(self.ACCEL_XOUT_H) / 16384.0 * 9.80665,
            self._read_s16(self.ACCEL_XOUT_H + 2) / 16384.0 * 9.80665,
            self._read_s16(self.ACCEL_XOUT_H + 4) / 16384.0 * 9.80665,
        )

    @property
    def gyro(self):
        return (
            self._read_s16(self.GYRO_XOUT_H) / 131.0,
            self._read_s16(self.GYRO_XOUT_H + 2) / 131.0,
            self._read_s16(self.GYRO_XOUT_H + 4) / 131.0,
        )

    @property
    def temperature(self):
        raw_temp = self._read_s16(self.TEMP_OUT_H)
        return (raw_temp / 340.0) + 36.53

# --- MPU6500 Setup ---
mpu = MPU6500(i2c)
latest_mpu = {
    "accel_x": None,
    "accel_y": None,
    "accel_z": None,
    "gyro_x": None,
    "gyro_y": None,
    "gyro_z": None,
    "temperature": None
}

def read_mpu_loop():
    global latest_mpu
    while True:
        try:
            accel = mpu.acceleration
            gyro = mpu.gyro
            temp = mpu.temperature
            latest_mpu = {
                "accel_x": round(accel[0], 2),
                "accel_y": round(accel[1], 2),
                "accel_z": round(accel[2], 2),
                "gyro_x": round(gyro[0], 2),
                "gyro_y": round(gyro[1], 2),
                "gyro_z": round(gyro[2], 2),
                "temperature": round(temp, 2)
            }
            print(f"[MPU6500] {latest_mpu}")
            time.sleep(0.2)
        except Exception as e:
            print("[MPU ERROR]", e)
            time.sleep(1)

# --- GPS Setup ---
GPS_PORT = "/dev/ttyS0"
GPS_BAUDRATE = 9600
latest_gps = {
    "latitude": None,
    "longitude": None,
    "timestamp": None,
    "speed_knots": None,
    "speed_kmh": None,
    "course": None,
    "status": None
}

oled_display(["GPS: Calibrating...", "Waiting for fix..."])

def read_gps_loop():
    global latest_gps
    try:
        ser = serial.Serial(GPS_PORT, baudrate=GPS_BAUDRATE, timeout=1)
        while True:
            line = ser.readline().decode("ascii", errors="replace").strip()
            if line.startswith("$GPRMC"):
                try:
                    msg = pynmea2.parse(line)
                    if msg.status == "A":  # valid GPS fix
                        speed_knots = msg.spd_over_grnd or 0.0
                        speed_kmh = speed_knots * 1.852
                        latest_gps = {
                            "latitude": msg.latitude,
                            "longitude": msg.longitude,
                            "timestamp": f"{msg.datestamp} {msg.timestamp}",
                            "speed_knots": round(speed_knots, 2),
                            "speed_kmh": round(speed_kmh, 2),
                            "course": msg.true_course,
                            "status": msg.status
                        }
                        print(f"[GPS] {latest_gps}")

                        GPIO.output(BUZZER_PIN, GPIO.HIGH)
                        GPIO.output(LED_PIN, GPIO.HIGH)
                        time.sleep(0.2)
                        GPIO.output(BUZZER_PIN, GPIO.LOW)

                        # Show GPS + MPU
                        oled_display([
                            f"Lat: {msg.latitude:.5f}",
                            f"Lon: {msg.longitude:.5f}",
                            f"Spd: {speed_kmh:.2f} km/h",
                            f"AccZ: {latest_mpu['accel_z']}"
                        ])
                    else:
                        # No valid GPS fix → still show MPU
                        oled_display([
                            "GPS: Calibrating...",
                            f"AccX: {latest_mpu['accel_x']}",
                            f"AccY: {latest_mpu['accel_y']}",
                            f"AccZ: {latest_mpu['accel_z']}",
                            f"Gx:{latest_mpu['gyro_x']} Gy:{latest_mpu['gyro_y']}"
                        ])
                        GPIO.output(LED_PIN, GPIO.LOW)
                except Exception as e:
                    print("[GPS PARSE ERROR]", e)
            else:
                # No GPS sentence yet → still show MPU
                oled_display([
                    "GPS: Waiting...",
                    f"AccX: {latest_mpu['accel_x']}",
                    f"AccY: {latest_mpu['accel_y']}",
                    f"AccZ: {latest_mpu['accel_z']}",
                    f"Gx:{latest_mpu['gyro_x']} Gy:{latest_mpu['gyro_y']}"
                ])
            time.sleep(0.1)
    except Exception as e:
        print("[GPS ERROR]", e)

# --- Start background threads ---
threading.Thread(target=read_gps_loop, daemon=True).start()
threading.Thread(target=read_mpu_loop, daemon=True).start()

# --- Flask Routes ---
@app.route("/")
def homepage():
    active_activity = 6
    return render_template("web/index.html", active_activity=active_activity, content='Home', css_file='css/index.css')

@app.route("/act6")
def act6_page():
    return render_template("web/act6.html")

@app.route("/act6/gps")
def act6_gps_data():
    return jsonify(latest_gps)

@app.route("/act6/mpu")
def act6_mpu_data():
    return jsonify(latest_mpu)

# --- Cleanup on exit ---
def cleanup():
    GPIO.cleanup()
    oled.fill(0)
    oled.show()

# --- Main ---
if __name__ == "__main__":
    try:
        app.run(host="0.0.0.0", port=5000)
    finally:
        cleanup()
