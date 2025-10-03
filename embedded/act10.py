from flask import Flask, render_template, jsonify, Response, request
import os
import signal
import sys
import atexit
import cv2
import face_recognition
import numpy as np
import pickle
import threading
from jinja2 import ChoiceLoader, FileSystemLoader
from gpiozero import Buzzer
import time

# --- Paths ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOCAL_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")
SAVE_DIR = "registered_faces"
os.makedirs(SAVE_DIR, exist_ok=True)
DATABASE_FILE = os.path.join(SAVE_DIR, "faces.pkl")

# --- Initialize Flask ---
app = Flask(__name__)
app.jinja_loader = ChoiceLoader([
    FileSystemLoader(BASE_DIR),
    FileSystemLoader(LOCAL_TEMPLATES)
])

# --- Globals ---
KNOWN_FACES = []
KNOWN_NAMES = []
current_unknown_encodings = []

# --- Load saved faces ---
if os.path.exists(DATABASE_FILE):
    with open(DATABASE_FILE, "rb") as f:
        data = pickle.load(f)
        KNOWN_FACES = data.get("faces", [])
        KNOWN_NAMES = data.get("names", [])
    print(f"[INFO] Loaded {len(KNOWN_NAMES)} known faces from database.")

# --- Buzzer Setup ---
buzzer = Buzzer(21)  # BCM pin 21

# --- Cleanup ---
def cleanup():
    print("[CLEANUP] Releasing resources...")
    camera.cap.release()
    cv2.destroyAllWindows()
    with open(DATABASE_FILE, "wb") as f:
        pickle.dump({"faces": KNOWN_FACES, "names": KNOWN_NAMES}, f)
    print(f"[INFO] Saved {len(KNOWN_NAMES)} known faces to database.")

def handle_signal(sig, frame):
    cleanup()
    sys.exit(0)

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)
atexit.register(cleanup)

# --- Threaded Video Capture ---
class VideoCaptureThread:
    def __init__(self, src=0):
        self.cap = cv2.VideoCapture(src)
        self.ret, self.frame = self.cap.read()
        self.lock = threading.Lock()
        threading.Thread(target=self.update, daemon=True).start()

    def update(self):
        while True:
            ret, frame = self.cap.read()
            with self.lock:
                self.ret, self.frame = ret, frame

    def read(self):
        with self.lock:
            return self.ret, self.frame.copy()

camera = VideoCaptureThread(0)

# --- Routes ---
@app.route("/")
def homepage():
    active_activity = 10
    return render_template("web/index.html",
                           active_activity=active_activity,
                           content='Home',
                           css_file='css/index.css')

@app.route("/act10")
def act10_page():
    return render_template("web/act10.html")

@app.route("/act10/history")
def act10_history_page():
    return render_template("web/act10_history.html")

@app.route("/act10/history/data")
def act10_history_data():
    return jsonify({
        "names": KNOWN_NAMES,
        "count": len(KNOWN_NAMES),
        "unknown_count": len(current_unknown_encodings)
    })

# --- Face Recognition Stream ---
frame_count = 0
PROCESS_EVERY_N_FRAMES = 3

def gen_frames():
    global current_unknown_encodings, frame_count
    while True:
        success, frame = camera.read()
        if not success:
            break

        frame_count += 1
        small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
        rgb_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)

        if frame_count % PROCESS_EVERY_N_FRAMES == 0:
            current_unknown_encodings = []

            face_locations = face_recognition.face_locations(rgb_frame)
            face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)

            unknown_detected = False  # Flag to track unknown faces

            for (top, right, bottom, left), face_encoding in zip(face_locations, face_encodings):
                name = "Unknown"

                if KNOWN_FACES:
                    matches = face_recognition.compare_faces(KNOWN_FACES, face_encoding, tolerance=0.5)
                    if True in matches:
                        first_match_index = matches.index(True)
                        name = KNOWN_NAMES[first_match_index]
                    else:
                        current_unknown_encodings.append(face_encoding)
                        unknown_detected = True
                else:
                    current_unknown_encodings.append(face_encoding)
                    unknown_detected = True

                # Scale box to original frame size
                top *= 4
                right *= 4
                bottom *= 4
                left *= 4

                cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)
                cv2.putText(frame, name, (left, top - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            # Activate buzzer only if unknown faces detected
            if unknown_detected:
                threading.Thread(target=lambda: buzzer.on() or time.sleep(0.2) or buzzer.off(),
                                 daemon=True).start()

        ret, buffer = cv2.imencode('.jpg', frame)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

@app.route("/act10/video_feed")
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

# --- Register Unknown Face ---
@app.route("/act10/register", methods=['POST'])
def register_face():
    global current_unknown_encodings
    name = request.form.get("name", "").strip()

    if not current_unknown_encodings:
        return jsonify({"status": "error", "message": "No unknown face detected"})

    if not name:
        return jsonify({"status": "error", "message": "Name is required"})

    # Register first unknown face
    face_encoding = current_unknown_encodings[0]
    KNOWN_FACES.append(face_encoding)
    KNOWN_NAMES.append(name)

    # Save snapshot
    ret, frame = camera.read()
    if ret:
        face_id = len(KNOWN_NAMES)
        filepath = os.path.join(SAVE_DIR, f"{name}_{face_id}.jpg")
        cv2.imwrite(filepath, frame)

    # Update database
    with open(DATABASE_FILE, "wb") as f:
        pickle.dump({"faces": KNOWN_FACES, "names": KNOWN_NAMES}, f)

    return jsonify({"status": "success", "message": f"Registered {name}"})

# --- Main ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
