#!/usr/bin/env python3
"""
Jakka351's Battery Monitor GUI - Raspberry Pi / Tkinter analogue-video dashboard
https://github.com/jakka351/Battery-Monitor-Protocol

Install:
    python3 -m venv .venv
    source .venv/bin/activate
    pip install bleak pycryptodome

Run against your BLE device:
    python3 monitor.py AA:BB:CC:DD:EE:FF

Test without the BLE device:
    python3 monitor.py --sim

Useful display flags:
    python3 monitor.py AA:BB:CC:DD:EE:FF --size 720x480 --fullscreen
    python3 monitor.py AA:BB:CC:DD:EE:FF --size 720x576 --fullscreen
"""

from __future__ import annotations

import argparse
import asyncio
import binascii
import math
import os
import queue
import random
import signal
import sys
import threading
import time
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

try:
    from bleak import BleakClient
except Exception:  # Lets --sim mode run even if bleak is not installed.
    BleakClient = None

try:
    from Crypto.Cipher import AES
except Exception:
    AES = None


# === Original BLE/decryption constants ===
KEY = bytes([(b & 255) for b in [108, 101, 97, 103, 101, 110, 100, -1, -2, 49, 56, 56, 50, 52, 54, 54]])
CHAR_UUID = "0000fff4-0000-1000-8000-00805f9b34fb"


# === Display defaults tuned for analogue/composite video ===
DEFAULT_WIDTH = 720
DEFAULT_HEIGHT = 480
SAFE_MARGIN = 26
TARGET_FPS = 30


@dataclass
class Reading:
    voltage: float
    charge: int
    raw_hex: str
    t: float


@dataclass
class Status:
    state: str
    detail: str = ""
    t: float = 0.0


def decrypt(data: bytes) -> bytes:
    """Decrypt a notification payload using the original AES-CBC-zero-IV scheme."""
    if AES is None:
        raise RuntimeError("pycryptodome is not installed. Run: pip install pycryptodome")
    if len(data) == 0 or len(data) % 16 != 0:
        raise ValueError(f"AES-CBC payload length must be a non-zero multiple of 16, got {len(data)} byte(s).")
    cipher = AES.new(KEY, AES.MODE_CBC, b"\x00" * 16)
    return cipher.decrypt(data)


def parse_payload(data: bytes) -> Tuple[float, int, str]:
    """
    Parse the decrypted packet using your original offsets.

    Original logic:
        raw = binascii.hexlify(data).decode()
        voltage = int(raw[2:5], 16) / 100.0
        power = int(raw[6:8], 16)

    This keeps the same wire interpretation, but adds validation and clamping.
    """
    raw = binascii.hexlify(data).decode()
    if len(raw) < 8:
        raise ValueError(f"Decrypted packet too short: {raw!r}")

    voltage = int(raw[2:5], 16) / 100.0
    charge = int(raw[6:8], 16)
    charge = max(0, min(100, charge))
    return voltage, charge, raw


async def ble_loop(address: str, out_q: "queue.Queue[object]", stop_evt: threading.Event) -> None:
    """Connect to BLE, subscribe to notifications, and push parsed readings to the GUI queue."""
    if BleakClient is None:
        out_q.put(Status("ERROR", "bleak is not installed"))
        return

    def notification_handler(_, data: bytearray) -> None:
        try:
            dec = decrypt(bytes(data))
            voltage, charge, raw = parse_payload(dec)
            out_q.put(Reading(voltage=voltage, charge=charge, raw_hex=raw, t=time.time()))
        except Exception as exc:
            out_q.put(Status("PACKET ERROR", str(exc), time.time()))

    while not stop_evt.is_set():
        try:
            out_q.put(Status("CONNECTING", address, time.time()))
            async with BleakClient(address) as client:
                out_q.put(Status("CONNECTED", "receiving live data", time.time()))
                await client.start_notify(CHAR_UUID, notification_handler)

                while not stop_evt.is_set():
                    await asyncio.sleep(0.25)

                try:
                    await client.stop_notify(CHAR_UUID)
                except Exception:
                    pass

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            out_q.put(Status("RECONNECTING", str(exc), time.time()))
            await asyncio.sleep(3.0)


