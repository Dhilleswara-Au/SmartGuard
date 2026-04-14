# SmartGuard 🛡️

**SmartGuard** is a self-hosted, AI-powered home security system that runs on a standard Windows PC. It combines real-time facial recognition, geofence-based home/away detection, a Telegram alert bot, and a web dashboard into a single, easy-to-configure application.

> No cloud subscription. No monthly fees. Just plug in a camera and run it.

---

## ✨ Features

- **Face Recognition** — Enroll residents by uploading a short selfie video. SmartGuard extracts and averages facial encodings to build a robust profile, then identifies known faces in real time via your webcam.
- **Tiered Alarm Escalation** — Unknown presence triggers a configurable escalation ladder: silent observation → Telegram snapshot with authorization buttons → full siren alarm + breach recording.
- **Geofencing** — Mobile devices post their GPS coordinates to the server. When the owner is detected within the configured home radius, the system automatically switches to Passive Mode and the alarm is suppressed.
- **Telegram Bot Integration** — Receive live snapshots when an unknown person is detected. Tap inline buttons directly in Telegram to authorize a guest, force the alarm, or disarm the siren — no app required.
- **Breach Recording** — Automatically records a video clip (with pre-roll buffer) when an alarm is triggered. Clips are stored locally and accessible from the dashboard.
- **Web Dashboard** — A Flask-powered dashboard lets you manage resident profiles, monitor live system status, review the activity log, and control the system remotely.
- **Mobile Interface** — A dedicated mobile-optimized page allows phone-based remote control and location sharing.
- **Activity Log** — All security events (detections, alarms, profile changes) are logged to a local SQLite database and viewable in the dashboard.
- **Heartbeat Monitoring** — Optional integration with uptime services (e.g., Healthchecks.io) to alert you if the system goes offline.

---

## 🏗️ Architecture

```
┌─────────────────────────────┐      ┌──────────────────────────┐
│        detector.py          │      │          app.py           │
│  - Webcam capture loop      │◄────►│  - Flask web server       │
│  - Face recognition         │  API │  - Resident management    │
│  - Alarm escalation         │      │  - Geofencing endpoints   │
│  - Breach video recording   │      │  - Activity log           │
│  - Telegram callbacks       │      │  - Admin dashboard        │
└─────────────────────────────┘      └──────────────────────────┘
            │                                    │
            └──────────────┬─────────────────────┘
                           │
                    smartguard.db (SQLite)
                    database/  (face profiles)
                    static/    (snapshots, clips)
```

---

## 📋 Requirements

- **OS:** Windows (the siren uses `winsound`)
- **Python:** 3.9 or higher
- **Camera:** Any USB or built-in webcam recognized by OpenCV
- **CMake:** Required to build `dlib` (the engine behind `face_recognition`)

---

## ⚡ Installation

### 1. Install CMake

