# SmartGuard – Manual Test Plan

For each test you get:
- **Why** we run it (purpose)
- **How** to run it (steps)
- **What** you should see (expected result)

---

## General Prerequisites

- `app.py` running — either via the Flask development server or Waitress:
  ```powershell
  # Development (localhost only)
  python app.py

  # Production / LAN / Ngrok (recommended when testing from a phone)
  python -m waitress --host=0.0.0.0 --port=5000 app:app
  ```
- `python detector.py` running in a separate terminal
- Browser open at `http://localhost:5000`
- Phone (or second browser) ready for `/mobile` — see README for how to find your local IP when using Waitress
- Telegram bot configured if you want to test Telegram features
- At least one **enrolled resident** and one **non-enrolled person** available

Notes:
- `app.py` will refuse to start if your `.env` still contains placeholder defaults (like `SMARTGUARD_ADMIN_PASSWORD=change_me` or `SMARTGUARD_API_KEY=default_secure_key`).

---

## Phase 1 – Admin & Resident Management

### T01 – Ghost Video (failed enrollment)
- **Why**: Ensure the system never creates an empty/invalid face profile.
- **How**:
  1. Log into the dashboard.
  2. In **Enrol / Update Resident**, enter any name.
  3. Upload a short video of a **blank wall / empty room** (no faces).
  4. Submit the form.
- **Expected**:
  - Dashboard shows an error like "No clear face detected".
  - No new resident appears in the Authorized Database table.
  - No new resident folder is created under `database/`.

### T02 – Valid enrollment
- **Why**: Verify correct face profiles can be created.
- **How**:
  1. On the dashboard, enter a resident name (e.g., `Alice`).
  2. Upload a **5–10 second** video of the person's face, slowly panning (up/down/left/right).
  3. Submit.
- **Expected**:
  - Success message appears.
  - `Alice` appears in the Authorized Database table.
  - A folder `database/Alice/` exists with `Alice_profile.npy` inside.

### T03 – Revocation
- **Why**: Confirm revoking a resident removes all local biometric data.
- **How**:
  1. From the dashboard's Authorized Database table, click **Revoke** on an existing resident.
  2. Confirm the browser prompt.
- **Expected**:
  - The resident row disappears from the table.
  - Their folder under `database/` is deleted.
  - An appropriate log entry is added in the activity log.

---

## Phase 2 – Geofencing & Mobile Remote

> For these tests, make sure both `app.py` and `detector.py` are running.

### T04 – Simulated "Away"
- **Why**: Check that the mobile page can force the system into **Away** mode.
- **How**:
  1. Log in to the dashboard.
  2. From the same browser session, open `/mobile` on your phone (or second tab).
  3. Tap **Simulate "Away"**.
  4. Watch the dashboard and detector overlay.
- **Expected**:
  - Mobile shows a message like "Simulation Synced".
  - Dashboard status badge changes to **ACTIVE (AWAY)** (red).
  - Detector overlay changes from `HOME (Passive Mode)` to an observing/scanning state.

### T05 – Manual GPS sync (Home)
- **Why**: Verify that a real GPS-like update sets the system to **Home / Passive**.
- **How**:
  1. Set realistic home coordinates in `.env` (`SMARTGUARD_HOME_LAT` / `SMARTGUARD_HOME_LON`).
  2. Physically near that location, open `/mobile`.
  3. Tap **Send Exact Location**.
- **Expected**:
  - Dashboard badge shows **PASSIVE (HOME)** (green).
  - Detector overlay shows `HOME (Passive Mode)` and alarms/timers stop.

### T06 – Live Radar auto-track
- **Why**: Confirm continuous geofence updates from the mobile page.
- **How**:
  1. Open `/mobile` while logged in.
  2. Toggle **Live Radar** on.
  3. Walk around a bit (or move with the device).
  4. Watch the dashboard status area.
- **Expected**:
  - Browser asks for GPS permission (first time).
  - Radar icon pulses on `/mobile`.
  - Dashboard "Closest Device" distance updates every few seconds.

---

## Phase 3 – Vision & Recognition Logic

> For all tests in this phase, ensure the system is in **ACTIVE (AWAY)** mode using the mobile remote.

### T07 – Authorized entry
- **Why**: Verify that an enrolled resident is recognized and does **not** start alarms.
- **How**:
  1. Put the system into **Away** mode (T04).
  2. Have an **enrolled** resident walk into the camera frame.