def start_ble_worker(address: str, out_q: "queue.Queue[object]", stop_evt: threading.Event) -> threading.Thread:
    def runner() -> None:
        try:
            asyncio.run(ble_loop(address, out_q, stop_evt))
        except Exception as exc:
            out_q.put(Status("ERROR", str(exc), time.time()))

    th = threading.Thread(target=runner, name="ble-worker", daemon=True)
    th.start()
    return th


def start_sim_worker(out_q: "queue.Queue[object]", stop_evt: threading.Event) -> threading.Thread:
    """Fake data source so the HUD can be tested on the bench."""
    def runner() -> None:
        out_q.put(Status("SIMULATOR", "fake BLE feed active", time.time()))
        charge = 82
        base = 12.62
        phase = 0.0
        while not stop_evt.is_set():
            phase += 0.065
            ripple = math.sin(phase) * 0.18 + math.sin(phase * 0.27) * 0.08
            voltage = base + ripple + random.uniform(-0.035, 0.035)
            if random.random() < 0.045:
                charge += random.choice([-1, 0, 0, 1])
                charge = max(0, min(100, charge))
            fake_raw = f"sim-{voltage:.2f}-{charge:02d}"
            out_q.put(Reading(voltage=voltage, charge=charge, raw_hex=fake_raw, t=time.time()))
            time.sleep(0.45)

    th = threading.Thread(target=runner, name="sim-worker", daemon=True)
    th.start()
    return th


