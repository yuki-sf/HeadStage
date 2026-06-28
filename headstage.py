from __future__ import annotations

import ctypes
import json
import os
import sys
import time
import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

try:
    from PIL import Image, ImageTk
except Exception:
    Image = None
    ImageTk = None

try:
    import mediapipe as mp
except Exception:
    mp = None

try:
    import psutil
except Exception:
    psutil = None

try:
    import pystray
except Exception:
    pystray = None

APP_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
PROFILE_DIR = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents" / "HeadStage" / "Profiles"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_PATH = PROFILE_DIR / "headstage_profile.json"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class ScreenTarget:
    name: str
    yaw: Optional[float] = None
    assigned_session: Optional[str] = None
    volume: float = 1.0


@dataclass
class TrackingState:
    yaw: Optional[float] = None
    confidence: float = 0.0
    face_found: bool = False
    frame: Optional[np.ndarray] = None
    status: str = "Starting camera"


@dataclass
class MonitorInfo:
    index: int
    rect: Tuple[int, int, int, int]
    name: str


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    process_name: str
    rect: Tuple[int, int, int, int]
    monitor_index: Optional[int]


class WindowsMapper:
    def __init__(self) -> None:
        self.available = hasattr(ctypes, "windll")
        self.user32 = ctypes.windll.user32 if self.available else None
        if self.available:
            try:
                self.user32.SetProcessDPIAware()
            except Exception:
                pass

    def monitors(self) -> List[MonitorInfo]:
        if not self.available:
            return []

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class MONITORINFOEX(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_ulong), ("szDevice", ctypes.c_wchar * 32)]

        monitors: List[MonitorInfo] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_long)

        def callback(hmonitor, _hdc, _rect, _data):
            info = MONITORINFOEX()
            info.cbSize = ctypes.sizeof(MONITORINFOEX)
            self.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info))
            rect = (info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom)
            monitors.append(MonitorInfo(index=len(monitors), rect=rect, name=info.szDevice or f"Monitor {len(monitors) + 1}"))
            return 1

        self.user32.EnumDisplayMonitors(0, 0, callback_type(callback), 0)
        monitors.sort(key=lambda monitor: (monitor.rect[0], monitor.rect[1]))
        for idx, monitor in enumerate(monitors):
            monitor.index = idx
        return monitors

    def visible_windows(self) -> List[WindowInfo]:
        if not self.available:
            return []

        monitors = self.monitors()
        windows: List[WindowInfo] = []
        user32 = self.user32

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_long)

        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value.strip()
            if not title:
                return True
            rect = RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            win_rect = (rect.left, rect.top, rect.right, rect.bottom)
            if win_rect[2] - win_rect[0] < 80 or win_rect[3] - win_rect[1] < 60:
                return True
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process_name = self._process_name(int(pid.value))
            monitor_index = self._best_monitor(win_rect, monitors)
            windows.append(WindowInfo(int(hwnd or 0), title, int(pid.value), process_name, win_rect, monitor_index))
            return True

        user32.EnumWindows(callback_type(callback), 0)
        return windows

    def foreground_window(self) -> Optional[WindowInfo]:
        if not self.available:
            return None
        hwnd = int(self.user32.GetForegroundWindow())
        return next((window for window in self.visible_windows() if window.hwnd == hwnd), None)

    def _process_name(self, pid: int) -> str:
        if psutil is None:
            return ""
        try:
            return psutil.Process(pid).name()
        except Exception:
            return ""

    def _best_monitor(self, rect: Tuple[int, int, int, int], monitors: List[MonitorInfo]) -> Optional[int]:
        best_idx = None
        best_area = 0
        for monitor in monitors:
            area = self._intersection_area(rect, monitor.rect)
            if area > best_area:
                best_area = area
                best_idx = monitor.index
        return best_idx

    @staticmethod
    def _intersection_area(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> int:
        left = max(a[0], b[0])
        top = max(a[1], b[1])
        right = min(a[2], b[2])
        bottom = min(a[3], b[3])
        return max(0, right - left) * max(0, bottom - top)


class HeadTracker:
    def __init__(self, camera_index: int = 0) -> None:
        self.camera_index = camera_index
        self.state = TrackingState()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._smoothed_yaw: Optional[float] = None
        self.smoothing = 0.65
        self._face_mesh = None

        if mp is not None:
            self._face_mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=False,
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=0.55,
                min_tracking_confidence=0.55,
            )

        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._cascade = cv2.CascadeClassifier(cascade_path)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._face_mesh is not None:
            self._face_mesh.close()

    def snapshot(self) -> TrackingState:
        with self._lock:
            return TrackingState(
                yaw=self.state.yaw,
                confidence=self.state.confidence,
                face_found=self.state.face_found,
                frame=None if self.state.frame is None else self.state.frame.copy(),
                status=self.state.status,
            )

    def _open_camera(self) -> Tuple[Optional[cv2.VideoCapture], str]:
        backends = [
            (cv2.CAP_DSHOW, "DirectShow"),
            (cv2.CAP_MSMF, "Media Foundation"),
            (cv2.CAP_ANY, "Default"),
        ]
        indices = [self.camera_index] + [idx for idx in range(4) if idx != self.camera_index]
        for index in indices:
            for backend, backend_name in backends:
                cap = cv2.VideoCapture(index, backend)
                if cap.isOpened():
                    ok, _frame = cap.read()
                    if ok:
                        return cap, f"Camera {index} via {backend_name}"
                cap.release()
        return None, "Camera unavailable. Check Windows camera privacy settings for desktop apps."

    def _run(self) -> None:
        cap, camera_status = self._open_camera()
        if cap is None:
            with self._lock:
                self.state.status = camera_status
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)
        with self._lock:
            self.state.status = camera_status

        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue

            frame = cv2.flip(frame, 1)
            yaw, confidence, annotated = self._estimate_yaw(frame)
            if yaw is not None:
                if self._smoothed_yaw is None:
                    self._smoothed_yaw = yaw
                else:
                    alpha = clamp(self.smoothing, 0.0, 0.95)
                    self._smoothed_yaw = alpha * self._smoothed_yaw + (1.0 - alpha) * yaw
                status = "Tracking"
                face_found = True
            else:
                status = "No face"
                face_found = False

            with self._lock:
                self.state = TrackingState(
                    yaw=self._smoothed_yaw if face_found else None,
                    confidence=confidence,
                    face_found=face_found,
                    frame=annotated,
                    status=status,
                )

            time.sleep(0.015)

        cap.release()

    def _estimate_yaw(self, frame: np.ndarray) -> Tuple[Optional[float], float, np.ndarray]:
        annotated = frame.copy()
        if self._face_mesh is not None:
            yaw, confidence = self._estimate_with_facemesh(frame, annotated)
            if yaw is not None:
                return yaw, confidence, annotated
        return self._estimate_with_haar(frame, annotated)

    def _estimate_with_facemesh(self, frame: np.ndarray, annotated: np.ndarray) -> Tuple[Optional[float], float]:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = self._face_mesh.process(rgb)
        if not result.multi_face_landmarks:
            return None, 0.0

        h, w = frame.shape[:2]
        landmarks = result.multi_face_landmarks[0].landmark
        nose = landmarks[1]
        left_cheek = landmarks[234]
        right_cheek = landmarks[454]
        face_center_x = (left_cheek.x + right_cheek.x) / 2.0
        cheek_span = max(0.05, abs(right_cheek.x - left_cheek.x))
        nose_offset = (nose.x - face_center_x) / cheek_span
        frame_offset = face_center_x - 0.5
        yaw = (nose_offset * 0.75 + frame_offset * 1.3) * 100.0
        confidence = clamp(cheek_span * 4.0, 0.2, 1.0)

        nx, ny = int(nose.x * w), int(nose.y * h)
        cx, cy = int(face_center_x * w), int(((left_cheek.y + right_cheek.y) / 2.0) * h)
        cv2.circle(annotated, (nx, ny), 5, (0, 255, 255), -1)
        cv2.line(annotated, (cx, cy), (nx, ny), (0, 255, 255), 2)
        cv2.putText(annotated, f"yaw {yaw:+.1f}", (18, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        return yaw, confidence

    def _estimate_with_haar(self, frame: np.ndarray, annotated: np.ndarray) -> Tuple[Optional[float], float, np.ndarray]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(80, 80))
        if len(faces) == 0:
            return None, 0.0, annotated

        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        frame_w = frame.shape[1]
        center_x = x + w / 2.0
        yaw = ((center_x / frame_w) - 0.5) * 100.0
        confidence = clamp((w * h) / float(frame.shape[0] * frame.shape[1]) * 8.0, 0.15, 1.0)
        cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 210, 120), 2)
        cv2.putText(annotated, f"yaw {yaw:+.1f}", (18, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 210, 120), 2)
        return yaw, confidence, annotated


