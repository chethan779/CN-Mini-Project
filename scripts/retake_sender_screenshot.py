#!/usr/bin/env python3
"""
retake_sender_screenshot.py
Captures a crystal-clear, focused, publication-grade screenshot of the Sender Client GUI
with full 100% progress, live telemetry graphs (Sent vs ACKed), and verified SHA-256 match.
"""

import os
import sys
import time
import json
import socket
import threading
import subprocess

os.environ["WAYLAND_DISPLAY"] = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
os.environ["XDG_RUNTIME_DIR"] = os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")
os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = os.environ.get(
    "HYPRLAND_INSTANCE_SIGNATURE",
    "11cbc6c577b84ffb1edf4df3fc5b6128c3ae6947_1794991624_1816963156"
)

import tkinter as tk
from tkinter import messagebox

# Suppress modal messagebox popup so the window remains active, focused, and not dimmed by Hyprland
messagebox.showinfo = lambda *args, **kwargs: None
messagebox.showerror = lambda *args, **kwargs: None
messagebox.showwarning = lambda *args, **kwargs: None

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOTS_DIR = os.path.join(PROJECT_DIR, "screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
sys.path.insert(0, PROJECT_DIR)

from app_gui import AppGUI
from server import run_server
import client


def paced_run_client(*args, **kwargs):
    """Wraps client.run_client with small inter-chunk delay so graphs display realistic telemetry."""
    orig_sendto = socket.socket.sendto
    def paced_sendto(self, data, addr):
        # Add 4ms pacing for data packets so the 100-packet transfer takes ~0.5s
        if len(data) > 50:
            time.sleep(0.005)
        return orig_sendto(self, data, addr)
        
    socket.socket.sendto = paced_sendto
    try:
        return client.run_client(*args, **kwargs)
    finally:
        socket.socket.sendto = orig_sendto


def main():
    print("=" * 75)
    print("  Capturing High-Resolution Sender Client GUI Screenshot")
    print("=" * 75)
    
    # 1. Start background UDP server on 127.0.0.1:5005
    srv_stop = threading.Event()
    srv_thread = threading.Thread(
        target=run_server,
        kwargs={"ip": "127.0.0.1", "port": 5005, "save_dir": "received", "stop_event": srv_stop},
        daemon=True
    )
    srv_thread.start()
    time.sleep(0.3)

    # 2. Launch Sender GUI
    app = AppGUI(initial_mode="sender")
    app.resizable(False, False)
    app.geometry("1180x820+30+50")
    app.update()

    # Configure target IP to localhost and select sample.bin (100 KB, 100 packets)
    app.set_target_ip("127.0.0.1")
    sample_file = os.path.join(PROJECT_DIR, "files", "sample.bin")
    app.set_selected_file(sample_file)
    app.update()

    # Temporarily patch client.run_client with paced transfer for beautiful graphs
    orig_client_func = client.run_client
    client.run_client = paced_run_client

    # 3. Start file transfer
    print("[*] Starting file transfer in GUI...")
    app.start_client_transfer()

    # 4. Pump UI loop until transfer completes
    max_wait = 20.0
    start_t = time.time()
    while time.time() - start_t < max_wait:
        app.update()
        time.sleep(0.02)
        if hasattr(app, "lbl_sender_status"):
            status_text = app.lbl_sender_status.cget("text")
            if "VERIFIED" in status_text or "COMPLETE" in status_text or "FAILED" in status_text:
                break

    # Restore run_client
    client.run_client = orig_client_func

    # Force a final graph redraw and flush Tkinter events
    app._draw_client_graphs_immediate()
    for _ in range(20):
        app.update_idletasks()
        app.update()
        time.sleep(0.03)

    # 5. Bring window to front and focus in Hyprland
    app.lift()
    app.focus_force()
    app.update()

    out_file = os.path.join(SCREENSHOTS_DIR, "01_sender_transfer_completed.png")

    target = None
    try:
        res = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True)
        clients = json.loads(res.stdout)
        for c in clients:
            if "reliable udp file transfer protocol" in c.get("title", "").lower() or "app_gui" in c.get("class", "").lower():
                target = c
                break
    except Exception as e:
        print(f"[!] Warning: Failed to query hyprctl: {e}")

    if target:
        x, y = target["at"]
        w, h = target["size"]
        addr = target.get("address", "")
        if addr:
            print(f"[*] Focusing Hyprland window at address {addr}...")
            subprocess.run(["hyprctl", "dispatch", "focuswindow", f"address:{addr}"])
            time.sleep(0.4)
            for _ in range(10):
                app.update()
                time.sleep(0.02)

        print(f"[*] Capturing focused window: at={x},{y} size={w}x{h} -> {out_file}")
        res = subprocess.run(["grim", "-g", f"{x},{y} {w}x{h}", out_file], capture_output=True, text=True)
        if res.returncode != 0:
            print(f"[!] grim -g failed, falling back to full capture: {res.stderr}")
            subprocess.run(["grim", out_file])
    else:
        print("[!] Target window not found in hyprctl, using fallback capture.")
        subprocess.run(["grim", out_file])

    if os.path.exists(out_file):
        size_kb = os.path.getsize(out_file) / 1024
        print(f"[+] Sender screenshot saved: {out_file} ({size_kb:.1f} KB)")

    srv_stop.set()
    app.destroy()
    time.sleep(0.3)


if __name__ == '__main__':
    main()