Download and install CMake from [cmake.org](https://cmake.org/download/). Make sure to check **"Add CMake to system PATH"** during installation.

### 2. Clone the repository

```bash
git clone https://github.com/your-username/SmartGuard.git
cd SmartGuard
```

### 3. Create and activate a virtual environment

```bash
python -m venv venv
venv\Scripts\activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** Installing `face_recognition` (which builds `dlib` from source) can take several minutes. This is normal.

### 5. Configure your environment

Copy the example file and fill in your values:

```bash
copy .env.example .env
```

Open `.env` in a text editor and set the required values (see [Configuration](#%EF%B8%8F-configuration) below).

---

## ⚙️ Configuration

All configuration is done via the `.env` file. **Never commit this file to version control** — it contains secrets.

| Variable | Required | Description |
|---|---|---|
| `SMARTGUARD_SECRET_KEY` | ✅ | A long random string for Flask session signing |
| `SMARTGUARD_ADMIN_USERNAME` | ✅ | Dashboard login username |
| `SMARTGUARD_ADMIN_PASSWORD` | ✅ | Dashboard login password (cannot be `change_me`) |
| `SMARTGUARD_API_KEY` | ✅ | Shared secret used by the detector and mobile app |
| `SMARTGUARD_PORT` | — | Port to run the server on (default: `5000`) |
| `SMARTGUARD_DEBUG` | — | Set `true` to use Flask dev server; `false` (default) uses Waitress |
| `SMARTGUARD_HOME_LAT` | — | Home latitude for geofencing (decimal degrees) |
| `SMARTGUARD_HOME_LON` | — | Home longitude for geofencing (decimal degrees) |
| `SMARTGUARD_HOME_DISTANCE_METERS` | — | Geofence radius in meters (default: `100`) |
| `SMARTGUARD_TELEGRAM_TOKEN` | — | Telegram bot token (from [@BotFather](https://t.me/BotFather)) |
| `SMARTGUARD_TELEGRAM_CHAT_ID` | — | Your Telegram chat ID for receiving alerts |
| `SMARTGUARD_HEARTBEAT_URL` | — | Optional uptime monitoring URL (pinged every 60s) |

To generate a secure random key, run:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 🚀 Running SmartGuard

SmartGuard has two components that run simultaneously. Open two terminals (both with your virtual environment activated).

**Terminal 1 — Start the web server:**
```bash
python app.py
```

**Terminal 2 — Start the detector:**
```bash
python detector.py
```

Then open your browser and navigate to `http://127.0.0.1:5000`.

### Remote Access (Optional)

To access your dashboard from outside your home network, you can use the included `ngrok.exe`:

```bash
ngrok http 5000
```

This gives you a public HTTPS URL. Use this URL in your mobile browser and for the mobile geofencing feature.

---

## 👤 Enrolling a Resident

1. Open the dashboard at `http://127.0.0.1:5000`.
2. In the **Residents** panel, enter the person's name.
3. Record a 5–10 second video of their face in good lighting (looking slightly left, right, and straight ahead works best).
4. Upload the video and click **Enroll**.
5. SmartGuard will process the video, extract face encodings, and save the profile. The detector picks up new profiles automatically without a restart.

---

## 📱 Mobile Geofencing

1. Open the mobile page: `http://<your-server-ip-or-ngrok-url>/mobile`
2. Log in with your admin credentials.
3. Grant location permissions when prompted.
4. The page will continuously push your GPS coordinates to the server. When you're within the home radius, the detector enters **Passive Mode** and disables all alerts.

---

## 🔔 Telegram Setup

1. Message [@BotFather](https://t.me/BotFather) on Telegram, create a new bot, and copy the token.
2. Start a chat with your new bot, then visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser to find your `chat_id`.
3. Add both values to your `.env` file.

Once configured, SmartGuard will:
- Send a **photo with action buttons** when an unknown person has been present for 15 seconds.
- Send a **siren alert with a disarm button** when the full alarm triggers at 30 seconds.

---

## 🔒 Security Notes

- The app enforces CSRF protection on all state-changing web routes.
- All API endpoints (`/add_log`, `/update_location`, `/get_system_status`) require the `X-API-KEY` header.
- The server runs on `127.0.0.1` in production mode (via Waitress) and only exposes itself to the local machine. Use a reverse proxy (e.g., ngrok, Nginx) for remote access.
- Face profile `.npy` files are stored locally and never leave your machine.

---

## 📁 Project Structure

```
SmartGuard/
├── app.py                  # Flask web server & REST API
├── detector.py             # Camera loop, face recognition, alarm logic
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variable template
├── alarm.wav               # Siren audio file
├── ngrok.exe               # Optional: tunnel for remote access
├── templates/
│   ├── index.html          # Main dashboard
│   ├── login.html          # Login page
│   ├── logs.html           # Activity log viewer
│   └── mobile.html         # Mobile remote control & geofencing
├── database/               # Resident face profiles (auto-created)
└── static/                 # Snapshots & breach recordings (auto-created)
```

---

## 🤝 Contributing

Contributions, bug reports, and feature requests are welcome. Please open an issue before submitting a pull request so we can discuss the change.

---

## 📄 License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
