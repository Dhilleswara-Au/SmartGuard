"""
SmartGuard Flask app: dashboard, resident management, geofencing API, and activity logs.
"""
from flask import Flask, render_template, request, redirect, jsonify, session, flash
from functools import wraps
import os
import sqlite3
import datetime
from math import sin, cos, sqrt, atan2, radians
import secrets
from contextlib import closing
from werkzeug.utils import secure_filename
import cv2
import shutil
import numpy as np
import face_recognition

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
app = Flask(__name__)

app.secret_key = os.getenv("SMARTGUARD_SECRET_KEY")
if not app.secret_key:
    raise ValueError("CRITICAL: SMARTGUARD_SECRET_KEY is missing. Check your .env file.")

app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

ADMIN_USERNAME = os.getenv("SMARTGUARD_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("SMARTGUARD_ADMIN_PASSWORD", "change_me")
API_KEY = os.getenv("SMARTGUARD_API_KEY", "default_secure_key")

if ADMIN_PASSWORD == "change_me":
    raise ValueError("CRITICAL: SMARTGUARD_ADMIN_PASSWORD is still set to the default 'change_me'.")
if API_KEY == "default_secure_key":
    raise ValueError("CRITICAL: SMARTGUARD_API_KEY is still set to the default 'default_secure_key'.")


# -----------------------------------------------------------------------------
# Config & Paths
# -----------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "database/")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
DB_PATH = os.path.join(BASE_DIR, "smartguard.db")


def _ensure_csrf():
    if "csrf_token" not in session:
        session["csrf_token"] = os.urandom(24).hex()


def _check_csrf():
    server_token = session.get("csrf_token")
    client_token = request.form.get("csrf_token")
    if server_token is None or client_token is None:
        return False
    return secrets.compare_digest(server_token, client_token)


def _env_float(name: str, default: str) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


HOME_COORDINATES = {
    "lat": _env_float("SMARTGUARD_HOME_LAT", "0.0"),
    "lon": _env_float("SMARTGUARD_HOME_LON", "0.0"),
}
HOME_RADIUS_M = _env_float("SMARTGUARD_HOME_DISTANCE_METERS", "100.0")
DEVICE_STALE_SEC = 300

active_devices = {}


@app.context_processor
def inject_csrf():
    _ensure_csrf()
    return {"csrf_token": session.get("csrf_token", "")}


# -----------------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------------

def init_db():
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as conn:
        with conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS residents (id INTEGER PRIMARY KEY, name TEXT UNIQUE, path TEXT)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS activity_log
                   (id INTEGER PRIMARY KEY, timestamp TEXT, event_type TEXT, status TEXT)"""
            )

def _ensure_residents_schema():
    """Keep DB compatible across earlier column naming mistakes."""
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as conn:
        with conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(residents)").fetchall()}
            if "folder_path" in cols and "path" not in cols:
                conn.execute("ALTER TABLE residents RENAME COLUMN folder_path TO path")


init_db()
_ensure_residents_schema()


def db_execute(query: str, args=(), many=False):
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as conn:
        with conn:
            if many:
                conn.executemany(query, args)
            else:
                conn.execute(query, args)

def db_fetch(query: str, args=()):
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as conn:
        return conn.execute(query, args).fetchall()

def db_fetchone(query: str, args=()):
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as conn:
        return conn.execute(query, args).fetchone()


def add_activity_log(timestamp: str, event_type: str, status: str):
    db_execute(
        "INSERT INTO activity_log (timestamp, event_type, status) VALUES (?, ?, ?)",
        (timestamp, event_type, status),
    )


# -----------------------------------------------------------------------------
# Auth
# -----------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "logged_in" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    _ensure_csrf()
    error = None
    if request.method == "POST":
        if not _check_csrf():
            error = "Invalid request. Please try again."
        else:
            u = request.form.get("username", "")
            p = request.form.get("password", "")
            if u == ADMIN_USERNAME and p == ADMIN_PASSWORD:
                session["logged_in"] = True
                return redirect("/")
            error = "Invalid credentials. Please try again."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect("/login")


# -----------------------------------------------------------------------------
# Dashboard & mobile
# -----------------------------------------------------------------------------
@app.route("/")
@login_required
def index():
    residents = db_fetch("SELECT id, name, path FROM residents")
    return render_template("index.html", residents=residents, api_key=API_KEY)

@app.route("/mobile")
@login_required
def mobile_remote():
    return render_template("mobile.html", api_key=API_KEY)


# -----------------------------------------------------------------------------
# Resident management
# -----------------------------------------------------------------------------
@app.route("/upload", methods=["POST"])
@login_required
def upload_file():
    if not _check_csrf():
        flash("Invalid request. Please try again.", "danger")
        return redirect("/")
        
    raw_name = (request.form.get("name") or "").strip()
    if not raw_name:
        flash("Name is required.", "danger")
        return redirect("/")
        
    name = secure_filename(raw_name)
    if not name:
        flash("Invalid name format.", "danger")
        return redirect("/")
        
    file = request.files.get("file")
    if not file:
        flash("No video file provided.", "danger")
        return redirect("/")

    # Safely clear old folder if it exists, then create a new one
    user_folder = os.path.join(UPLOAD_FOLDER, name)
    if os.path.exists(user_folder):
        shutil.rmtree(user_folder)
    os.makedirs(user_folder, exist_ok=True)
    
    video_path = os.path.join(user_folder, f"temp_{name}.mp4")
    file.save(video_path)
    
    try:
        encodings = _extract_face_encodings_from_video(video_path)
    except Exception:
        shutil.rmtree(user_folder, ignore_errors=True)
        flash("Video processing failed. Try another file.", "danger")
        return redirect("/")
    finally:
        if os.path.exists(video_path):
            try:
                os.remove(video_path)
            except OSError:
                pass

    if not encodings:
        shutil.rmtree(user_folder, ignore_errors=True)
        flash("No clear face detected. Try better lighting and a 5–10s video.", "danger")
        return redirect("/")

    master = np.mean(encodings, axis=0)
    profile_path = os.path.join(user_folder, f"{name}_profile.npy")
    np.save(profile_path, master)

    db_execute("DELETE FROM residents WHERE name=?", (name,))
    db_execute("INSERT INTO residents (name, path) VALUES (?, ?)", (name, user_folder))

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    add_activity_log(ts, "Resident Profile Updated", f"Profile for {name} created/updated")
    flash(f"Successfully profiled {name}.", "success")
    return redirect("/")


def _extract_face_encodings_from_video(video_path: str):
    """Sample every 10th frame at 0.25 scale; return list of 128-D face encodings."""
    encodings = []
    cap = cv2.VideoCapture(video_path)
    frame_count = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_count += 1
        if frame_count % 10 != 0:
            continue
        small = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        locations = face_recognition.face_locations(rgb, model="hog")
        if len(locations) == 1: 
            for enc in face_recognition.face_encodings(rgb, locations):
                encodings.append(enc)
                break
    cap.release()
    return encodings


@app.route("/delete/<int:id>", methods=["POST"])
@login_required
def delete_resident(id):
    if not _check_csrf():
        flash("Invalid request.", "danger")
        return redirect("/")
    row = db_fetchone("SELECT name, path FROM residents WHERE id=?", (id,))
    if row:
        name, folder_path = row
        if os.path.exists(folder_path):
            shutil.rmtree(folder_path)
        db_execute("DELETE FROM residents WHERE id=?", (id,))
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        add_activity_log(ts, "Resident Removed", f"Profile for {name} deleted")
    return redirect("/")


# -----------------------------------------------------------------------------
# Geofencing API (used by mobile + detector)
# -----------------------------------------------------------------------------

def _cleanup_stale_devices():
    now = datetime.datetime.now()
    stale_keys = [did for did, loc in active_devices.items() 
                  if (now - loc["last_updated"]).total_seconds() > DEVICE_STALE_SEC]
    for k in stale_keys:
        del active_devices[k]

@app.route("/update_location", methods=["POST"])
def update_location():
    _cleanup_stale_devices()
    if request.headers.get("X-API-KEY") != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No data"}), 400
    try:
        device_id = secure_filename(data.get("device_id", "unknown_device"))
        lat = float(data["lat"])
        lon = float(data["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Lat/lon out of range")
    except (TypeError, ValueError, KeyError):
        return jsonify({"error": "Invalid coordinates"}), 400

    active_devices[device_id] = {
        "lat": lat,
        "lon": lon,
        "last_updated": datetime.datetime.now(),
    }
    return jsonify({"status": "Location updated", "device": device_id}), 200


@app.route("/get_system_status")
def get_system_status():
    if request.headers.get("X-API-KEY") != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    now = datetime.datetime.now()
    lat1 = radians(HOME_COORDINATES["lat"])
    lon1 = radians(HOME_COORDINATES["lon"])
    R = 6371000  # Earth radius meters
    is_home = False
    min_dist = float("inf")
    count = 0

    for did, loc in list(active_devices.items()):
        if (now - loc["last_updated"]).total_seconds() > DEVICE_STALE_SEC:
            del active_devices[did]
            continue
        lat2, lon2 = radians(loc["lat"]), radians(loc["lon"])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        c = 2 * atan2(sqrt(a), sqrt(max(0.0, 1 - a)))
        dist = R * c
        if dist < min_dist:
            min_dist = dist
        if HOME_RADIUS_M > 0 and dist < HOME_RADIUS_M:
            is_home = True
        count += 1

    return jsonify({
        "is_home": is_home,
        "distance_meters": round(min_dist, 2) if min_dist != float("inf") else 0,
        "devices_tracked": count,
    })


# -----------------------------------------------------------------------------
# Activity logs
# -----------------------------------------------------------------------------
@app.route("/logs")
@login_required
def view_logs():
    logs = db_fetch("SELECT * FROM activity_log ORDER BY id DESC")
    return render_template("logs.html", logs=logs)


@app.route("/clear_all_logs", methods=["POST"])
@login_required
def clear_all_logs():
    if not _check_csrf():
        flash("Invalid request.", "danger")
        return redirect("/logs")
    try:
        db_execute("DELETE FROM activity_log")
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        add_activity_log(ts, "System Maintenance", "All activity logs cleared by admin")
    except Exception as e:
        app.logger.exception("clear_all_logs failed")
    return redirect("/logs")


@app.route("/add_log_manual", methods=["POST"])
@login_required
def add_log_manual():
    if not _check_csrf():
        return redirect("/logs")
    event_type = request.form.get("event_type", "").strip()
    status = request.form.get("status", "").strip()
    if not event_type or not status:
        return redirect("/logs")
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    add_activity_log(ts, event_type, status)
    return redirect("/logs")


@app.route("/delete_log/<int:id>", methods=["POST"])
@login_required
def delete_log(id):
    if not _check_csrf():
        return redirect("/logs")
    db_execute("DELETE FROM activity_log WHERE id=?", (id,))
    return redirect("/logs")


@app.route("/add_log", methods=["POST"])
def add_log():
    """Used by detector.py to record events. Requires X-API-KEY."""
    if request.headers.get("X-API-KEY") != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True)
    if not data or not all(k in data for k in ("timestamp", "event_type", "status")):
        return jsonify({"error": "Missing fields"}), 400
    add_activity_log(data["timestamp"], data["event_type"], data["status"])
    return jsonify({"status": "success"})


# -----------------------------------------------------------------------------
# Run
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    debug = os.getenv("SMARTGUARD_DEBUG", "false").lower() == "true"
    try:
        port = int(os.getenv("SMARTGUARD_PORT", "5000"))
    except (TypeError, ValueError):
        port = 5000
    host = "0.0.0.0" if debug else "127.0.0.1"
    
    if debug:
        app.run(host=host, port=port, debug=True)
    else:
        try:
            from waitress import serve
            print(f"[INFO] Server starting... Waitress binding to {host}:{port}")
            serve(app, host=host, port=port)
        except ImportError:
            print("[WARNING] Waitress not installed. Using development server.")
            app.run(host=host, port=port, debug=False)
