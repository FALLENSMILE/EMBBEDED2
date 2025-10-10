from flask import Flask, render_template, jsonify, Response, request
import os
os.environ["GLOG_minloglevel"] = "2"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
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
import mediapipe as mp

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
current_gesture = ""
gesture_lock = threading.Lock()

# --- Load saved faces ---
if os.path.exists(DATABASE_FILE):
    with open(DATABASE_FILE, "rb") as f:
        data = pickle.load(f)
        KNOWN_FACES = data.get("faces", [])
        KNOWN_NAMES = data.get("names", [])
    print(f"[INFO] Loaded {len(KNOWN_NAMES)} known faces from database.")

# --- Buzzer Setup ---
buzzer = Buzzer(21)

# --- Cleanup ---
def cleanup():
    print("[CLEANUP] Releasing resources...")
    try:
        camera.cap.release()
    except Exception:
        pass
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

        # Try setting 1080p resolution
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)

        width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if width < 1280 or height < 720:
            print("[WARNING] Camera does not support 1080p. Falling back to 720p.")
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        else:
            print(f"[INFO] Using camera resolution: {width}x{height}")

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

# --- MediaPipe Hands Setup ---
mp_hands = mp.solutions.hands
mp_drawing = mp.solutions.drawing_utils
hands_detector = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=2,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)

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

@app.route("/act10/gesture/data")
def act10_gesture_data():
    with gesture_lock:
        return jsonify({"gesture": current_gesture})

# --- Gesture Detection Helper ---
def detect_gesture_from_landmarks(hand_landmarks, image_w, image_h):
    tips = {"thumb": 4, "index": 8, "middle": 12, "ring": 16, "pinky": 20}
    pip = {"index": 6, "middle": 10, "ring": 14, "pinky": 18}
    mcp = {"thumb": 2}

    lm = [(int(l.x * image_w), int(l.y * image_h), l.z) for l in hand_landmarks.landmark]
    extended = {}
    for finger in ["index", "middle", "ring", "pinky"]:
        tip_y = lm[tips[finger]][1]
        pip_y = lm[pip[finger]][1]
        extended[finger] = tip_y < pip_y - 10

    thumb_tip_x, thumb_tip_y, _ = lm[tips["thumb"]]
    thumb_mcp_x, thumb_mcp_y, _ = lm[mcp["thumb"]]
    thumb_extended_vertical = thumb_tip_y < thumb_mcp_y - 10
    thumb_extended_horizontal = abs(thumb_tip_x - thumb_mcp_x) > 40
    thumb_extended = thumb_extended_vertical or thumb_extended_horizontal

    if thumb_extended and not (extended["index"] or extended["middle"] or extended["ring"] or extended["pinky"]):
        return "Thumbs Up"

    if extended["index"] and extended["middle"] and not extended["ring"] and not extended["pinky"]:
        return "Peace"

    return ""

# --- Video Stream Generator ---
frame_count = 0

def gen_frames():
    global current_unknown_encodings, frame_count, current_gesture
    while True:
        success, frame = camera.read()
        if not success:
            break

        frame_count += 1
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        do_face = (frame_count % 10) != 0
        do_hand = not do_face

        if do_face:
            small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
            rgb_small = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
            face_locations = face_recognition.face_locations(rgb_small)
            face_encodings = face_recognition.face_encodings(rgb_small, face_locations)

            current_unknown_encodings = []
            unknown_detected = False

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

                top *= 4; right *= 4; bottom *= 4; left *= 4
                color = (0, 0, 255) if name == "Unknown" else (0, 255, 0)
                cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
                cv2.putText(frame, name, (left, top - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

            if unknown_detected:
                threading.Thread(target=lambda: buzzer.on() or time.sleep(0.4) or buzzer.off(),
                                 daemon=True).start()

        elif do_hand:
            results = hands_detector.process(rgb_frame)
            found_gesture = ""
            if results.multi_hand_landmarks:
                for hand_landmarks in results.multi_hand_landmarks:
                    h, w, _ = frame.shape
                    gesture = detect_gesture_from_landmarks(hand_landmarks, w, h)
                    if gesture:
                        found_gesture = gesture
                    mp_drawing.draw_landmarks(frame, hand_landmarks, mp_hands.HAND_CONNECTIONS)

            with gesture_lock:
                current_gesture = found_gesture if found_gesture else ""

        with gesture_lock:
            gesture_text = current_gesture

        if gesture_text:
            cv2.rectangle(frame, (10, 10), (250, 60), (0, 0, 0), -1)
            cv2.putText(frame, f"Gesture: {gesture_text}", (20, 45),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)

        ret, buffer = cv2.imencode('.jpg', frame)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

@app.route("/act10/video_feed")
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/act10/register", methods=['POST'])
def register_face():
    global current_unknown_encodings
    name = request.form.get("name", "").strip()

    if not current_unknown_encodings:
        return jsonify({"status": "error", "message": "No unknown face detected"})

    if not name:
        return jsonify({"status": "error", "message": "Name is required"})

    face_encoding = current_unknown_encodings[0]
    KNOWN_FACES.append(face_encoding)
    KNOWN_NAMES.append(name)

    ret, frame = camera.read()
    if ret:
        face_id = len(KNOWN_NAMES)
        filepath = os.path.join(SAVE_DIR, f"{name}_{face_id}.jpg")
        cv2.imwrite(filepath, frame)

    with open(DATABASE_FILE, "wb") as f:
        pickle.dump({"faces": KNOWN_FACES, "names": KNOWN_NAMES}, f)

    print(f"[INFO] Auto-registered new face: {name}")
    return jsonify({"status": "success", "message": f"Registered {name}"})

# --- Main ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