- **Expected**:
  - Detector overlay shows `AUTHORIZED: <Resident Name>` in green.
  - No observation timer or alarm sequence starts.

### T08 – Area clear reset
- **Why**: Ensure the system correctly clears its state when nobody is present.
- **How**:
  1. After any presence, have everyone step completely out of the frame.
  2. Wait more than **5 seconds**.
- **Expected**:
  - Overlay text becomes `Scanning (Area Clear)...`.
  - Any lingering authorized identity is cleared internally.

### T09 – Intruder lock-on
- **Why**: Check that unknown people start the observation timer.
- **How**:
  1. With the system in **Away** mode, have a **non-enrolled** person step into the frame.
  2. Keep them in view.
- **Expected**:
  - Overlay text turns red and shows `OBSERVING (Xs)` where X increases over time.

---

## Phase 4 – Escalation Timers & Recording

> Keep the system in **Away** mode and use a non-enrolled person.

### T10 – 15-second Telegram prompt (Tier 1)
- **Why**: Verify first-stage escalation and snapshot.
- **How**:
  1. Keep the unknown person in the frame continuously.
  2. Watch the elapsed time reach at least **15 seconds**.
- **Expected**:
  - A snapshot `.jpg` is saved under `static/`.
  - If Telegram is configured, your bot sends a photo captioned as an unknown person with inline buttons (authorize guest / sound alarm).

### T11 – 30-second escalation (Tier 2)
- **Why**: Verify full alarm trigger when there is no response.
- **How**:
  1. After T10, **do not** respond in Telegram.
  2. Keep the unknown person in frame until **30 seconds total** have passed.
- **Expected**:
  - Local siren starts (looping `alarm.wav` if present, or system beeps).
  - Detector overlay shows `ALARM ACTIVE / RECORDING` and a red `REC` indicator.
  - If Telegram is configured, a major alert message is sent.

### T12 – Video compilation
- **Why**: Confirm breach clips include pre-roll and finalize correctly.
- **How**:
  1. Allow Tier 2 alarm to trigger (T11).
  2. Let the alarm run until the configured recording window finishes (default ~15 seconds after trigger).
  3. Let the intruder leave the frame.
- **Expected**:
  - A `.mp4` breach clip appears in `static/`, including a few seconds of pre-roll (~5s).
  - If Telegram is configured, the clip is delivered to your chat.

### T13 – Persistent reset
- **Why**: Ensure persistent breach state does not permanently "spam" alerts.
- **How**:
  1. Trigger an alarm (Tier 2).
  2. Allow the area to fully clear.
  3. Wait at least the configured persistent reset time (default about **60 seconds**).
  4. Have an unknown person appear again.
- **Expected**:
  - System returns to normal observing state after the reset period.
  - New intruder starts **fresh** 15s and 30s timers (not instantly re-triggering).

---

## Phase 5 – Telegram Callbacks

> Requires `SMARTGUARD_TELEGRAM_TOKEN` and `SMARTGUARD_TELEGRAM_CHAT_ID` set, and the bot running.

### T14 – Remote guest authorization
- **Why**: Confirm that remote "known guest" approval interrupts escalation.
- **How**:
  1. Trigger the 15s prompt (T10) with an unknown person.
  2. In Telegram, tap the **"Known Guest"** / authorize-guest button.
- **Expected**:
  - Detector overlay shows `AUTHORIZED: Guest`.
  - Observation and alarm timers stop.
  - An appropriate log entry is added.

### T15 – Panic button (force escalation)
- **Why**: Verify that an operator can immediately force recording and alarm.
- **How**:
  1. With an unknown person in view, wait for the 15s prompt.
  2. In Telegram, tap the **force alarm** / panic button.
- **Expected**:
  - Alarm and recording start immediately without waiting for the 30s timer.
  - Overlay shows `ALARM ACTIVE / RECORDING`.

### T16 – Remote disarm
- **Why**: Confirm the siren can be silenced remotely via Telegram.
- **How**:
  1. Allow the full Tier 2 alarm to trigger (T11).
  2. In Telegram, tap the **Disarm Siren** button on either the major alert message or the breach video.
