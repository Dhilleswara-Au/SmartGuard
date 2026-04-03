SmartGuard is a Windows-first home security demo that combines resident face recognition, GPS-style geofencing, and Telegram-driven escalation. The project is split into two Python processes:

- `app.py` runs the Flask web app for resident enrollment, system status, and log viewing.
- `detector.py` runs the camera loop, face recognition, geofence polling, local alarm, and breach recording.

## Core Features

- **Resident enrollment and biometrics**
  - Upload a short `.mp4` video of a person turning their head.
  - `app.py` samples every 10th frame, shrinks it, and uses `face_recognition` to extract encodings.
  - All encodings are averaged into a single `.npy` profile in `database/<name>/<name>_profile.npy`.
  - The detector loads these profiles into RAM at startup for fast comparisons.

- **GPS-like geofencing and Home/Away logic**
  - The `/mobile` page uses your phone's GPS (or simulated coordinates) to POST latitude/longitude to `/update_location`.
  - `app.py` keeps recent device locations in memory and uses the Haversine formula to decide whether any device is inside the configured radius.
  - `detector.py` polls `/get_system_status` and switches between **HOME (Passive Mode)** and **ACTIVE (AWAY)** without manual toggles.

- **Dashboard, logs, and admin workflows**
  - A login-protected dashboard shows enrolled residents, lets you enroll/revoke profiles, and exposes a dedicated `/logs` page.
  - All important changes (profile updates, revocations, manual notes, detector events) are stored in `smartguard.db` and visible as an audit trail.

- **Timed escalation with Telegram**
  - When the system is in Away mode and sees an unknown face:
    - It starts a dwell timer and shows `OBSERVING (Xs)` on the camera feed.
    - After ~15 seconds, it sends a snapshot with inline buttons to Telegram.
    - After ~30 seconds (or earlier via a "force alarm" button), it sounds the siren and records a breach clip.
  - Telegram buttons let you authorize a guest, force recording early, or disarm the siren remotely.

- **Local alarm and breach recording**
  - When a major alert triggers, `detector.py`:
    - Starts playing `alarm.wav` (or system beeps) in a loop.
    - Uses a rolling frame buffer so the saved `.mp4` video includes a few seconds of "pre-roll" before the trigger moment.
    - Streams frames into a background writer thread and optionally uploads the finished clip to Telegram.

## How the two main Python files work

- **`app.py` – web app and database**
  - Hosts the Flask site (`/`, `/login`, `/logs`, `/mobile`) and handles:
    - Admin login and session tracking.
    - CSRF-protected forms for enroll/revoke/log actions.
    - Resident profile creation from uploaded videos (using OpenCV + `face_recognition` + NumPy).
    - A SQLite database (`smartguard.db`) with helper functions for safe, parameterized queries.
    - Geofencing JSON APIs: `/update_location` and `/get_system_status` (secured by `X-API-KEY`).
    - A simple `/add_log` API used by the detector to append events.

- **`detector.py` – vision engine and escalation logic**
  - Opens the webcam, loads known face profiles, and spawns background threads for:
    - Geofence polling (`_geofence_worker`),
    - Siren control (`_siren_worker`),
    - Telegram callback handling (`_telegram_worker`).
  - Uses a fast Haar cascade for face presence detection and a slower background thread for actual face recognition.
  - Maintains timers and state to move from observing → Telegram prompt → full alarm + recording.
  - Writes breach videos and snapshots to `static/` and logs important events back to the Flask app.

## Project Layout

```text
SmartGuard/
|-- app.py
|-- detector.py
|-- requirements.txt
|-- .env.example
|-- templates/
|   |-- index.html
|   |-- logs.html
|   |-- login.html
|   `-- mobile.html
|-- alarm.wav         # optional siren sound (looped by detector)
|-- database/         # created at runtime; holds resident profiles
|-- static/           # created at runtime; holds snapshots and recordings
`-- smartguard.db     # created at runtime; activity log database
```

## Requirements

- Windows 10 or Windows 11
- Python 3.10 or newer
- A webcam available at `cv2.VideoCapture(0)`
- CMake and Visual Studio Build Tools if `face_recognition` and `dlib` need to compile locally

## Setup

1. Create and activate a virtual environment.
2. Install dependencies with:
   ```powershell
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env`.
4. Fill in the required values:
   - `SMARTGUARD_SECRET_KEY` – any long random string (e.g., 32+ random characters)
   - `SMARTGUARD_ADMIN_PASSWORD` (must not be `change_me`)
   - `SMARTGUARD_API_KEY` – any random string shared between app.py and detector.py
   - `SMARTGUARD_HOME_LAT` and `SMARTGUARD_HOME_LON` – your home's decimal-degree coordinates
5. Optionally add:
   - `SMARTGUARD_TELEGRAM_TOKEN` and `SMARTGUARD_TELEGRAM_CHAT_ID` for remote alerts
   - `SMARTGUARD_HEARTBEAT_URL` for uptime monitoring

## Optional: Healthcheck bot (uptime heartbeat)

If you set `SMARTGUARD_HEARTBEAT_URL`, `detector.py` will send a simple HTTP `GET` to that URL about once per minute. This is meant for uptime monitoring services such as Healthchecks.io (or any endpoint you control) so you can get alerted if the detector stops running.

