# SmartGuard – Full Project Breakdown (Aligned to Current Code)

This document explains **every major file, feature, and security choice** in the current `SmartGuard/` folder.

---

## 1. File Structure Overview

```text
SmartGuard/
|-- app.py              # Flask web app: dashboard, residents, logs, geofence API
|-- detector.py         # Vision engine: camera loop, face matching, siren, Telegram, recording
|-- requirements.txt    # Python dependencies
|-- .env.example        # Environment variable template (copy to .env)
|-- templates/
|   |-- index.html      # Admin dashboard: enrollment, resident list, live geofence status
|   |-- logs.html       # Activity log viewer / editor
|   |-- login.html      # Admin login portal
|   `-- mobile.html     # Mobile geofence remote (GPS-style updates, simulate away)
|-- alarm.wav           # Optional siren sound file (looped during major alerts)
|-- database/           # Created at runtime: resident folders and `.npy` face profiles
|-- static/             # Created at runtime: snapshots and breach videos
`-- smartguard.db       # Created at runtime: SQLite DB (residents + activity_log)
```

---

## 2. `app.py` – Web App, Database, and Geofencing API

`app.py` is the **backend brain**: it serves the web UI, manages the database, builds biometric profiles from videos, and exposes geofencing and logging APIs.

### 2.1 Imports and environment loading

- Uses `flask` and `werkzeug` for HTTP routing, templates, sessions, and safe file handling.
- Uses `sqlite3` to store residents and activity logs in a single file (`smartguard.db`).
- Uses `math` (sin, cos, atan2, sqrt) for the **Haversine formula** used in geofencing.
- Uses `cv2`, `face_recognition`, and `numpy` to extract and store face encodings.
- Uses `dotenv` (`load_dotenv()`) to pull secrets and configuration from `.env` instead of hardcoding them.

**Why**: This combination keeps the stack light (no external DB service), but still powerful enough to demo computer vision, login, and geofencing.

### 2.2 Core configuration and globals