- **Expected**:
  - Siren stops.
  - Overlay shows `DISARMED VIA TELEGRAM`.
  - Recording stops if still in progress.

---

## Phase 6 – Security Controls

### T17 – Login protection
- **Why**: Confirm that unauthenticated users cannot access the dashboard.
- **How**:
  1. Open a private/incognito browser window.
  2. Navigate directly to `http://localhost:5000/` or `/logs`.
- **Expected**:
  - Redirected to `/login`.
  - Dashboard content is not visible.

### T18 – Large upload rejection
- **Why**: Ensure the 50 MB upload limit prevents abuse.
- **How**:
  1. Attempt to upload a video file larger than 50 MB via the enroll form.
- **Expected**:
  - App does not crash; no partial resident profile is saved.

### T19 – CSRF forgery
- **Why**: Verify that destructive actions require a valid CSRF token.
- **How**:
  1. Log into the dashboard normally.
  2. From an external tool (Postman/curl) or browser console, attempt to POST to a CSRF-protected route (like `/delete/<id>` or `/clear_all_logs`) **without** sending the form's `csrf_token`.
- **Expected**:
  - Action is rejected (redirect or "Invalid request" message).
  - Database remains unchanged.

---

## Phase 7 – Network & Edge Cases

### T20 – Network hiccup in Home mode
- **Why**: Ensure temporary network loss does not cause false alarms.
- **How**:
  1. Put system into **PASSIVE (HOME)** mode.
  2. Briefly disconnect the PC's network (Wi-Fi off for ~3 seconds), then reconnect.
- **Expected**:
  - Detector continues to see `HOME (Passive Mode)`.
  - No false alarm is triggered purely from a short connection loss.

### T21 – "Ghost recording" cancellation
- **Why**: Ensure recordings stop when the owner returns home mid-alarm.
- **How**:
  1. Trigger a major alarm so that recording starts.
  2. About 5 seconds into the recording, send **Send Exact Location** from `/mobile` to switch system to Home.
- **Expected**:
  - Overlay switches to `HOME (Passive Mode)` immediately.
  - Recording queues are closed and no extra unintended footage is recorded.

### T22 – 7-day storage leak test
- **Why**: Confirm `static/` does not grow without bound.
- **How**:
  1. Trigger a few alarms to create `.mp4` and `.jpg` files in `static/`.
  2. Manually set system clock forward by more than 7 days, or touch the files to be older than 7 days.
  3. Restart `detector.py`.
- **Expected**:
  - `_cleanup_old_media` deletes the old files on startup.
  - New recordings still work normally.

---

## Phase 8 – Database & Error Handling

### T23 – Corrupt database recreation
- **Why**: Ensure the app can recreate its database schema cleanly.
- **How**:
  1. Stop `app.py`.
  2. Delete `smartguard.db` (and optional SQLite side files) from the project folder.
  3. Start `python app.py` again.
- **Expected**:
  - App starts without unhandled exceptions.
  - A new `smartguard.db` is created and the app works normally.

### T24 – Database lock under concurrent operations
- **Why**: Check that SQLite timeouts prevent permanent "database locked" errors.
- **How**:
  1. Open **two** browser tabs, both logged in.
  2. In quick succession, perform multiple delete or log operations from both tabs.
- **Expected**:
  - Operations complete successfully.
  - No persistent "database is locked" error appears in the console.

---

## Phase 9 – Vision Edge Cases

### T25 – Mask / occlusion
- **Why**: See how the system behaves when faces are partially hidden.
- **How**:
  1. Put the system in **Away** mode.
  2. Have an **enrolled** resident enter the frame wearing a mask/sunglasses/hat.
- **Expected**:
  - Either:
    - Resident is correctly identified (depending on visibility), or
    - Treated as unknown and an observation timer starts.
  - The system **must not** mislabel them as a different enrolled person.

### T26 – Low-light reliability
- **Why**: Check behavior when video quality is poor.
- **How**:
  1. Darken the room so faces are hard to see.
  2. Have a person enter the frame.
- **Expected**:
  - If no faces are detected, system continues to scan and does **not** crash.
  - When lighting improves, detection and recognition resume correctly.

### T27 – Mixed group priority
- **Why**: Understand what happens when an owner and intruder appear together.
- **How**:
  1. Put system in **Away** mode.
  2. Have an enrolled resident and an unknown person enter the frame **at the same time**.