- Example: create a check in your monitoring service and paste its "ping URL" into `SMARTGUARD_HEARTBEAT_URL`.
- If you leave it blank, the detector simply skips heartbeats.

## Running the System

### Option A – Flask development server (local demos only)

Start the web app:

```powershell
python app.py
```

When `SMARTGUARD_DEBUG=false` (default), the app binds to `127.0.0.1` (localhost only). Set `SMARTGUARD_DEBUG=true` if you want to test from another device on your LAN.

### Option B – Waitress production server (recommended for demos on a LAN or via Ngrok)

`waitress` is a pure-Python WSGI server included in `requirements.txt`. It is more stable than Flask's development server and handles concurrent requests correctly:

```powershell
python -m waitress --host=0.0.0.0 --port=5000 app:app
```

Use this command when you need the dashboard to be reachable from other devices (e.g., your phone for the `/mobile` page) or when running the app over an Ngrok tunnel.

Start the detector in a second terminal (same in both cases):

```powershell
python detector.py
```

## Accessing the Mobile Remote (`/mobile`) from Your Phone

The `/mobile` page is the GPS remote that controls the Home/Away state. To open it on your phone:

1. Make sure both your PC and your phone are on the **same Wi-Fi network**.
2. Start the app with Waitress using `--host=0.0.0.0` (see Option B above).
3. Find your PC's local IP address:
   ```powershell
   ipconfig
   ```
   Look for the IPv4 address under your active Wi-Fi adapter (e.g., `192.168.1.42`).
4. On your phone's browser, navigate to:
   ```
   http://<your-pc-ip>:5000/mobile
   ```
   For example: `http://192.168.1.42:5000/mobile`
5. Log in with your admin credentials, then use the buttons to send your GPS location or simulate being away.

> **Note**: You must be logged in on the same browser session (or log in at `/login` on your phone first) before `/mobile` will load.

## Optional: Remote Access via Ngrok

If you want the dashboard and mobile remote accessible from outside your home network (e.g., to test Telegram callbacks while away), you can use [Ngrok](https://ngrok.com):

1. Download `ngrok.exe` from [ngrok.com/download](https://ngrok.com/download) and place it in your project folder (or anywhere on your PATH).
2. Start the app with Waitress on port 5000 (see Option B above).
3. In a third terminal, run:
   ```powershell
   .\ngrok.exe http 5000
   ```
4. Ngrok will display a public HTTPS URL such as `https://abc123.ngrok-free.app`.
5. Open that URL in any browser or on your phone to access the dashboard remotely.
6. Use `<ngrok-url>/mobile` on your phone for GPS-based geofencing from anywhere.

> **Important**: Ngrok free-tier tunnels generate a new URL every time you restart. The detector always talks to `127.0.0.1:5000` internally so it does not need to be updated. Only external browser/mobile access uses the Ngrok URL.

## Dashboard Walkthrough

1. Open `http://localhost:5000` (or your LAN/Ngrok URL).
2. Sign in with the admin credentials from `.env`.
3. Enroll resident profiles from the dashboard — upload a 5–10 second `.mp4` face video.
4. Open `/mobile` from your phone to send GPS updates and switch between Home and Away mode.
5. Watch the detector window for live status (`HOME (Passive Mode)`, `OBSERVING`, `ALARM ACTIVE / RECORDING`).

## Operations

- The dashboard `/` shows enrolled residents and lets you add / revoke profiles.
- `/logs` shows the activity log stored in `smartguard.db` and lets you add or clear entries.
- `/update_location` and `/get_system_status` are JSON endpoints used by both the detector and the mobile page. They require the `X-API-KEY` header and accept simple latitude/longitude payloads.
- The detector removes generated media in `static/` that are older than seven days on startup.

## Verifying the System

Before marking the build as ready, run through the manual test plan to confirm every feature works end-to-end:

```
docs/manual_tests.md
```

The test plan covers enrollment, geofencing, escalation timers, Telegram callbacks, security controls, and edge cases — with clear steps and expected results for each scenario.

## Security Features (college-safe defaults)

- `.env` + `.gitignore` keep secrets, biometric profiles, the SQLite database, and generated media out of Git.
- Forms that change data (enroll, revoke, log operations) include a session-backed CSRF token and are rejected if the token is missing or invalid.
- Dashboard, logs, and mobile pages are wrapped in a simple session-based login (`login_required`).
- All database reads/writes use parameterized SQLite queries via small helper functions.
- The Flask app enforces a 50 MB upload limit, and `detector.py` cleans up media in `static/` older than seven days on startup.
- Geofencing and detector log APIs (`/update_location`, `/get_system_status`, `/add_log`) require the `X-API-KEY` header; in this local demo, that key is also injected into browser JavaScript purely for convenience.

If you ever plan to deploy SmartGuard publicly, you should move browser flows away from the shared API key and add HTTPS, secure cookies, rate limiting, and stricter security headers.

## Notes for Reviewers / Instructors

- The main goal of this project is to demonstrate:
  - Face-based resident profiling with `face_recognition`.
  - A simple geofencing model using the Haversine formula.
  - Timer-based escalation with Telegram callbacks and video recording.
- The code is intentionally kept small and readable for teaching and review. It is not a production security system without further hardening.