class CarHUD:
    def __init__(self, root: tk.Tk, data_q: "queue.Queue[object]", stop_evt: threading.Event,
                 width: int, height: int, fullscreen: bool, title: str) -> None:
        self.root = root
        self.q = data_q
        self.stop_evt = stop_evt
        self.w = width
        self.h = height
        self.fullscreen = fullscreen
        self.title = title

        self.voltage: Optional[float] = None
        self.charge: Optional[int] = None
        self.raw_hex = ""
        self.last_packet_t = 0.0
        self.start_t = time.time()
        self.packet_count = 0
        self.bad_packet_count = 0

        self.status = "BOOTING"
        self.status_detail = "initialising display"
        self.status_t = time.time()

        self.voltage_history: Deque[float] = deque(maxlen=180)
        self.charge_history: Deque[int] = deque(maxlen=180)
        self.packet_times: Deque[float] = deque(maxlen=40)

        self.frame = 0
        self.particles = self._make_particles(46)

        self.canvas = tk.Canvas(root, width=self.w, height=self.h, highlightthickness=0, bg="#03060b")
        self.canvas.pack(fill="both", expand=True)

        self.font_title = ("DejaVu Sans", max(15, int(self.h * 0.035)), "bold")
        self.font_label = ("DejaVu Sans", max(9, int(self.h * 0.020)), "bold")
        self.font_small = ("DejaVu Sans", max(8, int(self.h * 0.017)))
        self.font_num_big = ("DejaVu Sans", max(38, int(self.h * 0.105)), "bold")
        self.font_num_mid = ("DejaVu Sans", max(25, int(self.h * 0.070)), "bold")
        self.font_mono = ("DejaVu Sans Mono", max(8, int(self.h * 0.016)))

        self.root.title(self.title)
        self.root.geometry(f"{self.w}x{self.h}+0+0")
        self.root.configure(bg="#03060b")
        self.root.attributes("-fullscreen", self.fullscreen)
        if self.fullscreen:
            self.root.config(cursor="none")

        self.root.bind("<Escape>", lambda _: self.close())
        self.root.bind("q", lambda _: self.close())
        self.root.bind("Q", lambda _: self.close())
        self.root.bind("f", lambda _: self.toggle_fullscreen())
        self.root.bind("F", lambda _: self.toggle_fullscreen())

        self.draw_static()
        self.tick()

    def _make_particles(self, count: int):
        rng = random.Random(1882466)
        return [
            {
                "x": rng.uniform(SAFE_MARGIN, self.w - SAFE_MARGIN),
                "y": rng.uniform(SAFE_MARGIN, self.h - SAFE_MARGIN),
                "r": rng.uniform(0.8, 2.3),
                "a": rng.uniform(0.15, 0.75),
                "s": rng.uniform(0.2, 0.9),
            }
            for _ in range(count)
        ]

    def close(self) -> None:
        self.stop_evt.set()
        self.root.after(60, self.root.destroy)

    def toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)
        self.root.config(cursor="none" if self.fullscreen else "")

    def draw_static(self) -> None:
        c = self.canvas
        c.delete("static")

        # Vertical gradient background.
        steps = 96
        for i in range(steps):
            t = i / max(1, steps - 1)
            r = int(2 + 4 * t)
            g = int(6 + 13 * t)
            b = int(13 + 30 * t)
            colour = f"#{r:02x}{g:02x}{b:02x}"
            y0 = int(i * self.h / steps)
            y1 = int((i + 1) * self.h / steps) + 1
            c.create_rectangle(0, y0, self.w, y1, fill=colour, outline="", tags="static")

        # Safe-area frame for dirty composite displays.
        m = SAFE_MARGIN
        c.create_rectangle(m, m, self.w - m, self.h - m, outline="#17324b", width=2, tags="static")
        c.create_rectangle(m + 5, m + 5, self.w - m - 5, self.h - m - 5, outline="#07131f", width=1, tags="static")

        # Subtle grid.
        grid = 40
        for x in range(m, self.w - m + 1, grid):
            c.create_line(x, m, x, self.h - m, fill="#071523", width=1, tags="static")
        for y in range(m, self.h - m + 1, grid):
            c.create_line(m, y, self.w - m, y, fill="#071523", width=1, tags="static")

        # Perspective road-ish neon lines.
        cx = self.w / 2
        horizon = self.h * 0.40
        for off in (-0.42, -0.25, 0.25, 0.42):
            x2 = cx + off * self.w
            c.create_line(cx, horizon, x2, self.h - SAFE_MARGIN, fill="#061b2a", width=2, tags="static")

        # Top header panel.
        c.create_polygon(
            m + 22, m,
            self.w - m - 22, m,
            self.w - m - 2, m + 26,
            self.w - m - 20, m + 50,
            m + 20, m + 50,
            m + 2, m + 26,
            fill="#08101b",
            outline="#174563",
            width=2,
            tags="static",
        )

        c.create_text(
            self.w / 2,
            m + 24,
            text=self.title.upper(),
            fill="#d9fbff",
            font=self.font_title,
            tags="static",
        )

        # Bottom panel shell.
        c.create_rectangle(m + 10, self.h - 112, self.w - m - 10, self.h - m - 12,
                           fill="#07101a", outline="#17324b", width=2, tags="static")

    def process_queue(self) -> None:
        # Drain a bounded amount per frame so the GUI never bogs down.
        for _ in range(80):
            try:
                item = self.q.get_nowait()
            except queue.Empty:
                break

            if isinstance(item, Reading):
                self.voltage = item.voltage
                self.charge = item.charge
                self.raw_hex = item.raw_hex
                self.last_packet_t = item.t
                self.packet_count += 1
                self.voltage_history.append(item.voltage)
                self.charge_history.append(item.charge)
                self.packet_times.append(item.t)
            elif isinstance(item, Status):
                self.status = item.state
                self.status_detail = item.detail
                self.status_t = item.t or time.time()
                if item.state == "PACKET ERROR":
                    self.bad_packet_count += 1

    def tick(self) -> None:
        self.process_queue()
        self.frame += 1
        self.draw_dynamic()
        self.root.after(int(1000 / TARGET_FPS), self.tick)

    def draw_dynamic(self) -> None:
        c = self.canvas
        c.delete("dyn")
        now = time.time()

        self.draw_particles(now)
        self.draw_scanlines(now)

        # Layout zones.
        top = SAFE_MARGIN + 68
        bottom_panel_top = self.h - 112
        gauge_y = top + (bottom_panel_top - top) * 0.45

        left_x = self.w * 0.275
        right_x = self.w * 0.725
        radius = min(self.w, self.h) * 0.205

        voltage = self.voltage if self.voltage is not None else 0.0
        charge = self.charge if self.charge is not None else 0

        # Outer centre diamond / system panel.
        self.draw_core_panel(self.w / 2, gauge_y, radius * 0.92, now)

        self.draw_gauge(
            cx=left_x,
            cy=gauge_y,
            r=radius,
            value=voltage,
            min_value=10.0,
            max_value=15.5,
            label="SYSTEM VOLTAGE",
            unit="V",
            major_text=f"{voltage:05.2f}" if self.voltage is not None else "--.--",
            accent="#00e7ff",
            warn=voltage < 11.9 if self.voltage is not None else False,
            critical=voltage < 11.4 if self.voltage is not None else False,
        )

        self.draw_gauge(
            cx=right_x,
            cy=gauge_y,
            r=radius,
            value=float(charge),
            min_value=0,
            max_value=100,
            label="CHARGE STATE",
            unit="%",
            major_text=f"{charge:03d}" if self.charge is not None else "---",
            accent="#b66dff",
            warn=charge < 35 if self.charge is not None else False,
            critical=charge < 18 if self.charge is not None else False,
        )

        # Top status/telemetry.
        self.draw_status_bar(now)

        # Bottom live graph and stat blocks.
        graph_x0 = SAFE_MARGIN + 26
        graph_y0 = self.h - 95
        graph_x1 = int(self.w * 0.58)
        graph_y1 = self.h - SAFE_MARGIN - 24
        self.draw_sparkline(graph_x0, graph_y0, graph_x1, graph_y1)

        x_stats = graph_x1 + 20
        self.draw_stats(x_stats, graph_y0, self.w - SAFE_MARGIN - 30, graph_y1, now)

    def draw_particles(self, now: float) -> None:
        c = self.canvas
        for i, p in enumerate(self.particles):
            y = p["y"] + math.sin(now * p["s"] + i) * 2.5
            x = p["x"] + math.cos(now * p["s"] * 0.7 + i * 0.6) * 1.5
            pulse = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(now * 1.2 + i))
            intensity = int(60 + 110 * p["a"] * pulse)
            colour = f"#00{intensity:02x}{min(255, intensity + 45):02x}"
            c.create_oval(x - p["r"], y - p["r"], x + p["r"], y + p["r"],
                          fill=colour, outline="", tags="dyn")

    def draw_scanlines(self, now: float) -> None:
        c = self.canvas
        # Composite-video CRT feel; intentionally faint.
        for y in range(SAFE_MARGIN + 3, self.h - SAFE_MARGIN, 6):
            c.create_line(SAFE_MARGIN + 3, y, self.w - SAFE_MARGIN - 3, y,
                          fill="#02080e", width=1, tags="dyn")

        sweep_y = SAFE_MARGIN + ((now * 38) % max(1, self.h - SAFE_MARGIN * 2))
        c.create_rectangle(SAFE_MARGIN + 4, sweep_y, self.w - SAFE_MARGIN - 4, sweep_y + 2,
                           fill="#103046", outline="", tags="dyn")

    def draw_core_panel(self, cx: float, cy: float, r: float, now: float) -> None:
        c = self.canvas
        pulse = 0.5 + 0.5 * math.sin(now * 2.1)
        glow = int(55 + 80 * pulse)
        accent = f"#00{glow:02x}{min(255, glow + 65):02x}"

        # Diamond housing.
        pts = [
            cx, cy - r * 0.70,
            cx + r * 0.55, cy,
            cx, cy + r * 0.70,
            cx - r * 0.55, cy,
        ]
        c.create_polygon(pts, fill="#050d16", outline="#15324a", width=2, tags="dyn")
        c.create_polygon(
            cx, cy - r * 0.48,
            cx + r * 0.38, cy,
            cx, cy + r * 0.48,
            cx - r * 0.38, cy,
            fill="#071522",
            outline=accent,
            width=2,
            tags="dyn",
        )

        age = time.time() - self.last_packet_t if self.last_packet_t else 999
        live = age < 3.0 and self.voltage is not None
        state_text = "LIVE" if live else "NO DATA"
        state_colour = "#76ff8a" if live else "#ff4d6d"

        c.create_text(cx, cy - 22, text=state_text, fill=state_colour, font=self.font_label, tags="dyn")
        c.create_text(cx, cy + 3, text=f"{self.packet_rate():.1f} pkt/s", fill="#9cb8c9", font=self.font_small, tags="dyn")
        c.create_text(cx, cy + 26, text="BLE AES HUD", fill="#3f7da0", font=self.font_small, tags="dyn")

        # Rotating tiny ticks around the core.
        for i in range(18):
            a = now * 0.55 + i * (math.tau / 18)
            rr1 = r * 0.60
            rr2 = r * (0.64 + 0.04 * ((i % 3) == 0))
            x1 = cx + math.cos(a) * rr1
            y1 = cy + math.sin(a) * rr1
            x2 = cx + math.cos(a) * rr2
            y2 = cy + math.sin(a) * rr2
            c.create_line(x1, y1, x2, y2, fill="#16465d", width=1, tags="dyn")

    def draw_gauge(self, cx: float, cy: float, r: float, value: float, min_value: float, max_value: float,
                   label: str, unit: str, major_text: str, accent: str,
                   warn: bool = False, critical: bool = False) -> None:
        c = self.canvas
        bbox = (cx - r, cy - r, cx + r, cy + r)

        clamped = max(min_value, min(max_value, value))
        norm = (clamped - min_value) / max(0.0001, (max_value - min_value))

        display_accent = "#ff315b" if critical else "#ffb000" if warn else accent
        dark_accent = "#43101d" if critical else "#3d2c07" if warn else "#082936"

        # Glow rings.
        for width, alpha_col in ((22, dark_accent), (14, "#091e2b"), (7, "#113048")):
            c.create_arc(bbox, start=205, extent=-230, style=tk.ARC, outline=alpha_col, width=width, tags="dyn")

        # Progress arc.
        c.create_arc(bbox, start=205, extent=-230 * norm, style=tk.ARC,
                     outline=display_accent, width=13, tags="dyn")
        c.create_arc(cx - r * 0.88, cy - r * 0.88, cx + r * 0.88, cy + r * 0.88,
                     start=205, extent=-230 * norm, style=tk.ARC,
                     outline="#e9fdff" if not warn else display_accent, width=3, tags="dyn")

        # Tick marks.
        for i in range(31):
            n = i / 30
            angle = math.radians(205 - 230 * n)
            major = i % 5 == 0
            rr1 = r * (0.82 if major else 0.86)
            rr2 = r * 0.94
            x1 = cx + math.cos(angle) * rr1
            y1 = cy - math.sin(angle) * rr1
            x2 = cx + math.cos(angle) * rr2
            y2 = cy - math.sin(angle) * rr2
            tick_col = "#d6fbff" if n <= norm else "#214057"
            c.create_line(x1, y1, x2, y2, fill=tick_col, width=2 if major else 1, tags="dyn")

        # Needle.
        angle = math.radians(205 - 230 * norm)
        nx = cx + math.cos(angle) * r * 0.70
        ny = cy - math.sin(angle) * r * 0.70
        c.create_line(cx, cy, nx, ny, fill=display_accent, width=3, tags="dyn")
        c.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, fill="#e6fbff", outline=display_accent, width=2, tags="dyn")

        # Readout.
        c.create_text(cx, cy - r * 0.18, text=major_text, fill="#f2fdff", font=self.font_num_big, tags="dyn")
        c.create_text(cx, cy + r * 0.20, text=unit, fill=display_accent, font=self.font_label, tags="dyn")
        c.create_text(cx, cy + r * 0.47, text=label, fill="#88a9bd", font=self.font_label, tags="dyn")

        # Bottom range chips.
        c.create_text(cx - r * 0.66, cy + r * 0.79, text=f"{min_value:g}", fill="#45687e", font=self.font_small, tags="dyn")
        c.create_text(cx + r * 0.66, cy + r * 0.79, text=f"{max_value:g}", fill="#45687e", font=self.font_small, tags="dyn")

    def draw_status_bar(self, now: float) -> None:
        c = self.canvas
        m = SAFE_MARGIN
        y = m + 24

        age = now - self.last_packet_t if self.last_packet_t else 999
        live = age < 3.0 and self.voltage is not None

        status_colour = "#76ff8a" if live else "#ffb000" if self.status in ("CONNECTING", "RECONNECTING", "SIMULATOR") else "#ff4d6d"
        dot_r = 6 + 2 * math.sin(now * 4.5)

        c.create_oval(m + 36 - dot_r, y - dot_r, m + 36 + dot_r, y + dot_r,
                      fill=status_colour, outline="", tags="dyn")
        c.create_text(m + 56, y, text=self.status, fill="#ecfbff", anchor="w", font=self.font_label, tags="dyn")

        detail = self.status_detail
        if len(detail) > 50:
            detail = detail[:47] + "..."
        c.create_text(m + 56, y + 20, text=detail, fill="#5c7f95", anchor="w", font=self.font_small, tags="dyn")

        clock_text = time.strftime("%H:%M:%S")
        c.create_text(self.w - m - 50, y, text=clock_text, fill="#ecfbff", anchor="e", font=self.font_label, tags="dyn")
        c.create_text(self.w - m - 50, y + 20, text="ESC/Q EXIT  •  F FULLSCREEN", fill="#5c7f95",
                      anchor="e", font=self.font_small, tags="dyn")

    def draw_sparkline(self, x0: int, y0: int, x1: int, y1: int) -> None:
        c = self.canvas
        c.create_rectangle(x0, y0, x1, y1, fill="#050b12", outline="#15324a", width=2, tags="dyn")
        c.create_text(x0 + 12, y0 + 13, text="VOLTAGE HISTORY", fill="#d9fbff",
                      anchor="w", font=self.font_label, tags="dyn")

        # Grid.
        for i in range(1, 4):
            y = y0 + i * (y1 - y0) / 4
            c.create_line(x0 + 10, y, x1 - 10, y, fill="#0b2232", tags="dyn")
        for i in range(1, 7):
            x = x0 + i * (x1 - x0) / 7
            c.create_line(x, y0 + 25, x, y1 - 10, fill="#0b1724", tags="dyn")

        if len(self.voltage_history) < 2:
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2 + 10, text="waiting for packets...",
                          fill="#496a7f", font=self.font_small, tags="dyn")
            return

        vals = list(self.voltage_history)
        v_min = min(10.8, min(vals) - 0.1)
        v_max = max(15.0, max(vals) + 0.1)
        pad_l, pad_r, pad_t, pad_b = 14, 12, 30, 12
        plot_w = (x1 - x0 - pad_l - pad_r)
        plot_h = (y1 - y0 - pad_t - pad_b)

        pts = []
        max_len = max(2, self.voltage_history.maxlen)
        start_idx = max_len - len(vals)
        for i, v in enumerate(vals):
            idx = start_idx + i
            px = x0 + pad_l + (idx / (max_len - 1)) * plot_w
            py = y0 + pad_t + (1.0 - ((v - v_min) / max(0.001, v_max - v_min))) * plot_h
            pts.extend([px, py])

        # Glow underlay and crisp line.
        if len(pts) >= 4:
            c.create_line(*pts, fill="#07333f", width=7, smooth=True, tags="dyn")
            c.create_line(*pts, fill="#00e7ff", width=3, smooth=True, tags="dyn")

        # Last point.
        px, py = pts[-2], pts[-1]
        c.create_oval(px - 5, py - 5, px + 5, py + 5, fill="#e9fdff", outline="#00e7ff", width=2, tags="dyn")
        c.create_text(x1 - 12, y0 + 13, text=f"{vals[-1]:.2f} V", fill="#00e7ff",
                      anchor="e", font=self.font_label, tags="dyn")

    def draw_stats(self, x0: int, y0: int, x1: int, y1: int, now: float) -> None:
        c = self.canvas
        c.create_rectangle(x0, y0, x1, y1, fill="#050b12", outline="#15324a", width=2, tags="dyn")

        vals = list(self.voltage_history)
        avg_v = sum(vals) / len(vals) if vals else None
        min_v = min(vals) if vals else None
        max_v = max(vals) if vals else None
        runtime = int(now - self.start_t)
        mins, secs = divmod(runtime, 60)
        hours, mins = divmod(mins, 60)
        uptime = f"{hours:02d}:{mins:02d}:{secs:02d}"

        stats = [
            ("AVG", f"{avg_v:.2f}V" if avg_v is not None else "--"),
            ("MIN", f"{min_v:.2f}V" if min_v is not None else "--"),
            ("MAX", f"{max_v:.2f}V" if max_v is not None else "--"),
            ("PKT", str(self.packet_count)),
            ("ERR", str(self.bad_packet_count)),
            ("RUN", uptime),
        ]

        cols = 3
        rows = 2
        cell_w = (x1 - x0) / cols
        cell_h = (y1 - y0) / rows

        for idx, (label, value) in enumerate(stats):
            col = idx % cols
            row = idx // cols
            cx0 = x0 + col * cell_w
            cy0 = y0 + row * cell_h
            if col:
                c.create_line(cx0, y0 + 7, cx0, y1 - 7, fill="#0d2536", tags="dyn")
            if row:
                c.create_line(x0 + 7, cy0, x1 - 7, cy0, fill="#0d2536", tags="dyn")
            c.create_text(cx0 + 11, cy0 + 13, text=label, fill="#5c7f95",
                          anchor="w", font=self.font_small, tags="dyn")
            c.create_text(cx0 + cell_w / 2, cy0 + cell_h * 0.63, text=value, fill="#f2fdff",
                          anchor="center", font=self.font_label, tags="dyn")

        # Raw packet footer; useful for reverse/debug without dominating the car UI.
        raw = self.raw_hex
        if len(raw) > 42:
            raw = raw[:39] + "..."
        c.create_text(x0 + 10, y1 + 18, text=f"RAW {raw or '--'}",
                      fill="#36586d", anchor="w", font=self.font_mono, tags="dyn")

    def packet_rate(self) -> float:
        if len(self.packet_times) < 2:
            return 0.0
        span = self.packet_times[-1] - self.packet_times[0]
        if span <= 0:
            return 0.0
        return (len(self.packet_times) - 1) / span