- `app = Flask(__name__)` – main Flask app instance.
- `app.secret_key = SMARTGUARD_SECRET_KEY` – required to sign session cookies. If missing, the app **refuses to start**.
- `app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024` – caps uploads at **50 MB**.
- `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `SMARTGUARD_API_KEY` – pulled from `.env`.
  - The app refuses to start if `SMARTGUARD_ADMIN_PASSWORD` is still `change_me`.
  - The app refuses to start if `SMARTGUARD_API_KEY` is still `default_secure_key`.
- `HOME_COORDINATES` and `HOME_RADIUS_M` – parsed from environment using `_env_float` to avoid crashes if values are missing or malformed.
- `active_devices = {}` – in-memory map of device IDs to their latest GPS location and timestamp.

**Why**:
- Upload size limit defends against denial-of-service via huge uploads.
- `active_devices` lives in RAM because GPS pings are frequent and do **not** need durable storage.

### 2.3 CSRF handling (custom security layer)

Functions:
- `_ensure_csrf()` – generates a random `csrf_token` (24 random bytes, hex) and stores it in the Flask session.
- `inject_csrf()` – context processor that injects `csrf_token` into every Jinja template.
- `_check_csrf()` – verifies that the token in the submitted form matches the one in the session.

**Where used**:
- All important POST routes, such as resident enrollment, deletion, and log changes, call `_check_csrf()` and reject invalid tokens.

**Why**: Blocks Cross-Site Request Forgery, where a malicious site could try to make your browser perform sensitive actions while you are logged in.

### 2.4 Database helpers and schema

Functions:
- `init_db()` – creates two tables if they do not exist:
  - `residents(id, name, path)`
  - `activity_log(id, timestamp, event_type, status)`
- `db_execute(query, args, many=False)`, `db_fetch`, `db_fetchone` – thin wrappers around SQLite connections with `timeout=10`.

**Why**:
- `timeout=10` allows concurrent write attempts (e.g., web app and detector) to queue briefly, instead of failing with "database is locked".
- All queries use **parameterized SQL** to prevent injection.

### 2.5 Authentication system

Components:
- `login_required` decorator – guards sensitive routes.
- `/login` – handles GET (form) + POST (credential check).
- `/logout` – clears the `logged_in` flag from the session.

**Flow**:
1. If `logged_in` is not in the session, visiting `/`, `/logs`, or `/mobile` redirects to `/login`.
2. On successful login, `logged_in` is set to `True`, and user is redirected to the dashboard.

**Why**: Keeps admin functionality (faces, logs, mobile remote) behind a username/password gate.

### 2.6 Resident enrollment and deletion

Key functions:
- `/upload`:
  - Validates CSRF token, resident name, and presence of a file.
  - Uses `secure_filename()` to sanitize the submitted name for filesystem use.
  - Creates `database/<n>/`, removing any previous folder with the same name.
  - Saves a temp video, passes it to `_extract_face_encodings_from_video`, and deletes temp file.
  - Averages encodings into a master profile and saves `<n>_profile.npy`.
  - Upserts the resident into the `residents` table and logs the event.

- `_extract_face_encodings_from_video`:
  - Opens the video with `cv2.VideoCapture`.
  - Samples every 10th frame and resizes to 25% of original size to speed up `face_recognition`.
  - Collects one encoding per sampled face, returning a list of 128-D vectors.

- `/delete/<id>`:
  - Verifies CSRF.
  - Looks up the resident, removes the folder under `database/`, deletes the DB row.

**Why**:
- Skipping frames and shrinking images keeps profiling responsive.
- Averaging encodings over multiple frames makes profiles robust to pose and lighting changes.

### 2.7 Geofencing API (`/update_location`, `/get_system_status`)

Functions:
- `_cleanup_stale_devices()` – removes devices with no update for > 300 seconds (5 minutes).
- `/update_location`:
  - Requires `X-API-KEY` to match `SMARTGUARD_API_KEY`.
  - Accepts JSON with `lat`, `lon`, `device_id`.
  - Validates coordinate ranges and stores them in `active_devices`.
- `/get_system_status`:
  - Requires `X-API-KEY`.
  - Computes the shortest distance (in meters) between any active device and the home coordinates using the **Haversine formula**.
  - Returns JSON: `{ is_home, distance_meters, devices_tracked }`.

**Why**:
- Using a header key instead of cookies suits both the detector script and the mobile web page.
- In-memory geofence state is fast and resets safely on restart.

### 2.8 Activity logging

Routes:
- `/logs` – shows the audit trail.
- `/clear_all_logs` – wipes `activity_log` (CSRF-protected).
- `/add_log_manual` – lets the admin add free-text entries.
- `/delete_log/<id>` – deletes a single log entry (CSRF-protected).
- `/add_log` – API endpoint used by `detector.py` via `X-API-KEY` to record events programmatically.

**Why**:
- Keeps a human-readable history of system events (enrollment, revocations, alarms, etc.).
- Allows detector and admin actions to share one central ledger.

### 2.9 App startup

At the bottom:
- Checks `SMARTGUARD_DEBUG` to decide `debug=True/False` and which host to bind to.
- When `SMARTGUARD_DEBUG=false` (default), the app binds to `127.0.0.1` (localhost only).
- When `SMARTGUARD_DEBUG=true`, the app binds to `0.0.0.0` for LAN testing.

**Two ways to start the app**:

```powershell
# Flask development server (localhost only, fine for local demos)
python app.py

