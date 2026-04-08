"""
SmartGuard detector: camera loop, face recognition, geofence polling,
alarm escalation, siren control, breach recording, and Telegram callbacks.
"""
import cv2
import time
import os
import requests
import datetime
import threading
import queue
import winsound
import json
from collections import deque, Counter
import numpy as np
import face_recognition
import glob

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- Configuration Constants ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database/")
STATIC_PATH = os.path.join(BASE_DIR, "static/")
os.makedirs(DB_PATH, exist_ok=True)
os.makedirs(STATIC_PATH, exist_ok=True)

FRAME_SKIP = 10            # Run face recognition every N frames to reduce CPU load
PRE_ROLL_SEC = 5           # Seconds of frames buffered before an alarm triggers
AREA_CLEAR_SEC = 5.0       # Seconds with no face before the area is considered empty
PROMPT_AT_SEC = 15         # Seconds of unknown presence before sending a Telegram snapshot
MAJOR_ALERT_AT_SEC = 30    # Seconds of unknown presence before triggering full alarm + recording
PERSISTENT_RESET_SEC = 60  # Seconds after a breach before all timers fully reset
RE_TRIGGER_AFTER_SEC = 10  # Seconds before re-escalating during a persistent alert window
VIDEO_RECORD_SEC = 15      # Duration of each breach recording clip in seconds
FACE_MATCH_THRESHOLD = 0.50  # Reverted to 0.50 for better real-world matching