@dataclass
class AudioSessionInfo:
    key: str
    label: str
    process_name: str
    pid: int
    session: object
    simple_volume: object
    channel_volume: Optional[object] = None
    original_master: Optional[float] = None
    original_channels: Optional[List[float]] = None


class AudioController:
    def __init__(self) -> None:
        self.available = False
        self.error = ""
        self.sessions: Dict[str, AudioSessionInfo] = {}
        self._originals: Dict[str, Tuple[Optional[float], Optional[List[float]]]] = {}
        try:
            from pycaw.pycaw import AudioUtilities
            self.AudioUtilities = AudioUtilities
            self.available = True
        except Exception as exc:
            self.AudioUtilities = None
            self.error = str(exc)

    def refresh(self) -> List[AudioSessionInfo]:
        if not self.available:
            return []

        refreshed: Dict[str, AudioSessionInfo] = {}
        for session in self.AudioUtilities.GetAllSessions():
            process = getattr(session, "Process", None)
            simple_volume = getattr(session, "SimpleAudioVolume", None)
            if process is None or simple_volume is None:
                continue

            process_name = getattr(process, "name", lambda: "Unknown")()
            pid = getattr(process, "pid", 0)
            display_name = getattr(session, "DisplayName", "") or ""
            label = f"{process_name} [{pid}]"
            if display_name:
                label = f"{label} - {display_name}"
            key = f"{process_name}:{pid}:{display_name}"
            channel_volume = self._get_channel_volume(session)
            info = AudioSessionInfo(key, label, process_name, pid, session, simple_volume, channel_volume)
            self._capture_original(info)
            refreshed[key] = info

        self.sessions = refreshed
        return list(refreshed.values())

    def _get_channel_volume(self, session: object) -> Optional[object]:
        for module_name in ("pycaw.pycaw", "pycaw.api.audioclient"):
            try:
                module = __import__(module_name, fromlist=["IChannelAudioVolume"])
                interface = getattr(module, "IChannelAudioVolume")
                return session._ctl.QueryInterface(interface)
            except Exception:
                continue
        return None

    def _capture_original(self, info: AudioSessionInfo) -> None:
        if info.key in self._originals:
            info.original_master, info.original_channels = self._originals[info.key]
            return

        master = None
        channels = None
        try:
            master = float(info.simple_volume.GetMasterVolume())
        except Exception:
            pass

        if info.channel_volume is not None:
            try:
                count = int(info.channel_volume.GetChannelCount())
                channels = [float(info.channel_volume.GetChannelVolume(i)) for i in range(count)]
            except Exception:
                channels = None

        info.original_master = master
        info.original_channels = channels
        self._originals[info.key] = (master, channels)

    def apply_pan(self, session_key: str, pan: float, master_scale: float, crossfeed: float, dry_run: bool) -> str:
        info = self.sessions.get(session_key)
        if info is None:
            return "missing"

        pan = clamp(pan, -1.0, 1.0)
        master_scale = clamp(master_scale, 0.0, 1.0)
        crossfeed = clamp(crossfeed, 0.0, 0.65)
        left = master_scale * (1.0 if pan <= 0 else max(crossfeed, 1.0 - pan))
        right = master_scale * (1.0 if pan >= 0 else max(crossfeed, 1.0 + pan))

        if dry_run:
            return f"dry L {left:.2f} R {right:.2f}"

        if info.channel_volume is not None:
            try:
                count = int(info.channel_volume.GetChannelCount())
                if count >= 2:
                    info.channel_volume.SetChannelVolume(0, float(clamp(left, 0.0, 1.0)), None)
                    info.channel_volume.SetChannelVolume(1, float(clamp(right, 0.0, 1.0)), None)
                    return f"L {left:.2f} R {right:.2f}"
            except Exception:
                pass

        try:
            fallback = max(left, right)
            info.simple_volume.SetMasterVolume(float(clamp(fallback, 0.0, 1.0)), None)
            return f"volume {fallback:.2f}"
        except Exception as exc:
            return f"error {exc}"

    def restore(self) -> None:
        for info in list(self.sessions.values()):
            master, channels = self._originals.get(info.key, (None, None))
            if channels is not None and info.channel_volume is not None:
                try:
                    for idx, value in enumerate(channels):
                        info.channel_volume.SetChannelVolume(idx, float(value), None)
                except Exception:
                    pass
            if master is not None:
                try:
                    info.simple_volume.SetMasterVolume(float(master), None)
                except Exception:
                    pass


class HeadStageApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("HeadStage")
        self.root.geometry("1480x940")
        self.root.minsize(1480, 940)
        self.root.resizable(False, False)
        self._configure_fonts_and_icon()
        self.tracker = HeadTracker()
        self.audio = AudioController()
        self.window_mapper = WindowsMapper()
        self.screens: List[ScreenTarget] = [
            ScreenTarget("Screen 1 - Left"),
            ScreenTarget("Screen 2 - Center"),
            ScreenTarget("Screen 3 - Right"),
        ]
        self.session_labels: Dict[str, str] = {}
        self.rows: List[Dict[str, object]] = []
        self.window_list: List[WindowInfo] = []
        self.audio_state: Dict[str, Tuple[float, float]] = {}
        self.last_face_time = time.time()
        self._photo = None
        self._logo_photo = None
        self._tray_icon = None
        self._closing = False

        self.audio_enabled = tk.BooleanVar(value=False)
        self.dry_run = tk.BooleanVar(value=True)
        self.isolation_mode = tk.BooleanVar(value=False)
        self.auto_restore = tk.BooleanVar(value=True)
        self.auto_save_profile = tk.BooleanVar(value=True)
        self.smoothing = tk.DoubleVar(value=0.65)
        self.spread = tk.DoubleVar(value=0.86)
        self.crossfeed = tk.DoubleVar(value=0.18)
        self.master_scale = tk.DoubleVar(value=1.0)
        self.focus_boost = tk.DoubleVar(value=2.25)
        self.transition_speed = tk.DoubleVar(value=0.16)
        self.minimize_to_tray = tk.BooleanVar(value=False)
        self.current_yaw = tk.StringVar(value="Yaw: --")
        self.current_focus = tk.StringVar(value="Facing: --")
        self.status_text = tk.StringVar(value="Starting")

        self._build_ui()
        self.load_default_profile()
        self.tracker.start()
        self.refresh_sessions()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Unmap>", self._on_unmap)
        self.root.after(50, self.update_loop)


    def _configure_fonts_and_icon(self) -> None:
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkTooltipFont"):
            try:
                base = tkfont.nametofont(name)
                base.configure(size=max(13, int(base.cget("size")) + 4))
            except Exception:
                pass
        icon_path = RESOURCE_DIR / "assets" / "headstage.ico"
        if icon_path.exists():
            try:
                self.root.iconbitmap(str(icon_path))
            except Exception:
                pass

    def _make_logo_image(self) -> Optional[object]:
        if Image is None or ImageTk is None:
            return None
        image = Image.new("RGBA", (72, 72), (0, 0, 0, 0))
        from PIL import ImageDraw
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((5, 8, 67, 64), radius=16, fill=(16, 24, 39, 255), outline=(59, 130, 246, 255), width=3)
        draw.arc((20, 21, 52, 53), start=205, end=335, fill=(45, 212, 191, 255), width=5)
        draw.ellipse((29, 27, 43, 41), fill=(248, 250, 252, 255))
        draw.line((16, 48, 27, 42), fill=(96, 165, 250, 255), width=4)
        draw.line((56, 48, 45, 42), fill=(96, 165, 250, 255), width=4)
        return ImageTk.PhotoImage(image)

    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        self.root.configure(bg="#f6f8fb")
        style.configure("TFrame", background="#f6f8fb")
        style.configure("TLabelframe", background="#f6f8fb")
        style.configure("TLabelframe.Label", background="#f6f8fb", font=("Segoe UI", 12, "bold"), foreground="#1f2937")
        style.configure("TButton", padding=(14, 10), font=("Segoe UI", 12))
        style.configure("TCheckbutton", background="#f6f8fb", font=("Segoe UI", 12))
        style.configure("TLabel", background="#f6f8fb", font=("Segoe UI", 12), foreground="#111827")
        style.configure("Header.TLabel", font=("Segoe UI", 26, "bold"), foreground="#0f172a")
        style.configure("Small.TLabel", font=("Segoe UI", 11), foreground="#64748b")
        style.configure("Accent.TButton", padding=(14, 10), font=("Segoe UI", 12, "bold"))

        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(outer)
        top.pack(fill=tk.X)
        self._logo_photo = self._make_logo_image()
        if self._logo_photo is not None:
            ttk.Label(top, image=self._logo_photo).pack(side=tk.LEFT, padx=(0, 12))
        title_stack = ttk.Frame(top)
        title_stack.pack(side=tk.LEFT)
        ttk.Label(title_stack, text="HeadStage", style="Header.TLabel").pack(anchor="w")
        ttk.Label(title_stack, textvariable=self.status_text, style="Small.TLabel").pack(anchor="w")
        ttk.Button(top, text="Save profile", command=self.save_profile).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(top, text="Load profile", command=self.load_profile).pack(side=tk.RIGHT)

        body = ttk.PanedWindow(outer, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, pady=(14, 0))
        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=3)
        body.add(right, weight=2)

        preview_frame = ttk.LabelFrame(left, text="Camera")
        preview_frame.pack(fill=tk.BOTH, expand=True)
        self.preview = ttk.Label(preview_frame, anchor=tk.CENTER)
        self.preview.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        telemetry = ttk.Frame(left)
        telemetry.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(telemetry, textvariable=self.current_yaw).pack(side=tk.LEFT)
        ttk.Label(telemetry, textvariable=self.current_focus).pack(side=tk.LEFT, padx=18)

        control = ttk.LabelFrame(right, text="Controls")
        control.pack(fill=tk.X)
        ttk.Checkbutton(control, text="Audio control", variable=self.audio_enabled).grid(row=0, column=0, sticky="w", padx=10, pady=8)
        ttk.Checkbutton(control, text="Dry run", variable=self.dry_run).grid(row=0, column=1, sticky="w", padx=10, pady=8)
        ttk.Checkbutton(control, text="Isolation mode", variable=self.isolation_mode).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 8))
        ttk.Checkbutton(control, text="Restore when face is lost", variable=self.auto_restore).grid(row=1, column=1, sticky="w", padx=10, pady=(0, 8))
        self._slider(control, "Smoothing", self.smoothing, 0.0, 0.95, 2)
        self._slider(control, "Stereo spread", self.spread, 0.2, 1.0, 3)
        self._slider(control, "Crossfeed", self.crossfeed, 0.0, 0.55, 4)
        self._slider(control, "Max volume", self.master_scale, 0.1, 1.0, 5)
        self._slider(control, "Focus boost", self.focus_boost, 1.0, 4.5, 6)
        self._slider(control, "Transition speed", self.transition_speed, 0.05, 0.45, 7)
        ttk.Checkbutton(control, text="Minimize to tray", variable=self.minimize_to_tray).grid(row=8, column=0, sticky="w", padx=10, pady=(4, 8))
        ttk.Checkbutton(control, text="Auto-save profile", variable=self.auto_save_profile).grid(row=8, column=1, sticky="w", padx=10, pady=(4, 8))

        session_bar = ttk.Frame(right)
        session_bar.pack(fill=tk.X, pady=(12, 6))
        ttk.Label(session_bar, text="Screens and audio", style="Header.TLabel").pack(side=tk.LEFT)
        ttk.Button(session_bar, text="Refresh sessions", command=self.refresh_sessions).pack(side=tk.RIGHT)
        ttk.Button(session_bar, text="Auto map", command=self.auto_map_audio).pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Button(session_bar, text="Windows", command=self.show_window_mapper).pack(side=tk.RIGHT, padx=(0, 8))

        self.table = ttk.Frame(right)
        self.table.pack(fill=tk.BOTH, expand=True)
        self.table.columnconfigure(2, weight=1)
        self.table.columnconfigure(3, weight=1)
        for col, text in enumerate(("Screen", "Yaw", "Session", "Volume", "Calibrate", "Map", "Output")):
            ttk.Label(self.table, text=text, style="Small.TLabel").grid(row=0, column=col, sticky="w", padx=4, pady=4)
        self.rebuild_screen_rows()

        bottom = ttk.Frame(right)
        bottom.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(bottom, text="Add screen", command=self.add_screen).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Remove last", command=self.remove_screen).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(bottom, text="Restore audio now", command=self.audio.restore).pack(side=tk.RIGHT)
        ttk.Button(bottom, text="Hide to tray", command=self.hide_to_tray).pack(side=tk.RIGHT, padx=(0, 8))

        if not self.audio.available:
            self.status_text.set(f"Audio API unavailable: {self.audio.error}")

    def _slider(self, parent: ttk.Frame, label: str, variable: tk.DoubleVar, low: float, high: float, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=5)
        ttk.Scale(parent, variable=variable, from_=low, to=high, orient=tk.HORIZONTAL).grid(row=row, column=1, sticky="ew", padx=10, pady=5)
        parent.columnconfigure(1, weight=1)

    def rebuild_screen_rows(self) -> None:
        for child in self.table.winfo_children():
            grid = child.grid_info()
            if int(grid.get("row", 0)) > 0:
                child.destroy()
        self.rows.clear()

        for idx, screen in enumerate(self.screens, start=1):
            name = ttk.Entry(self.table, width=18)
            name.insert(0, screen.name)
            name.grid(row=idx, column=0, sticky="ew", padx=4, pady=4)
            name.bind("<FocusOut>", lambda _event, i=idx - 1, widget=name: self.rename_screen(i, widget.get()))
            yaw_var = tk.StringVar(value="--" if screen.yaw is None else f"{screen.yaw:+.1f}")
            ttk.Label(self.table, textvariable=yaw_var, width=8).grid(row=idx, column=1, sticky="w", padx=4, pady=4)
            session = ttk.Combobox(self.table, state="readonly", width=28)
            session.grid(row=idx, column=2, sticky="ew", padx=4, pady=4)
            volume_var = tk.DoubleVar(value=clamp(screen.volume, 0.0, 2.0))
            volume = ttk.Scale(self.table, variable=volume_var, from_=0.0, to=2.0, orient=tk.HORIZONTAL, command=lambda _value, i=idx - 1, var=volume_var: self.set_screen_volume(i, var.get()))
            volume.grid(row=idx, column=3, sticky="ew", padx=4, pady=4)
            ttk.Button(self.table, text="Record", command=lambda i=idx - 1: self.record_screen(i)).grid(row=idx, column=4, sticky="ew", padx=4, pady=4)
            ttk.Button(self.table, text="Focus in 3s", command=lambda i=idx - 1: self.capture_focused_after_delay(i)).grid(row=idx, column=5, sticky="ew", padx=4, pady=4)
            output_var = tk.StringVar(value="--")
            ttk.Label(self.table, textvariable=output_var, width=22).grid(row=idx, column=6, sticky="w", padx=4, pady=4)
            session.bind("<<ComboboxSelected>>", lambda _event, i=idx - 1, widget=session: self.assign_session(i, widget.get()))
            self.rows.append({"yaw": yaw_var, "session": session, "volume": volume_var, "output": output_var})
        self.update_session_dropdowns()

    def refresh_sessions(self) -> None:
        sessions = self.audio.refresh()
        self.session_labels = {info.label: info.key for info in sessions}
        self.update_session_dropdowns()
        if self.audio.available:
            self.status_text.set(f"{len(sessions)} audio sessions found")

    def update_session_dropdowns(self) -> None:
        labels = [""] + sorted(self.session_labels.keys(), key=str.lower)
        for idx, row in enumerate(self.rows):
            combo: ttk.Combobox = row["session"]
            combo["values"] = labels
            assigned_key = self.screens[idx].assigned_session if idx < len(self.screens) else None
            selected = ""
            for label, key in self.session_labels.items():
                if key == assigned_key:
                    selected = label
                    break
            combo.set(selected)

    def add_screen(self) -> None:
        self.screens.append(ScreenTarget(f"Screen {len(self.screens) + 1}"))
        self.rebuild_screen_rows()

    def remove_screen(self) -> None:
        if len(self.screens) > 1:
            self.screens.pop()
            self.rebuild_screen_rows()

    def rename_screen(self, idx: int, name: str) -> None:
        if 0 <= idx < len(self.screens):
            self.screens[idx].name = name.strip() or f"Screen {idx + 1}"

    def set_screen_volume(self, idx: int, volume: float) -> None:
        if 0 <= idx < len(self.screens):
            self.screens[idx].volume = clamp(float(volume), 0.0, 2.0)

    def record_screen(self, idx: int) -> None:
        state = self.tracker.snapshot()
        if state.yaw is None:
            messagebox.showwarning("No face", "Look at the screen until tracking is active, then record again.")
            return
        self.screens[idx].yaw = float(state.yaw)
        self.rows[idx]["yaw"].set(f"{state.yaw:+.1f}")
        self.status_text.set(f"Recorded {self.screens[idx].name}")

    def assign_session(self, idx: int, label: str) -> None:
        if 0 <= idx < len(self.screens):
            self.screens[idx].assigned_session = self.session_labels.get(label)

    def auto_map_audio(self) -> None:
        self.refresh_sessions()
        windows = self.window_mapper.visible_windows()
        monitors = self.window_mapper.monitors()
        if not windows or not monitors:
            messagebox.showwarning("No windows", "I could not read the visible window list from Windows.")
            return

        screen_order = self._screen_order_for_monitors(len(monitors))
        mapped = 0
        skipped = 0
        for session in self.audio.sessions.values():
            window = self._best_window_for_session(session, windows)
            if window is None or window.monitor_index is None or window.monitor_index >= len(screen_order):
                skipped += 1
                continue
            screen_idx = screen_order[window.monitor_index]
            self.screens[screen_idx].assigned_session = session.key
            mapped += 1

        self.update_session_dropdowns()
        self.status_text.set(f"Auto mapped {mapped} sessions, skipped {skipped}")

    def capture_focused_after_delay(self, screen_idx: int) -> None:
        if not (0 <= screen_idx < len(self.screens)):
            return
        self.status_text.set(f"Click the app playing on {self.screens[screen_idx].name}; capturing in 3 seconds")
        self.root.after(3000, lambda: self.capture_focused_window(screen_idx))

    def capture_focused_window(self, screen_idx: int) -> None:
        self.refresh_sessions()
        window = self.window_mapper.foreground_window()
        if window is None:
            messagebox.showwarning("No window", "I could not detect the focused app window.")
            return

        matches = self._session_matches_for_window(window)
        if not matches:
            messagebox.showwarning(
                "No audio session",
                f"I found the focused window '{window.title}', but Windows does not currently show an audio session for it. Start playback, then try again.",
            )
            return

        if len(matches) == 1:
            self._assign_session_key(screen_idx, matches[0].key)
            self.status_text.set(f"Mapped {self.screens[screen_idx].name} to {matches[0].label}")
            return

        self._choose_session_for_screen(screen_idx, window, matches)

    def show_window_mapper(self) -> None:
        self.refresh_sessions()
        windows = self.window_mapper.visible_windows()
        monitors = self.window_mapper.monitors()
        popup = tk.Toplevel(self.root)
        popup.title("Window to audio mapping")
        popup.geometry("900x420")
        frame = ttk.Frame(popup, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text=f"{len(windows)} windows, {len(monitors)} monitors", style="Small.TLabel").pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Auto map now", command=lambda: [self.auto_map_audio(), popup.destroy()]).pack(side=tk.RIGHT)

        tree = ttk.Treeview(frame, columns=("process", "monitor", "session"), show="tree headings", height=14)
        tree.heading("#0", text="Window")
        tree.heading("process", text="Process")
        tree.heading("monitor", text="Monitor")
        tree.heading("session", text="Likely audio session")
        tree.column("#0", width=360)
        tree.column("process", width=150)
        tree.column("monitor", width=100)
        tree.column("session", width=260)
        tree.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        for window in windows:
            match = self._best_session_for_window(window)
            monitor_name = "--"
            if window.monitor_index is not None and window.monitor_index < len(monitors):
                monitor_name = f"{window.monitor_index + 1} ({monitors[window.monitor_index].name})"
            session_label = match.label if match else "--"
            tree.insert("", tk.END, text=window.title[:80], values=(f"{window.process_name} [{window.pid}]", monitor_name, session_label))

    def _choose_session_for_screen(self, screen_idx: int, window: WindowInfo, matches: List[AudioSessionInfo]) -> None:
        popup = tk.Toplevel(self.root)
        popup.title("Choose audio session")
        popup.geometry("560x170")
        frame = ttk.Frame(popup, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text=f"Focused window: {window.title[:80]}").pack(anchor="w")
        ttk.Label(frame, text="Multiple possible audio sessions were found. Pick the one that is playing sound.", style="Small.TLabel").pack(anchor="w", pady=(4, 10))
        labels = [match.label for match in matches]
        combo = ttk.Combobox(frame, values=labels, state="readonly")
        combo.pack(fill=tk.X)
        combo.set(labels[0])

        def assign() -> None:
            label = combo.get()
            selected = next((match for match in matches if match.label == label), matches[0])
            self._assign_session_key(screen_idx, selected.key)
            self.status_text.set(f"Mapped {self.screens[screen_idx].name} to {selected.label}")
            popup.destroy()

        ttk.Button(frame, text="Assign", command=assign).pack(anchor="e", pady=(12, 0))

    def _assign_session_key(self, screen_idx: int, session_key: str) -> None:
        self.screens[screen_idx].assigned_session = session_key
        self.update_session_dropdowns()

    def _screen_order_for_monitors(self, monitor_count: int) -> List[int]:
        order = list(range(len(self.screens)))
        while len(order) < monitor_count:
            order.append(order[-1] if order else 0)
        return order[:monitor_count]

    def _best_window_for_session(self, session: AudioSessionInfo, windows: List[WindowInfo]) -> Optional[WindowInfo]:
        exact = [window for window in windows if window.pid == session.pid]
        if exact:
            return self._largest_window(exact)

        related = [window for window in windows if self._processes_related(session.pid, window.pid)]
        if related:
            return self._largest_window(related)

        same_name = [window for window in windows if window.process_name.lower() == session.process_name.lower() and window.process_name]
        if len(same_name) == 1:
            return same_name[0]
        return None

    def _best_session_for_window(self, window: WindowInfo) -> Optional[AudioSessionInfo]:
        matches = self._session_matches_for_window(window)
        return matches[0] if matches else None

    def _session_matches_for_window(self, window: WindowInfo) -> List[AudioSessionInfo]:
        exact = [session for session in self.audio.sessions.values() if session.pid == window.pid]
        if exact:
            return exact

        related = [session for session in self.audio.sessions.values() if self._processes_related(session.pid, window.pid)]
        if related:
            return related

        same_name = [session for session in self.audio.sessions.values() if session.process_name.lower() == window.process_name.lower() and window.process_name]
        return same_name

    def _processes_related(self, first_pid: int, second_pid: int) -> bool:
        if psutil is None or first_pid == 0 or second_pid == 0:
            return False
        if first_pid == second_pid:
            return True
        return second_pid in self._ancestor_pids(first_pid) or first_pid in self._ancestor_pids(second_pid)

    def _ancestor_pids(self, pid: int) -> List[int]:
        ancestors: List[int] = []
        if psutil is None:
            return ancestors
        try:
            proc = psutil.Process(pid)
            for parent in proc.parents()[:6]:
                ancestors.append(parent.pid)
        except Exception:
            pass
        return ancestors

    def _largest_window(self, windows: List[WindowInfo]) -> WindowInfo:
        return max(windows, key=lambda window: max(0, window.rect[2] - window.rect[0]) * max(0, window.rect[3] - window.rect[1]))

    def update_loop(self) -> None:
        state = self.tracker.snapshot()
        self.tracker.smoothing = self.smoothing.get()
        self._update_preview(state)
        self._update_audio(state)
        self.root.after(50, self.update_loop)

    def _update_preview(self, state: TrackingState) -> None:
        if state.yaw is not None:
            self.current_yaw.set(f"Yaw: {state.yaw:+.1f}   Confidence: {state.confidence:.0%}")
        else:
            self.current_yaw.set("Yaw: --   Confidence: --")
        if state.frame is None or Image is None or ImageTk is None:
            return
        frame = cv2.cvtColor(state.frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(frame)
        max_w = max(360, self.preview.winfo_width() - 16)
        max_h = max(240, self.preview.winfo_height() - 16)
        image.thumbnail((max_w, max_h))
        self._photo = ImageTk.PhotoImage(image)
        self.preview.configure(image=self._photo)

    def _update_audio(self, state: TrackingState) -> None:
        calibrated = [screen for screen in self.screens if screen.yaw is not None]
        if state.face_found and state.yaw is not None:
            self.last_face_time = time.time()
        elif self.auto_restore.get() and time.time() - self.last_face_time > 1.3:
            if self.audio_enabled.get() and not self.dry_run.get():
                self.audio.restore()
            self.current_focus.set("Facing: --")
            return

        if state.yaw is None or not calibrated:
            return

        focus = min(calibrated, key=lambda screen: abs(screen.yaw - state.yaw))
        self.current_focus.set(f"Facing: {focus.name}")
        yaws = sorted(screen.yaw for screen in calibrated if screen.yaw is not None)
        step = max(1.0, float(np.median(np.diff(yaws)))) if len(yaws) >= 2 else 30.0
        ease = clamp(self.transition_speed.get(), 0.03, 0.7)
        boost = clamp(self.focus_boost.get(), 1.0, 5.0)
        base_scale = clamp(self.master_scale.get(), 0.05, 1.0)
        background_scale = 0.0 if self.isolation_mode.get() else base_scale / (boost ** 1.15)

        active_keys = set()
        for idx, screen in enumerate(self.screens):
            output = "--"
            if screen.yaw is not None:
                target_pan = clamp(((screen.yaw - state.yaw) / step) * self.spread.get(), -1.0, 1.0)
                if abs(target_pan) < 0.08:
                    target_pan = 0.0
                screen_trim = clamp(screen.volume, 0.0, 2.0)
                target_scale = base_scale * screen_trim if screen is focus else background_scale * screen_trim
                target_scale = clamp(target_scale, 0.0, 1.0)
                state_key = screen.assigned_session or f"screen:{idx}"
                old_pan, old_scale = self.audio_state.get(state_key, (target_pan, target_scale))
                smooth_pan = old_pan + (target_pan - old_pan) * ease
                smooth_scale = old_scale + (target_scale - old_scale) * ease
                self.audio_state[state_key] = (smooth_pan, smooth_scale)
                active_keys.add(state_key)
                focus_tag = " focus" if screen is focus else ""
                isolation_tag = " iso" if self.isolation_mode.get() and screen is not focus else ""
                output = f"pan {smooth_pan:+.2f} vol {smooth_scale:.2f} trim {screen.volume:.2f}{focus_tag}{isolation_tag}"
                if self.audio_enabled.get() and screen.assigned_session:
                    output = self.audio.apply_pan(
                        screen.assigned_session,
                        pan=smooth_pan,
                        master_scale=smooth_scale,
                        crossfeed=self.crossfeed.get(),
                        dry_run=self.dry_run.get(),
                    ) + focus_tag + isolation_tag
            if idx < len(self.rows):
                self.rows[idx]["output"].set(output)

        for key in list(self.audio_state.keys()):
            if key not in active_keys:
                self.audio_state.pop(key, None)

    def profile_data(self) -> Dict[str, object]:
        return {
            "screens": [screen.__dict__ for screen in self.screens],
            "smoothing": self.smoothing.get(),
            "spread": self.spread.get(),
            "crossfeed": self.crossfeed.get(),
            "master_scale": self.master_scale.get(),
            "focus_boost": self.focus_boost.get(),
            "transition_speed": self.transition_speed.get(),
            "audio_enabled": self.audio_enabled.get(),
            "dry_run": self.dry_run.get(),
            "isolation_mode": self.isolation_mode.get(),
            "auto_restore": self.auto_restore.get(),
            "auto_save_profile": self.auto_save_profile.get(),
            "minimize_to_tray": self.minimize_to_tray.get(),
        }

    def apply_profile_data(self, data: Dict[str, object]) -> None:
        self.screens = [ScreenTarget(**item) for item in data.get("screens", [])] or self.screens
        self.smoothing.set(float(data.get("smoothing", self.smoothing.get())))
        self.spread.set(float(data.get("spread", self.spread.get())))
        self.crossfeed.set(float(data.get("crossfeed", self.crossfeed.get())))
        self.master_scale.set(float(data.get("master_scale", self.master_scale.get())))
        self.focus_boost.set(float(data.get("focus_boost", self.focus_boost.get())))
        self.transition_speed.set(float(data.get("transition_speed", self.transition_speed.get())))
        self.audio_enabled.set(bool(data.get("audio_enabled", self.audio_enabled.get())))
        self.dry_run.set(bool(data.get("dry_run", self.dry_run.get())))
        self.isolation_mode.set(bool(data.get("isolation_mode", self.isolation_mode.get())))
        self.auto_restore.set(bool(data.get("auto_restore", self.auto_restore.get())))
        self.auto_save_profile.set(bool(data.get("auto_save_profile", self.auto_save_profile.get())))
        self.minimize_to_tray.set(bool(data.get("minimize_to_tray", self.minimize_to_tray.get())))
        self.rebuild_screen_rows()

    def save_profile_file(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.profile_data(), indent=2), encoding="utf-8")

    def load_profile_file(self, path: Path) -> None:
        self.apply_profile_data(json.loads(path.read_text(encoding="utf-8")))

    def load_default_profile(self) -> None:
        if PROFILE_PATH.exists():
            try:
                self.load_profile_file(PROFILE_PATH)
                self.status_text.set("Default profile loaded")
            except Exception as exc:
                self.status_text.set(f"Profile load failed: {exc}")

    def save_profile(self) -> None:
        path = filedialog.asksaveasfilename(initialdir=str(PROFILE_DIR), initialfile=PROFILE_PATH.name, defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path:
            self.save_profile_file(Path(path))
            self.status_text.set("Profile saved")

    def load_profile(self) -> None:
        path = filedialog.askopenfilename(initialdir=str(PROFILE_DIR), filetypes=[("JSON", "*.json")])
        if not path:
            return
        self.load_profile_file(Path(path))
        self.status_text.set("Profile loaded")

    def _tray_image(self) -> Optional[object]:
        if Image is None:
            return None
        icon_path = RESOURCE_DIR / "assets" / "headstage.ico"
        try:
            if icon_path.exists():
                return Image.open(icon_path)
        except Exception:
            pass
        return Image.new("RGBA", (64, 64), (15, 23, 42, 255))

    def hide_to_tray(self) -> None:
        if pystray is None or Image is None:
            self.status_text.set("Tray support needs pystray installed")
            self.root.iconify()
            return
        if self._tray_icon is None:
            image = self._tray_image()
            menu = pystray.Menu(
                pystray.MenuItem("Show HeadStage", lambda _icon, _item: self.root.after(0, self.show_from_tray)),
                pystray.MenuItem("Exit", lambda _icon, _item: self.root.after(0, self.on_close)),
            )
            self._tray_icon = pystray.Icon("HeadStage", image, "HeadStage", menu)
            self._tray_icon.run_detached()
        self.root.withdraw()

    def show_from_tray(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_unmap(self, _event: object) -> None:
        if self._closing:
            return
        if self.minimize_to_tray.get() and self.root.state() == "iconic":
            self.root.after(120, self.hide_to_tray)

    def on_close(self) -> None:
        self._closing = True
        if self.auto_save_profile.get():
            try:
                self.save_profile_file(PROFILE_PATH)
            except Exception:
                pass
        if self.audio_enabled.get():
            self.audio.restore()
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.tracker.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    HeadStageApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
