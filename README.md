# HeadStage

HeadStage is a Windows desktop prototype that uses your webcam to estimate which monitor you are facing, then pans assigned app audio sessions toward the matching left/right headphone channel.

## Features

- Calibrate any number of screens by looking at each screen and pressing Record.
- Assign active Windows audio sessions such as Chrome, Edge, Discord, games, or media players to screens.
- Auto-map audio sessions by matching their app windows to the monitor they are on.
- Manually capture the focused app/window for a screen when automatic browser matching is ambiguous.
- Smoothly pan each assigned session based on where that screen is relative to your current head position.
- Smooth volume and pan transitions so audio moves naturally instead of jumping.
- Keep the screen you are facing centered in both headphones.
- Give the screen you are currently looking at a relative focus boost by gently lowering the other screens.
- Dry run mode for testing before the app changes audio.
- Auto-restore when your face is lost and on app close.
- Save and load calibration profiles.

## Install

Use Python 3.10 or newer on Windows.

~~~powershell
cd C:\Users\sambh\Documents\Codex\2026-06-28\i-want-to-create-a-opencv\outputs\headstage
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
~~~

If MediaPipe is unavailable on your Python version, the app still works with OpenCV face detection:

~~~powershell
python -m pip install opencv-python numpy pycaw comtypes pillow psutil
~~~

## Run

~~~powershell
python headstage.py
~~~

Or run:

~~~powershell
.\run_headstage.ps1
~~~

## First Setup

1. Start audio in the apps you want to control. Windows only exposes active audio sessions.
2. Open HeadStage and confirm the webcam preview sees your face.
3. Leave Dry run enabled.
4. Look at the left monitor and press Record in that row.
5. Repeat for center and right.
6. Press Auto map to let HeadStage match app windows to monitors.
7. If a browser or Discord session is missed, press Focus in 3s on the correct screen row, then click the app/window that is playing sound.
8. You can also choose a session directly from the dropdown for fully manual mapping.
9. Disable Dry run, then enable Audio control.

## Important Windows Audio Note

Windows per-app stereo balance depends on the audio driver and Windows session API support. When channel balance is available, HeadStage pans left and right directly. If channel balance is unavailable for a session, it falls back to per-session volume shaping instead of crashing.

## Mapping Audio To Screens

HeadStage now has three mapping methods. Keep the screen rows in physical left-to-right order before pressing Auto map:

- **Auto map**: scans visible windows, detects which physical monitor they are on, and matches those windows to active Windows audio sessions. This works best for apps with one window and one audio process.
- **Focus in 3s**: click this on a screen row, then focus the app/window playing sound. HeadStage captures the focused window and maps its likely audio session to that screen.
- **Session dropdown**: direct manual override for stubborn cases. This is useful for Chrome or Edge when multiple tabs/windows share similar audio processes.

For YouTube in Chrome/Edge, start playback first. Windows often exposes browser audio as a process rather than as a specific tab, so the focused-window method is the most reliable manual fallback.

## Build The EXE

Run this from the HeadStage folder:

~~~powershell
.\build_exe.ps1
~~~

The executable will be created at:

~~~text
dist\HeadStage.exe
~~~

The build script installs PyInstaller into the local virtual environment, includes the app icon, and packages the app as a single windowed executable.

## New Audio Controls

- **Focus boost**: makes the screen you are looking at much louder relative to the others. Because Windows does not normally allow per-app audio above 100%, this works by keeping the focused screen high and lowering the non-focused screens more strongly.
- **Transition speed**: controls how quickly pan and volume move toward the new target. Lower values are silkier; higher values are more responsive.
- **Max volume**: caps the overall output level so the focus boost has room to work without becoming harsh.

## Profiles And EXE Notes

Profiles are saved in your Documents folder at:

~~~text
Documents\HeadStage\Profiles
~~~

This keeps profiles available when running the packaged .exe, even though a one-file Windows app unpacks its internal files into a temporary folder.

If the .exe cannot open the camera, check Windows Settings > Privacy & security > Camera and make sure desktop apps are allowed to use the camera. HeadStage now also tries DirectShow, Media Foundation, and the default OpenCV backend across the first few camera indexes.

The **Minimize to tray** option hides HeadStage from the taskbar when minimized. Use the tray icon menu to show or exit the app.

## Final Audio Controls

- **Per-screen volume**: each screen row has its own volume slider. Use this to tame loud apps or lift quiet apps before focus boost is applied.
- **Isolation mode**: when enabled, only the screen you are facing remains audible. Other assigned screens fade down to mute using the same transition smoothing.
- **Auto-save profile**: saves your calibration, mappings, per-screen volumes, and preferences to the default profile when the app closes.
- **Default profile auto-load**: HeadStage now loads the default profile from Documents\HeadStage\Profiles on startup when it exists.
