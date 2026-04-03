# SmartGuard Technical Deep Dive

## 1. Architecture

SmartGuard is split into two cooperating Python processes:

- `app.py` – the **command hub**:
  - Hosts the Flask web app for login, enrollment, logs, and the mobile remote.
  - Owns the SQLite database (`smartguard.db`) and all resident and activity records.
  - Exposes JSON APIs for geofencing (`/update_location`, `/get_system_status`) and detector logging (`/add_log`).

- `detector.py` – the **edge detector**:
  - Owns the webcam loop, face recognition, alarm state, and breach recording.
  - Talks to the Flask app using the API key to:
    - Read the current “is_home” status,
    - Append structured log entries.

Responsibility is intentionally divided:
- The web app stays responsive and focuses on configuration, control, and presentation.
- The detector can afford to run tight real-time loops and background worker threads without blocking HTTP requests.

## 2. Resident Enrollment

The enrollment pipeline turns a short video into a stable numeric profile:

1. An admin opens the dashboard and submits a form with:
   - A resident name, and
   - A 5–10 second `.mp4` video of the person’s face moving slightly.
2. `app.py`:
   - Sanitizes the name with `secure_filename` and prepares `database/<name>/`.
   - Opens the video with OpenCV.
   - Samples every 10th frame to keep processing time manageable.
   - Shrinks each sampled frame and runs `face_recognition.face_encodings` to get 128‑D vectors.
3. All collected encodings are averaged via `numpy.mean(encodings, axis=0)` into a single master profile.
4. The profile is saved as `<name>_profile.npy` under `database/<name>/`, and the `residents` table is updated.

If an admin re‑enrolls the same name, the on‑disk profile is overwritten so the detector always sees the latest biometric data.

## 3. Geofencing

Geofencing decides whether the system should be calm or armed:

- The `/mobile` page:
  - Generates a persistent `device_id` in `localStorage`.
  - Offers:
    - A **Send Exact Location** button (one‑off GPS update), and
    - A **Simulate "Away"** button (sends a far‑away fake coordinate),
    - A **Live Radar** toggle that streams GPS updates periodically.
  - Each update hits `/update_location` with `lat`, `lon`, and `device_id` plus `X-API-KEY`.

- `app.py`:
  - Stores device locations and timestamps in an in‑memory `active_devices` dictionary.
  - Regularly purges entries older than 5 minutes.
  - Uses the Haversine formula to compute the shortest distance from home coordinates to each device.
  - Returns `is_home`, `distance_meters`, and `devices_tracked` from `/get_system_status`.

- `detector.py`:
  - Polls `/get_system_status` in a lightweight background thread.
  - Sets `owner_is_home` based on the JSON result:
    - When `is_home` is true, it suspends alarms and shows `HOME (Passive Mode)`.
    - When `is_home` is false, it arms the observation and escalation logic.

## 4. Detector Escalation

The detector’s main loop is built as a small state machine:

- **Face detection and recognition**
  - Each frame is checked with a Haar cascade to see if any faces are present.
  - Every `FRAME_SKIP` frames, if no recognition is in progress, a background thread:
    - Extracts a face encoding from the current frame,
    - Compares it to all known profiles,
    - Marks `last_known_identity` if a resident is recognized.

- **Home vs Away behavior**
  - If `owner_is_home` is true:
    - Alarm flags, timers, and guest authorization are cleared.
    - The overlay shows `HOME (Passive Mode)`.
  - If `owner_is_home` is false:
    - The loop watches for:
      - Known residents (safe, no timers),
      - Unknown faces (start/advance a dwell timer),
      - Telegram commands (guest authorized, force alarm, disarm).

- **Escalation ladder**
  - When an unknown person remains in view:
    - At ~15 seconds (`PROMPT_AT_SEC`), the detector:
      - Captures a snapshot into `static/`,
      - Sends a Telegram photo with buttons to authorize a guest or sound the alarm.
    - At ~30 seconds (`MAJOR_ALERT_AT_SEC`), or earlier if “force alarm” is pressed:
      - Sets `is_alarm_active = True` (wakes the siren worker),
      - Starts recording with a pre‑roll buffer,
      - Sends a “major alert” message to Telegram.
  - When the area becomes empty for more than a few seconds, the detector:
    - Clears `last_known_identity` and guest authorization,
    - May mark a persistent alert window and then reset after a cool‑down.

## 5. Recording Flow

To avoid blocking the camera loop on disk I/O, recording is fully asynchronous:

1. While idle or observing, `detector.py` maintains a rolling `deque` of recent frames sized to `PRE_ROLL_SEC * fps`.
2. When a major alert is triggered:
   - It creates a `queue.Queue` for this incident.
   - It pushes the pre‑roll frames into the queue.
   - It starts a background writer thread that:
     - Opens an `.mp4` file under `static/`,
     - Pulls frames from the queue and writes them until it receives a sentinel `None`.
3. During the recording window (`VIDEO_RECORD_SEC` seconds), the main loop:
   - Feeds each new frame into any active recording queues,
   - Displays a blinking red REC indicator on the overlay.
4. When the recording window expires, the main loop:
   - Sends `None` into each active queue,
   - Clears the queue list so threads can shut down cleanly.
5. If the owner returns home or the alarm is disarmed mid‑recording, the detector stops further frames and closes the queues early.

## 6. Security Controls (summary)

- Forms that change data (enroll, revoke, clear logs, manual logs) include a CSRF token generated per session and are rejected if it does not match the server’s copy.
- Key pages (`/`, `/logs`, `/mobile`) are protected by a simple session-based login.
- Geofencing and detector logging APIs (`/update_location`, `/get_system_status`, `/add_log`) require the `X-API-KEY` header and never rely on browser cookies.
- Resident names are sanitized with `secure_filename`, and all database access uses parameterized SQLite queries with a small timeout to avoid locks.
- The app enforces a 50 MB upload limit, and the detector removes media older than seven days from `static/` on startup.
- In this college-focused build, the API key is injected into browser JavaScript for `/` and `/mobile` to keep testing simple; a production design would remove this and rely on stricter session-only flows and HTTPS.

## 7. Operational Notes

- The web app is typically run via `python app.py` using Flask’s development server for local demos.
- Generated media is cleaned up on detector startup based on a seven-day retention window.