- **Expected**:
  - You observe how the current "first encoding only" logic behaves.
  - Use this to explain in your viva that the system currently considers only the first detected face and could be extended in future to process multiple faces per frame.

---

## Phase 10 – Geofence Edge Cases

### T28 – Border walk
- **Why**: Test stability around the configured distance threshold.
- **How**:
  1. Set `SMARTGUARD_HOME_DISTANCE_METERS` to a known radius (e.g., `100`).
  2. With Live Radar on, walk roughly to that distance and back across it.
- **Expected**:
  - Dashboard toggles between **HOME** and **AWAY** at the boundary.
  - Toggles are stable (no rapid flapping or crashes).

### T29 – Zero-radius strict mode
- **Why**: Confirm that radius math is correct to within very small drift.
- **How**:
  1. Set `SMARTGUARD_HOME_DISTANCE_METERS=0` in `.env`.
  2. Restart `app.py` and `detector.py`.
  3. Use `/mobile` to send exact location several times.
- **Expected**:
  - Any GPS drift (even small) tends to classify as **Away**.
  - Demonstrates that Haversine distance is computed accurately.

---

## Phase 11 – Network & Performance Stress (Optional)

These are advanced but still manual-friendly with a bit of scripting.

### T30 – Ngrok reconnect (if you use tunneling)
- **Why**: Check that you can re-point the detector when your tunnel URL changes.
- **How**:
  1. Run ngrok (or similar) to expose your Flask app and get a tunnel URL.
  2. Set `SMARTGUARD_PORT` / `BASE_URL` (via `.env`) appropriately for the detector.
  3. With `detector.py` running, stop and restart ngrok to get a new URL.
  4. Update your `.env` or `SMARTGUARD_HEARTBEAT_URL` / `BASE_URL` and restart only what is necessary.
- **Expected**:
  - Detector can resume talking to the app using the new tunnel.
  - Existing profiles/logs are preserved.

### T31 – High-frequency geofence pings
- **Why**: See how the server behaves under heavier API traffic.
- **How**:
  1. Write a small script or use a tool to send `POST /update_location` requests ~every 100 ms with a valid `X-API-KEY`.
  2. Monitor CPU usage and responsiveness of the dashboard.
- **Expected**:
  - App remains responsive and does not crash.
  - `_cleanup_stale_devices` keeps the in-memory device list under control over time.

---

## Phase 12 – Privacy & Access Control

### T32 – Direct file access blocked
- **Why**: Ensure biometric profile files are not served directly.
- **How**:
  1. In the browser, try URLs like `http://localhost:5000/database/somefile.npy`.
- **Expected**:
  - You get a 404 (or similar), not a file download.
  - Confirms `database/` is not configured as a static folder.

### T33 – SQL injection probe
- **Why**: Confirm parameterized queries neutralize injection attempts.
- **How**:
  1. In **Enroll Resident**, use a name like:
     `test' OR '1'='1`
  2. In **Add Manual Log**, use similar patterns in the event/status fields.
- **Expected**:
  - App treats these as normal text.
  - No crashes, no strange rows showing that SQL logic was executed.

### T34 – Power-cycle recovery
- **Why**: See what happens if power is lost mid-incident.
- **How**:
  1. During an active alarm and recording, forcibly stop both `app.py` and `detector.py` (Ctrl+C or simulate power off).
  2. Restart `python app.py` and `python detector.py`.
- **Expected**:
  - System starts cleanly.
  - Database remains consistent.
  - Partially written media files (if any) do not break subsequent recordings.

---

## Quick Sign-Off Checklist

Use this as a summary before you call the build "ready" for your college submission:

- [ ] App and detector start cleanly with your real `.env`.
- [ ] Resident enrollment, revocation, and activity logs all work as expected.
- [ ] Geofencing moves correctly between **HOME** and **AWAY** and the mobile page works.
- [ ] Authorized residents are recognized and do not trigger alarms.
- [ ] Unknown people trigger timers, snapshots, alarms, and recordings correctly.
- [ ] Telegram alerts and callbacks work if configured.
- [ ] Large uploads are rejected safely.
- [ ] Old media in `static/` is cleaned up on detector startup.
- [ ] API requires `X-API-KEY` to read or update system status.
- [ ] Database can be recreated and handles concurrent use without permanent lock errors.