def parse_size(size: str) -> Tuple[int, int]:
    try:
        w_s, h_s = size.lower().split("x", 1)
        w, h = int(w_s), int(h_s)
        if w < 320 or h < 240:
            raise ValueError
        return w, h
    except Exception:
        raise argparse.ArgumentTypeError("size must look like 720x480 or 720x576")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Jakka351's Raspberry Pi Battery Monitor.")
    p.add_argument("address", nargs="?", help="BLE MAC/address, e.g. AA:BB:CC:DD:EE:FF")
    p.add_argument("--sim", action="store_true", help="Run with simulated data instead of BLE.")
    p.add_argument("--size", type=parse_size, default=(DEFAULT_WIDTH, DEFAULT_HEIGHT),
                   help="Window/canvas size. Good composite choices: 720x480 NTSC, 720x576 PAL.")
    p.add_argument("--fullscreen", dest="fullscreen", action="store_true", default=True,
                   help="Run fullscreen. Default: on.")
    p.add_argument("--windowed", dest="fullscreen", action="store_false",
                   help="Run in a window.")
    p.add_argument("--title", default="FPV GS 002 Battery Monitor", help="HUD title text.")
    return p


def main() -> int:
    args = build_arg_parser().parse_args()

    if not args.sim and not args.address:
        print("Usage: monitor.py <BLE_MAC> or monitor.py --sim", file=sys.stderr)
        return 2

    data_q: "queue.Queue[object]" = queue.Queue()
    stop_evt = threading.Event()

    # Clean shutdown from terminal/systemd.
    def handle_signal(_signum, _frame):
        stop_evt.set()

    try:
        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)
    except Exception:
        pass

    if args.sim:
        start_sim_worker(data_q, stop_evt)
    else:
        start_ble_worker(args.address, data_q, stop_evt)

    root = tk.Tk()
    width, height = args.size
    app = CarHUD(root, data_q, stop_evt, width, height, args.fullscreen, args.title)

    try:
        root.mainloop()
    finally:
        stop_evt.set()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
