# HeadStage 

> Turn your headphones into a virtual soundstage for your monitor setup.

HeadStage is a Windows desktop application that uses webcam-based head
tracking to determine which monitor you're looking at and intelligently
shifts application audio toward that screen.

Watching a YouTube video on the left monitor? The audio gently moves
left.

Joining a Discord call on the right monitor? Voices drift toward the
right ear.

Instead of every application competing for the center of your
headphones, HeadStage creates the feeling that sound comes from the
screen you're actually using.

<p align="center">
  <a href="https://github.com/yuki-sf/HeadStage/releases/latest">
    <img src="https://img.shields.io/badge/Download-Windows%20EXE-success?style=for-the-badge&logo=windows" />
  </a>
</p>

------------------------------------------------------------------------

## Why HeadStage?

Traditional multi-monitor setups give you visual separation but not
audio separation.

HeadStage bridges that gap by creating a more natural listening
experience:

-   Focus on the content you're actually viewing
-   Reduce cognitive load when multitasking
-   Improve immersion for games and media
-   Keep conversations and videos attached to their screens
-   Make a triple-monitor setup feel surprisingly physical

------------------------------------------------------------------------

## Preview

Below is a preview of the HeadStage interface application actively tracking user head movement and mapping orientation data to a multi-monitor configuration:

<p align="center">
  <img src="https://i.postimg.cc/gJG5kf1K/Head-Stage.png" alt="HeadStage Interface Preview" width="75%">
</p>

------------------------------------------------------------------------

## Features

### Head Tracking

-   Uses your webcam to estimate head position and viewing direction.
-   Supports MediaPipe tracking with OpenCV fallback detection.

### Multi-Monitor Calibration

-   Works with dual-monitor, triple-monitor, ultrawide, and custom
    layouts.
-   Add as many screens as you need and calibrate them in seconds.

### Smart Audio Routing

-   Assign audio sessions from Chrome, Discord, Spotify, games, media
    players, and more.
-   Smoothly shifts stereo positioning based on where you're looking.

### Automatic Session Detection

-   Automatically detects active audio sessions.
-   Automatically maps applications to monitors whenever possible.

### Multiple Mapping Methods

-   **Auto Map** for automatic monitor detection.
-   **Focus in 3s** for quick manual assignment.
-   **Session Dropdown** for full manual control.

### Audio Controls

-   Adjustable transition speed.
-   Configurable stereo spread.
-   Focus boost for the active monitor.
-   Isolation mode for distraction-free listening.
-   Per-screen volume controls.

### Profiles

-   Save monitor layouts and audio mappings.
-   Quickly switch between desk setups.
-   Automatically restore your configuration on startup.

### Safety Features

-   Dry-run mode for testing.
-   Automatic audio restoration if tracking is lost.
-   Automatic recovery when the application closes.

------------------------------------------------------------------------

## Installation

There isn't one.

Simply open the `dist` folder and run `HeadStage.exe` as administrator.

No Python installation.

No PowerShell commands.

No virtual environments.

Just run the executable and you're ready to go.

------------------------------------------------------------------------

## First-Time Setup

1.  Launch **HeadStage.exe**
2.  Start audio playback in the applications you want to control.
3.  Verify that the camera preview can see your face.
4.  Leave **Dry Run** enabled for initial testing.
5.  Look at your left monitor and press **Record**.
6.  Repeat for the center and right monitors.
7.  Press **Auto Map** to detect applications automatically.
8.  If an application is missing, use **Focus in 3s** and click the
    target window.
9.  Disable **Dry Run** and enable **Audio Control**.

Congratulations --- your monitors now have directionally-aware audio.

------------------------------------------------------------------------

## Audio Mapping Methods

### Auto Map

Scans visible windows, determines which monitor they occupy, and
attempts to match them with active audio sessions.

### Focus in 3s

Press the button and switch to the target application. HeadStage
captures the focused window and assigns it to the selected screen.

### Session Dropdown

For browsers and stubborn applications, manually select the audio
session from the list.

------------------------------------------------------------------------

## Profiles

Profiles save:

-   Screen calibration
-   Audio mappings
-   Volume settings
-   Application preferences
-   UI settings

Profiles are stored in:

``` text
Documents\HeadStage\Profiles
```

------------------------------------------------------------------------

## Building From Source

Developers can still run HeadStage directly from source:

``` powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python headstage.py
```

To create a standalone executable:

``` powershell
.\build_exe.ps1
```

------------------------------------------------------------------------

## Camera Troubleshooting

If the camera preview does not appear:

**Windows Settings → Privacy & Security → Camera**

Ensure that desktop applications are allowed to access the camera.

HeadStage automatically attempts multiple OpenCV camera backends and
camera indexes to maximize compatibility.

------------------------------------------------------------------------

## Known Limitations

-   Windows only exposes applications that are actively playing audio.
-   Browser tabs frequently share a single audio session.
-   Some audio drivers do not support direct stereo balance control.

When direct panning is unavailable, HeadStage automatically falls back
to volume shaping instead.

------------------------------------------------------------------------

## The Future

HeadStage is currently an experimental prototype exploring a simple
idea:

> If our screens exist in physical space, maybe our audio should too.

If you've ever pointed at the wrong monitor while talking to someone on
Discord, lost track of which YouTube tab was making noise, or wished
your headphones understood your setup a little better --- HeadStage was
built for you.