TELEGRAM_TOKEN = os.getenv("SMARTGUARD_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("SMARTGUARD_TELEGRAM_CHAT_ID")
API_KEY = os.getenv("SMARTGUARD_API_KEY", "")
PORT = os.getenv("SMARTGUARD_PORT", "5000")
BASE_URL = f"http://127.0.0.1:{PORT}"
HEARTBEAT_URL = os.getenv("SMARTGUARD_HEARTBEAT_URL", "")


class SmartGuardDetector:
    def __init__(self):
        # Load known resident profiles from disk into memory
        self.known_profiles = {}
        for path in glob.glob(f"{DB_PATH}/*/*_profile.npy"):
            name = os.path.basename(os.path.dirname(path))
            self.known_profiles[name] = np.load(path)
        print(f"[INFO] Loaded {len(self.known_profiles)} resident profile(s).")

        # Alarm and authorization state
        self.owner_is_home = False
        self.is_alarm_active = False
        self.disarm_requested = False
        self.guest_authorized = False
        self.force_escalation = False

        # Face recognition state
        self.is_processing_face = False
        self.last_known_identity = None
        self.current_display_text = "Initializing..."
        
        # Reduced buffer to 5 for much faster authorization (~1.5s to 2s)
        self.prediction_buffer = deque(maxlen=5)

        # Video recording state
        self.is_recording = False
        self.record_end_time = 0.0
        self.active_record_queues = []

        self.face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )

    def _cleanup_old_media(self):
        now = time.time()
        for filename in os.listdir(STATIC_PATH):
            filepath = os.path.join(STATIC_PATH, filename)
            if os.path.isfile(filepath) and (now - os.path.getmtime(filepath)) > (7 * 86400):
                try:
                    os.remove(filepath)
                except Exception:
                    pass

    def _log_event(self, event_type: str, status: str):
        def _do():
            try:
                payload = {
                    "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "event_type": event_type,
                    "status": status,
                }
                headers = {"X-API-KEY": API_KEY} if API_KEY else {}
                requests.post(f"{BASE_URL}/add_log", json=payload, headers=headers, timeout=2)
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()

    def _geofence_worker(self):
        last_heartbeat = 0
        last_profile_files = set(glob.glob(f"{DB_PATH}/*/*_profile.npy"))

        while True:
            current_files = set(glob.glob(f"{DB_PATH}/*/*_profile.npy"))
            if current_files != last_profile_files:
                print("[INFO] Database change detected. Hot-reloading profiles...")
                new_profiles = {}
                for path in current_files:
                    name = os.path.basename(os.path.dirname(path))
                    new_profiles[name] = np.load(path)
                self.known_profiles = new_profiles
                last_profile_files = current_files

            try:
                headers = {"X-API-KEY": API_KEY} if API_KEY else {}
                r = requests.get(f"{BASE_URL}/get_system_status", headers=headers, timeout=2)
                self.owner_is_home = r.json().get("is_home", False)
            except Exception:
                pass 

            if HEARTBEAT_URL and (time.time() - last_heartbeat) > 60:
                try:
                    requests.get(HEARTBEAT_URL, timeout=5)
                    last_heartbeat = time.time()
                except Exception:
                    pass
            time.sleep(2)

    def _siren_worker(self):
        playing = False
        while True:
            if self.is_alarm_active and not playing:
                if os.path.exists("alarm.wav"):
                    winsound.PlaySound("alarm.wav", winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
                playing = True
            elif not self.is_alarm_active and playing:
                if os.path.exists("alarm.wav"):
                    winsound.PlaySound(None, winsound.SND_PURGE)
                playing = False

            if self.is_alarm_active and not os.path.exists("alarm.wav"):
                winsound.Beep(2500, 500)
            time.sleep(0.2)

    def _telegram_worker(self):
        offset = None
        while True:
            if not TELEGRAM_TOKEN:
                break
            try:
                params = {"timeout": 10, "allowed_updates": ["callback_query"]}
                if offset:
                    params["offset"] = offset
                resp = requests.get(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                    params=params, timeout=15
                ).json()

                for item in resp.get("result", []):
                    offset = item["update_id"] + 1
                    cb = item.get("callback_query")
                    if not cb:
                        continue

                    data = cb.get("data", "")
                    cb_id = cb["id"]
                    chat_id = cb.get("message", {}).get("chat", {}).get("id")
                    message_id = cb.get("message", {}).get("message_id")
                    is_caption = "caption" in cb.get("message", {})
                    ans_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"

                    def provide_feedback(toast_msg, permanent_msg):
                        try:
                            requests.get(
                                ans_url,
                                params={"callback_query_id": cb_id, "text": toast_msg, "show_alert": True},
                                timeout=5
                            )
                        except Exception:
                            pass
                        if chat_id and message_id:
                            if is_caption:
                                edit_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageCaption"
                                payload = {"chat_id": chat_id, "message_id": message_id, "caption": permanent_msg, "reply_markup": {"inline_keyboard": []}}
                            else:
                                edit_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText"
                                payload = {"chat_id": chat_id, "message_id": message_id, "text": permanent_msg, "reply_markup": {"inline_keyboard": []}}
                            try:
                                requests.post(edit_url, json=payload, timeout=5)
                            except Exception:
                                pass

                    if data == "authorize_guest":
                        self.guest_authorized = True
                        self.is_alarm_active = False
                        self._log_event("Access", "Guest authorized via Telegram")
                        provide_feedback("✅ Guest Authorized!", "✅ Access Granted: Known Guest")

                    elif data == "force_alarm":
                        self.force_escalation = True
                        self._log_event("Security Breach", "Alarm forced via Telegram")
                        provide_feedback("🚨 Alarm Triggered!", "🚨 ACTION TAKEN: Alarm Forced")

                    elif data == "disarm_system":
                        self.is_alarm_active = False
                        self.disarm_requested = True
                        self._log_event("System", "Remotely disarmed via Telegram")
                        provide_feedback("🔇 System Disarmed!", "🔇 ACTION TAKEN: Siren Disarmed")

            except Exception:
                pass
            time.sleep(2)

    def _get_stable_identity(self):
        """Returns the recognized identity if majority match, else None."""
        if len(self.prediction_buffer) < 3:
            return None

        counts = Counter(self.prediction_buffer)
        most_common, freq = counts.most_common(1)[0]

        # Require a majority (at least 3 out of 5 frames) to be a known person
        if most_common != "unknown" and freq >= 3:
            return most_common
        return None

    def _process_face_background(self, frame_copy):
        try:
            rgb = cv2.cvtColor(frame_copy, cv2.COLOR_BGR2RGB)
            locations = face_recognition.face_locations(rgb, model="hog")
            if not locations:
                return

            encodings = face_recognition.face_encodings(rgb, locations)
            if not encodings:
                return

            live = encodings[0]
            best_name, best_dist = None, FACE_MATCH_THRESHOLD

            for name, profile in self.known_profiles.items():
                d = face_recognition.face_distance([profile], live)[0]
                if d < best_dist:
                    best_dist = d
                    best_name = name

            if best_name:
                self.prediction_buffer.append(best_name)
            else:
                self.prediction_buffer.append("unknown")

        finally:
            self.is_processing_face = False

    def _video_writer_worker(self, filepath, frame_size, q, fps):
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(filepath, fourcc, fps, frame_size)
        cancelled = False

        while True:
            frame = q.get()
            if isinstance(frame, str) and frame == "CANCEL":
                cancelled = True
                break
            if frame is None:
                break
            out.write(frame)
        out.release()

        if cancelled:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception:
                pass
            return

        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            try:
                with open(filepath, "rb") as f:
                    kb = {"inline_keyboard": [[{"text": "🔇 Disarm Siren", "callback_data": "disarm_system"}]]}
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo",
                        data={"chat_id": TELEGRAM_CHAT_ID, "caption": "📹 Intruder footage", "reply_markup": json.dumps(kb)},
                        files={"video": f},
                        timeout=60
                    )
            except Exception:
                pass

    def start(self):
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("[ERROR] Could not open camera.")
            return

        self._cleanup_old_media()
        camera_fps = cap.get(cv2.CAP_PROP_FPS)
        if camera_fps <= 0 or camera_fps > 120:
            camera_fps = 20.0

        frame_size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        pre_roll_buffer = deque(maxlen=int(camera_fps * PRE_ROLL_SEC))

        threading.Thread(target=self._geofence_worker, daemon=True).start()
        threading.Thread(target=self._siren_worker, daemon=True).start()
        if TELEGRAM_TOKEN:
            threading.Thread(target=self._telegram_worker, daemon=True).start()

        last_breach_time = 0.0
        persistent_alert_active = False
        dwell_timer = None
        last_face_time = 0.0
        prompt_sent = major_alert_sent = False
        frame_count = 0

        print("[INFO] System Active. Press 'q' to quit.")

        while True:
            ret, frame = cap.read()
            if not ret:
                print("[WARNING] Camera frame dropped. Retrying...")
                time.sleep(2)
                cap.release()
                cap = cv2.VideoCapture(0)
                continue

            frame_count += 1
            pre_roll_buffer.append(frame.copy())

            if self.owner_is_home:
                self.current_display_text = "HOME (Passive Mode)"
                self.is_alarm_active = self.disarm_requested = self.guest_authorized = self.force_escalation = False
                dwell_timer = None
                prompt_sent = major_alert_sent = False

                if self.is_recording:
                    self.is_recording = False
                    for q in self.active_record_queues:
                        q.put("CANCEL")
                    self.active_record_queues.clear()
            else:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = self.face_cascade.detectMultiScale(gray, 1.3, 5)

                if len(faces) > 0:
                    last_face_time = time.time()
                    if frame_count % FRAME_SKIP == 0 and not self.is_processing_face:
                        self.is_processing_face = True
                        threading.Thread(
                            target=self._process_face_background,
                            args=(frame.copy(),),
                            daemon=True
                        ).start()

                area_clear = (time.time() - last_face_time) > AREA_CLEAR_SEC

                if area_clear:
                    self.prediction_buffer.clear()
                    self.current_display_text = "Scanning (Area Clear)..."
                    self.last_known_identity = None

                    if self.is_alarm_active:
                        self.is_alarm_active = False
                        last_breach_time = time.time()
                        persistent_alert_active = True

                    major_alert_sent = False

                    time_since_last_face = time.time() - last_face_time
                    if time_since_last_face > PERSISTENT_RESET_SEC:
                        self.guest_authorized = False
                        self.disarm_requested = False
                        self.force_escalation = False
                        dwell_timer = None 
                        persistent_alert_active = prompt_sent = False
                else:
                    stable_identity = self._get_stable_identity()
                    current_time = time.time()

                    # Clean Authorization Logic
                    if stable_identity or self.guest_authorized:
                        self.last_known_identity = stable_identity
                        dwell_timer = None
                        self.is_alarm_active = False
                        prompt_sent = False
                        major_alert_sent = False

                        self.current_display_text = f"AUTHORIZED: {stable_identity or 'Guest'}"

                        if self.is_recording:
                            self.is_recording = False
                            for q in self.active_record_queues:
                                q.put("CANCEL")
                            self.active_record_queues.clear()

                    else:
                        if dwell_timer is None:
                            dwell_timer = current_time
                            self.disarm_requested = False

                        elapsed = current_time - dwell_timer

                        if self.disarm_requested:
                            self.current_display_text = "DISARMED VIA TELEGRAM"
                            self.is_alarm_active = False

                            if self.is_recording:
                                self.is_recording = False
                                for q in self.active_record_queues:
                                    q.put("CANCEL")
                                self.active_record_queues.clear()
                        else:
                            trigger_now = self.force_escalation or (
                                persistent_alert_active and elapsed > RE_TRIGGER_AFTER_SEC
                            )

                            if elapsed > PROMPT_AT_SEC and not prompt_sent and not trigger_now:
                                prompt_sent = True
                                snapshot_path = os.path.join(STATIC_PATH, f"snapshot_{int(time.time())}.jpg")
                                cv2.imwrite(snapshot_path, frame)

                                if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
                                    def _send_photo():
                                        try:
                                            with open(snapshot_path, "rb") as f:
                                                keyboard = {
                                                    "inline_keyboard": [
                                                        [{"text": "✅ Known Guest", "callback_data": "authorize_guest"}],
                                                        [{"text": "🚨 Sound Alarm", "callback_data": "force_alarm"}]
                                                    ]
                                                }
                                                requests.post(
                                                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                                                    data={
                                                        "chat_id": TELEGRAM_CHAT_ID,
                                                        "caption": "❓ Unknown person detected. Authorize?",
                                                        "reply_markup": json.dumps(keyboard)
                                                    },
                                                    files={"photo": f},
                                                    timeout=10
                                                )
                                        except Exception:
                                            pass
                                    threading.Thread(target=_send_photo, daemon=True).start()

                            if (elapsed > MAJOR_ALERT_AT_SEC or trigger_now) and not major_alert_sent:
                                self.is_alarm_active = True
                                self._log_event("Security Breach", "Tier 2 Alarm Triggered")

                                if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
                                    def _send_siren_alert():
                                        try:
                                            kb = {"inline_keyboard": [[{"text": "🔇 Disarm Siren", "callback_data": "disarm_system"}]]}
                                            requests.post(
                                                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                                json={"chat_id": TELEGRAM_CHAT_ID, "text": "🚨 ALARM TRIGGERED! Siren is sounding.", "reply_markup": kb},
                                                timeout=5
                                            )
                                        except Exception:
                                            pass
                                    threading.Thread(target=_send_siren_alert, daemon=True).start()

                                self.is_recording = True
                                self.record_end_time = time.time() + VIDEO_RECORD_SEC
                                current_q = queue.Queue()
                                for f in pre_roll_buffer:
                                    current_q.put(f)
                                self.active_record_queues.append(current_q)

                                t = int(time.time())
                                video_path = os.path.join(STATIC_PATH, f"breach_{t}.mp4")

                                threading.Thread(
                                    target=self._video_writer_worker,
                                    args=(video_path, frame_size, current_q, camera_fps),
                                    daemon=True
                                ).start()

                                major_alert_sent = True
                                self.force_escalation = False

                            if self.is_alarm_active:
                                self.current_display_text = "ALARM ACTIVE / RECORDING"
                            elif prompt_sent:
                                time_left = max(0, int(MAJOR_ALERT_AT_SEC - elapsed))
                                self.current_display_text = f"AWAITING INPUT ({time_left}s to Alarm)"
                            else:
                                self.current_display_text = f"OBSERVING ({int(elapsed)}s)"

            if self.is_recording:
                for q in self.active_record_queues:
                    q.put(frame.copy())

                if int(time.time() * 2) % 2 == 0:
                    cv2.circle(frame, (frame_size[0] - 30, 30), 10, (0, 0, 255), -1)
                cv2.putText(frame, "REC", (frame_size[0] - 80, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

                if time.time() > self.record_end_time:
                    self.is_recording = False
                    for q in self.active_record_queues:
                        q.put(None)
                    self.active_record_queues.clear()

            safe = any(word in self.current_display_text for word in ["AUTHORIZED", "HOME", "Clear"])
            color = (0, 255, 0) if safe else (0, 0, 255)
            cv2.putText(frame, self.current_display_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

            cv2.imshow("SmartGuard Feed", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        for q in self.active_record_queues:
            q.put(None)
        cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    detector = SmartGuardDetector()
    detector.start()