# Waitress production server (recommended for LAN or Ngrok access)
python -m waitress --host=0.0.0.0 --port=5000 app:app
```

Use Waitress when you need the dashboard accessible from another device (e.g., your phone for the `/mobile` page) or when running behind an Ngrok tunnel. Waitress handles concurrent requests correctly and does not display the Flask development-server warning.

---

## 3. `detector.py` – Vision Engine and Escalation Logic

`detector.py` is the **sensor and responder**: it reads the webcam, runs face recognition, polls geofence status, manages the siren, and records breach videos.

### 3.1 Configuration constants

- Paths (`DB_PATH`, `STATIC_PATH`) are based on the script's own folder for safe relative operation.
- Timing and behavior constants (each has an inline comment explaining its role):
  - `FRAME_SKIP = 10` – run face recognition every N frames to reduce CPU load.
  - `PRE_ROLL_SEC = 5` – seconds of frames buffered before an alarm triggers.
  - `AREA_CLEAR_SEC = 5` – seconds with no face before the area is considered empty.
  - `PROMPT_AT_SEC = 15` – unknown person for 15s → Telegram prompt + snapshot.
  - `MAJOR_ALERT_AT_SEC = 30` – unknown person for 30s → full alarm + recording.
  - `PERSISTENT_RESET_SEC = 60` and `RE_TRIGGER_AFTER_SEC = 10` – control how persistent alerts and retriggers behave.
  - `VIDEO_RECORD_SEC = 15` – recording length after major alert.
  - `FACE_MATCH_THRESHOLD = 0.50` – stricter than default to reduce false positives.

**Why**: These constants define how "paranoid" the system is and how long it waits before escalating.

### 3.2 Constructor (`__init__`) and state

Key state:
- `self.known_profiles` – loads `*.npy` profiles from `database/<n>/<n>_profile.npy` into memory.
- Booleans like `self.owner_is_home`, `self.is_alarm_active`, `self.disarm_requested`, `self.guest_authorized`, `self.force_escalation` – capture system mode.
- `self.is_processing_face`, `self.last_known_identity`, `self.current_display_text` – hold face recognition state and overlay text.
- Recording state: `self.is_recording`, `self.record_end_time`, `self.active_record_queues`.
- `self.face_cascade` – Haar cascade for fast face detection.

**Why**:
- Keeping all shared state as instance attributes (not globals) makes multi-threaded logic safer and easier to reason about.

### 3.3 Housekeeping and logging helpers

- `_cleanup_old_media()` – deletes files under `static/` older than 7 days.
- `_log_event(event_type, status)` – spawns a thread that POSTs to `/add_log` on `app.py` with a timestamp and description.

**Why**:
- Prevents unbounded growth of media.
- Non-blocking logging avoids frame drops when network calls are slow.

### 3.4 Background workers

- `_geofence_worker`:
  - Polls `/get_system_status` every 2 seconds with `X-API-KEY`.
  - Updates `self.owner_is_home`.
  - Hot-reloads resident profiles when the set of `.npy` files in `database/` changes (detects both additions and deletions).
  - Optionally sends periodic heartbeats to `SMARTGUARD_HEARTBEAT_URL` (uptime monitoring).
  - Silently ignores short errors so temporary network glitches don't trigger false alarms.

#### Healthcheck bot (heartbeat monitoring)

If `SMARTGUARD_HEARTBEAT_URL` is set in `.env`, the detector will periodically `GET` that URL (about once per minute). This is designed for services like Healthchecks.io so you can detect when the detector process is offline.

- `_siren_worker`:
  - If `self.is_alarm_active` is true, plays `alarm.wav` in a loop (if present) or beeps.
  - If alarm is turned off, stops the sound.

- `_telegram_worker`:
  - Long-polls Telegram for callback queries (button presses).
  - Handles:
    - `authorize_guest` → `self.guest_authorized = True`
    - `force_alarm` → `self.force_escalation = True`
    - `disarm_system` → `self.is_alarm_active = False`, `self.disarm_requested = True`

**Why**: Background threads keep network and audio operations from blocking the main camera loop.

### 3.5 Vision & recording workers

- `_process_face_background(frame_copy)`:
  - Converts frame to RGB, finds face locations, and encodes the first face.
  - Compares encoding to all known profiles using `face_recognition.face_distance`.
  - If a match is under `FACE_MATCH_THRESHOLD`, marks that name as `AUTHORIZED`.
  - Otherwise, clears `last_known_identity`.
  - Always resets `self.is_processing_face` in the `finally` block so the main loop can queue the next call.

- `_video_writer_worker(filepath, frame_size, q, fps)`:
  - Consumes frames from a `queue.Queue` and writes them to an `.mp4` file.
  - Stops when it reads the sentinel value `None` (normal end) or `'CANCEL'` (alarm cleared early; partial file is deleted).
  - If Telegram is configured, sends the video as a message with a disarm button.

**Why**:
- Heavy face encoding and disk I/O live off the main thread so the UI stays smooth.
- Queue + sentinel pattern guarantees clean shutdown of video writers.

### 3.6 Main loop (`start()`)

High-level steps:
1. Open camera; if failed, print error and exit.
2. Clean up old media.
3. Measure camera FPS and create a `deque` to hold the last `PRE_ROLL_SEC` seconds of frames.
4. Start background workers (`_geofence_worker`, `_siren_worker`, `_telegram_worker` if enabled).
5. Enter `while True`:
   - Read frame; on failure, release the old handle and reopen the camera before retrying.
   - Append to pre-roll buffer.
   - If `owner_is_home`:
     - Set `current_display_text` to Home/passive and clear all alarms and timers.
   - Else (Away mode):
     - Run Haar cascade to detect faces (fast).
     - Every `FRAME_SKIP` frames, trigger `_process_face_background` if not already running.
     - Detect if area is clear for > `AREA_CLEAR_SEC`; if so, reset `last_known_identity`, prompt flags, and persistent state.
     - If a known resident or authorized guest is present, clear timers and show `AUTHORIZED`.
     - Otherwise, manage:
       - `dwell_timer` – how long unknown person has been present.
       - 15s **Tier 1** prompt (snapshot + Telegram photo with buttons).
       - 30s **Tier 2** escalation to alarm + recording.
       - Remote overrides (force escalation, disarm).
   - If `is_recording`:
     - Push frames into all active recording queues.
     - Overlay blinking red REC dot and text.
     - When `record_end_time` is reached, send `None` to queues and clear them.
   - Draw status text (`current_display_text`) on frame and show via `cv2.imshow`.
6. On `'q'` key press, break, send `None` to any remaining queues, release camera, destroy window.

**Why**:
- The main loop weaves together geofence state, face recognition results, and operator input to drive a clear escalation ladder from "idle" → "observing" → "prompting" → "alarm + recording".

---

## 4. Templates – Frontend Behavior

### 4.1 `index.html` – Admin dashboard

Roles:
- **Enrollment UI**: form to upload `.mp4` videos and name residents.
- **Authorized database table**: lists all residents with `Revoke` buttons.
- **Live status bar**: shows GPS/geofence status (`PASSIVE (HOME)` vs `ACTIVE (AWAY)`).

Behavior:
- Uses Bootstrap 5 and custom dark theme for a clean, modern UI.
- Uses Jinja to loop over `residents` and build the table.
- Includes CSRF token hidden fields in forms.
- JavaScript `updateStatus()`:
  - Calls `/get_system_status` every 3 seconds with `X-API-KEY` (injected as `{{ api_key }}`).
  - Updates a badge and text based on `is_home`, distance in meters, and devices tracked.

### 4.2 `login.html` – Authentication portal

Roles:
- Entry point to the admin interface.

Behavior:
- Centered card with username/password inputs.
- Includes a CSRF token hidden input to protect the login POST.
- Shows an error badge when credentials are invalid.

### 4.3 `logs.html` – Activity ledger

Roles:
- Shows the `activity_log` entries in a styled table.
- Provides:
  - Manual log add form (with CSRF token).
  - Single log delete buttons (CSRF-protected).
  - "Clear all logs" button (CSRF-protected) with a confirmation prompt.

Behavior:
- Uses Jinja to render logs and color-code badges based on event type (e.g., Breach, Access).

### 4.4 `mobile.html` – Smartphone remote

Roles:
- Acts as a **virtual key fob** using your phone's GPS.

Behavior:
- On first load, generates a persistent `deviceId` in `localStorage`.
- Buttons:
  - **Send Exact Location**: one-off GPS update via `navigator.geolocation.getCurrentPosition`, POSTing to `/update_location` with `X-API-KEY`.
  - **Simulate "Away"**: sends fake coordinates `(0.0001, 0.0001)` to mimic being far away.
- **Live Radar**:
  - Toggles `navigator.geolocation.watchPosition` to continuously send location every few seconds.
  - UI shows status text and uses a "radar" animation on the icon.
- Includes clean shutdown of Live Radar when switching modes (e.g., sending exact location or simulate away).

---

## 5. `.env.example` – Configuration Template

Lists:
- Web and API settings:
  - `SMARTGUARD_SECRET_KEY`, `SMARTGUARD_ADMIN_USERNAME`, `SMARTGUARD_ADMIN_PASSWORD`, `SMARTGUARD_API_KEY`, `SMARTGUARD_PORT`, `SMARTGUARD_DEBUG`.
- Geofencing:
  - `SMARTGUARD_HOME_LAT`, `SMARTGUARD_HOME_LON`, `SMARTGUARD_HOME_DISTANCE_METERS`.
- Optional integrations:
  - `SMARTGUARD_TELEGRAM_TOKEN`, `SMARTGUARD_TELEGRAM_CHAT_ID`, `SMARTGUARD_HEARTBEAT_URL`.

**How used**:
- You copy `.env.example` to `.env` and fill real values.
- `python-dotenv` loads these values into environment variables at runtime.

---

## 6. `.gitignore` – What Stays Local

Key entries:
- `.env`, `.env.*.local` – secrets.
- `smartguard.db`, `database/` – biometric and event data.
- `static/*.mp4`, `static/*.jpg` – generated snapshots and videos.
- Virtual environments (`venv/`, `.venv/`, etc.) and Python cache folders.
- IDE and OS artifacts (`.vscode/`, `.idea/`, `.DS_Store`, `Thumbs.db`).

**Goal**: Ensure sensitive and heavy files are never accidentally committed to Git.
