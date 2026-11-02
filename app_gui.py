"""
app_gui.py - Unified Graphical User Interface for Reliable UDP File Transfer
=============================================================================
Provides an interactive Tkinter UI for both Sender (Client) and Receiver (Server)
with live progress bars, packet statistics, loss percentage calculation,
real-time matplotlib telemetry graphs, and file chooser dialogs.

Uses a thread-safe Queue architecture to eliminate GUI freezing and closure bugs.
"""

import os
import sys
import time
import json
import socket
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from protocol import (
    DEFAULT_SERVER_PORT,
    DEFAULT_SDN_IP, DEFAULT_SDN_PORT,
    DEFAULT_WINDOW_SIZE, DEFAULT_TIMEOUT,
    CHUNK_SIZE, get_file_sha256
)
from client import run_client, read_file_chunks
from server import run_server

# Modern dark-mode color theme
THEME = {
    "bg": "#1e1e2e",           # Dark background
    "card_bg": "#252538",      # Card panel background
    "border": "#363654",       # Border color
    "text": "#cdd6f4",         # Main text
    "text_dim": "#a6adc8",     # Subdued text
    "accent": "#89b4fa",       # Blue accent
    "accent_hover": "#74c7ec", # Light blue
    "green": "#a6e3a1",        # Success green
    "red": "#f38ba8",          # Warning red
    "orange": "#fab387",       # Alert orange
    "yellow": "#f9e2af",       # Highlight yellow
    "entry_bg": "#181825",     # Input background
    "console_bg": "#11111b"    # Console background
}


def get_local_lan_ip() -> str:
    """Detects current machine's outbound LAN IP for easy multi-laptop setup."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class ModernCard(tk.Frame):
    """Clean styled container with subtle border."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=THEME["card_bg"], highlightbackground=THEME["border"],
                         highlightthickness=1, padx=12, pady=10, **kwargs)


class AppGUI(tk.Tk):
    def __init__(self, initial_mode: Optional[str] = None):
        super().__init__()
        self.title("Reliable UDP File Transfer Protocol")
        self.geometry("1100x820")
        self.minsize(980, 720)
        self.configure(bg=THEME["bg"])

        self.lan_ip = get_local_lan_ip()

        # Thread-safe event queue for GUI telemetry
        self.event_queue: queue.Queue = queue.Queue()
        self._poll_timer = None

        # Worker threads & cancel events
        self.current_role: Optional[str] = None
        self.client_thread: Optional[threading.Thread] = None
        self.client_stop_event = threading.Event()
        self.server_thread: Optional[threading.Thread] = None
        self.server_stop_event = threading.Event()
        self.server_running = False

        # Throttling timer for 60fps graph updates
        self.last_client_graph_update = 0.0
        self.last_server_graph_update = 0.0

        # Client telemetry metrics
        self.client_stats = {
            "total_sent": 0,
            "acked": 0,
            "retrans": 0,
            "timeouts": 0,
            "loss_pct": 0.0,
            "throughput_kbps": 0.0,
            "total_chunks": 0,
            "start_time": 0.0,
            "history_time": [0.0],
            "history_acked": [0],
            "history_sent": [0]
        }

        # Server telemetry metrics
        self.server_stats = {
            "received": 0,
            "in_order": 0,
            "duplicates": 0,
            "buffered": 0,
            "total_chunks": 0,
            "start_time": 0.0,
            "history_time": [0.0],
            "history_chunks": [0]
        }

        self._configure_styles()
        self._build_header_nav()

        # Main content container
        self.content_frame = tk.Frame(self, bg=THEME["bg"])
        self.content_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 14))

        # Hook clean exit on window close
        self.protocol("WM_DELETE_WINDOW", self.on_window_close)

        # Start periodic queue polling
        self._poll_event_queue()

        if initial_mode in ("sender", "receiver"):
            self.switch_to_mode(initial_mode)
        else:
            self.show_role_selector()

    def _configure_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure("Green.Horizontal.TProgressbar",
                        troughcolor=THEME["entry_bg"],
                        background=THEME["green"],
                        bordercolor=THEME["border"],
                        lightcolor=THEME["green"],
                        darkcolor=THEME["green"])

        style.configure("Blue.Horizontal.TProgressbar",
                        troughcolor=THEME["entry_bg"],
                        background=THEME["accent"],
                        bordercolor=THEME["border"],
                        lightcolor=THEME["accent"],
                        darkcolor=THEME["accent"])

    def _build_header_nav(self):
        nav_bar = tk.Frame(self, bg=THEME["card_bg"], height=56, highlightbackground=THEME["border"],
                           highlightthickness=1)
        nav_bar.pack(fill=tk.X, side=tk.TOP, padx=16, pady=12)

        lbl_title = tk.Label(nav_bar, text="⚡ Reliable UDP File Transfer Protocol",
                             font=("DejaVu Sans", 14, "bold"), fg=THEME["accent"], bg=THEME["card_bg"])
        lbl_title.pack(side=tk.LEFT, padx=16, pady=8)

        lbl_sub = tk.Label(nav_bar, text=f"| LAN IP: {self.lan_ip}",
                           font=("DejaVu Sans", 9), fg=THEME["text_dim"], bg=THEME["card_bg"])
        lbl_sub.pack(side=tk.LEFT, pady=8)

        btn_box = tk.Frame(nav_bar, bg=THEME["card_bg"])
        btn_box.pack(side=tk.RIGHT, padx=12, pady=8)

        self.btn_nav_sender = tk.Button(btn_box, text="🚀 Sender (Client)", font=("DejaVu Sans", 9, "bold"),
                                        bg=THEME["entry_bg"], fg=THEME["text"], activebackground=THEME["accent"],
                                        relief=tk.FLAT, padx=12, pady=5, cursor="hand2",
                                        command=lambda: self.switch_to_mode("sender"))
        self.btn_nav_sender.pack(side=tk.LEFT, padx=4)

        self.btn_nav_receiver = tk.Button(btn_box, text="📥 Receiver (Server)", font=("DejaVu Sans", 9, "bold"),
                                          bg=THEME["entry_bg"], fg=THEME["text"], activebackground=THEME["green"],
                                          relief=tk.FLAT, padx=12, pady=5, cursor="hand2",
                                          command=lambda: self.switch_to_mode("receiver"))
        self.btn_nav_receiver.pack(side=tk.LEFT, padx=4)

        btn_switch_role = tk.Button(btn_box, text="🔄 Choose Role", font=("DejaVu Sans", 9),
                                    bg=THEME["card_bg"], fg=THEME["text_dim"], relief=tk.GROOVE,
                                    padx=8, pady=5, cursor="hand2", command=self.show_role_selector)
        btn_switch_role.pack(side=tk.LEFT, padx=4)

        btn_new_window = tk.Button(btn_box, text="➕ New Window", font=("DejaVu Sans", 9),
                                   bg=THEME["card_bg"], fg=THEME["accent"], relief=tk.GROOVE,
                                   padx=8, pady=5, cursor="hand2", command=self.open_new_window)
        btn_new_window.pack(side=tk.LEFT, padx=4)

    def open_new_window(self):
        """Spawns an independent second window so user can run Sender and Receiver simultaneously."""
        other_mode = "receiver" if self.current_role == "sender" else "sender"
        subprocess.Popen([sys.executable, __file__])

    def clear_content(self):
        for widget in self.content_frame.winfo_children():
            widget.destroy()

    # =========================================================================
    # ROLE SELECTOR SCREEN
    # =========================================================================
    def show_role_selector(self):
        self.clear_content()
        self.current_role = None
        self.btn_nav_sender.config(bg=THEME["entry_bg"], fg=THEME["text"])
        self.btn_nav_receiver.config(bg=THEME["entry_bg"], fg=THEME["text"])

        welcome_card = ModernCard(self.content_frame)
        welcome_card.place(relx=0.5, rely=0.45, anchor=tk.CENTER, width=670, height=450)

        lbl_head = tk.Label(welcome_card, text="Choose Node Operating Role",
                            font=("DejaVu Sans", 18, "bold"), fg=THEME["text"], bg=THEME["card_bg"])
        lbl_head.pack(pady=(20, 6))

        lbl_desc = tk.Label(welcome_card,
                            text="Select whether this window acts as the Sender (Client)\n"
                                 "or Receiver (Server). You can test both locally on 127.0.0.1\n"
                                 "or across multiple laptops on your Wi-Fi.",
                            font=("DejaVu Sans", 10), fg=THEME["text_dim"], bg=THEME["card_bg"], justify=tk.CENTER)
        lbl_desc.pack(pady=(0, 24))

        cards_frame = tk.Frame(welcome_card, bg=THEME["card_bg"])
        cards_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

        # Card 1: Sender
        card_sender = tk.Frame(cards_frame, bg=THEME["entry_bg"], highlightbackground=THEME["accent"],
                               highlightthickness=2, padx=16, pady=16, cursor="hand2")
        card_sender.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        card_sender.bind("<Button-1>", lambda e: self.select_role_and_start("sender"))

        lbl_s_icon = tk.Label(card_sender, text="🚀", font=("DejaVu Sans", 32), bg=THEME["entry_bg"])
        lbl_s_icon.pack(pady=4)
        lbl_s_icon.bind("<Button-1>", lambda e: self.select_role_and_start("sender"))

        lbl_s_title = tk.Label(card_sender, text="Sender (Client)", font=("DejaVu Sans", 13, "bold"),
                               fg=THEME["accent"], bg=THEME["entry_bg"])
        lbl_s_title.pack(pady=2)
        lbl_s_title.bind("<Button-1>", lambda e: self.select_role_and_start("sender"))

        lbl_s_sub = tk.Label(card_sender,
                             text="Pick a file from device,\n"
                                  "fragment into chunks, and transmit\n"
                                  "with Go-Back-N sliding window.",
                             font=("DejaVu Sans", 9), fg=THEME["text_dim"], bg=THEME["entry_bg"], justify=tk.CENTER)
        lbl_s_sub.pack(pady=6)
        lbl_s_sub.bind("<Button-1>", lambda e: self.select_role_and_start("sender"))

        btn_s = tk.Button(card_sender, text="Launch Sender & Pick File →", font=("DejaVu Sans", 9, "bold"),
                          bg=THEME["accent"], fg="#11111b", relief=tk.FLAT, pady=6, cursor="hand2",
                          command=lambda: self.select_role_and_start("sender"))
        btn_s.pack(side=tk.BOTTOM, fill=tk.X, pady=6)

        # Card 2: Receiver
        card_receiver = tk.Frame(cards_frame, bg=THEME["entry_bg"], highlightbackground=THEME["green"],
                                 highlightthickness=2, padx=16, pady=16, cursor="hand2")
        card_receiver.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10)
        card_receiver.bind("<Button-1>", lambda e: self.select_role_and_start("receiver"))

        lbl_r_icon = tk.Label(card_receiver, text="📥", font=("DejaVu Sans", 32), bg=THEME["entry_bg"])
        lbl_r_icon.pack(pady=4)
        lbl_r_icon.bind("<Button-1>", lambda e: self.select_role_and_start("receiver"))

        lbl_r_title = tk.Label(card_receiver, text="Receiver (Server)", font=("DejaVu Sans", 13, "bold"),
                               fg=THEME["green"], bg=THEME["entry_bg"])
        lbl_r_title.pack(pady=2)
        lbl_r_title.bind("<Button-1>", lambda e: self.select_role_and_start("receiver"))

        lbl_r_sub = tk.Label(card_receiver,
                             text="Listen for incoming UDP files,\n"
                                  "buffer out-of-order chunks,\n"
                                  "and verify SHA-256 integrity.",
                             font=("DejaVu Sans", 9), fg=THEME["text_dim"], bg=THEME["entry_bg"], justify=tk.CENTER)
        lbl_r_sub.pack(pady=6)
        lbl_r_sub.bind("<Button-1>", lambda e: self.select_role_and_start("receiver"))

        btn_r = tk.Button(card_receiver, text="Launch Receiver & Start Listening →", font=("DejaVu Sans", 9, "bold"),
                          bg=THEME["green"], fg="#11111b", relief=tk.FLAT, pady=6, cursor="hand2",
                          command=lambda: self.select_role_and_start("receiver"))
        btn_r.pack(side=tk.BOTTOM, fill=tk.X, pady=6)

    def select_role_and_start(self, role: str):
        self.switch_to_mode(role)
        if role == "sender":
            self.after(250, self.choose_file_dialog)
        elif role == "receiver":
            self.after(250, self.start_server_thread)

    def switch_to_mode(self, mode: str):
        self.current_role = mode
        self.clear_content()
        if mode == "sender":
            self.btn_nav_sender.config(bg=THEME["accent"], fg="#11111b")
            self.btn_nav_receiver.config(bg=THEME["entry_bg"], fg=THEME["text"])
            self._build_sender_view()
        else:
            self.btn_nav_sender.config(bg=THEME["entry_bg"], fg=THEME["text"])
            self.btn_nav_receiver.config(bg=THEME["green"], fg="#11111b")
            self._build_receiver_view()

    # =========================================================================
    # SENDER (CLIENT) VIEW
    # =========================================================================
    def _build_sender_view(self):
        left_col = tk.Frame(self.content_frame, bg=THEME["bg"])
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        right_col = tk.Frame(self.content_frame, bg=THEME["bg"])
        right_col.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(8, 0))

        # --- LEFT: Config Card ---
        cfg_card = ModernCard(left_col)
        cfg_card.pack(fill=tk.X, pady=(0, 8))

        lbl_c_hdr = tk.Label(cfg_card, text="⚙️ Transmission & Network Parameters",
                             font=("DejaVu Sans", 10, "bold"), fg=THEME["accent"], bg=THEME["card_bg"])
        lbl_c_hdr.pack(anchor=tk.W, pady=(0, 4))

        # Quick preset buttons for IP
        preset_bar = tk.Frame(cfg_card, bg=THEME["card_bg"])
        preset_bar.pack(fill=tk.X, pady=(0, 6))

        tk.Label(preset_bar, text="Quick IP Presets:", font=("DejaVu Sans", 8),
                 fg=THEME["text_dim"], bg=THEME["card_bg"]).pack(side=tk.LEFT, padx=(0, 6))

        btn_p1 = tk.Button(preset_bar, text="💻 Localhost (127.0.0.1)", font=("DejaVu Sans", 8),
                           bg=THEME["entry_bg"], fg=THEME["accent"], relief=tk.GROOVE, padx=6, pady=2,
                           cursor="hand2", command=lambda: self.set_target_ip("127.0.0.1"))
        btn_p1.pack(side=tk.LEFT, padx=3)

        btn_p2 = tk.Button(preset_bar, text=f"📡 LAN ({self.lan_ip})", font=("DejaVu Sans", 8),
                           bg=THEME["entry_bg"], fg=THEME["green"], relief=tk.GROOVE, padx=6, pady=2,
                           cursor="hand2", command=lambda: self.set_target_ip(self.lan_ip))
        btn_p2.pack(side=tk.LEFT, padx=3)

        btn_p3 = tk.Button(preset_bar, text="🌐 Mininet (10.0.0.3)", font=("DejaVu Sans", 8),
                           bg=THEME["entry_bg"], fg=THEME["yellow"], relief=tk.GROOVE, padx=6, pady=2,
                           cursor="hand2", command=lambda: self.set_target_ip("10.0.0.3"))
        btn_p3.pack(side=tk.LEFT, padx=3)

        row1 = tk.Frame(cfg_card, bg=THEME["card_bg"])
        row1.pack(fill=tk.X, pady=2)

        tk.Label(row1, text="Server IP:", font=("DejaVu Sans", 9), fg=THEME["text_dim"], bg=THEME["card_bg"]).pack(side=tk.LEFT)
        self.ent_server_ip = tk.Entry(row1, font=("DejaVu Sans", 9), width=13, bg=THEME["entry_bg"],
                                      fg=THEME["text"], insertbackground=THEME["text"], relief=tk.FLAT)
        # Default to 127.0.0.1 so it works instantly on the same machine!
        self.ent_server_ip.insert(0, "127.0.0.1")
        self.ent_server_ip.pack(side=tk.LEFT, padx=(4, 12))

        tk.Label(row1, text="Port:", font=("DejaVu Sans", 9), fg=THEME["text_dim"], bg=THEME["card_bg"]).pack(side=tk.LEFT)
        self.ent_server_port = tk.Entry(row1, font=("DejaVu Sans", 9), width=7, bg=THEME["entry_bg"],
                                        fg=THEME["text"], insertbackground=THEME["text"], relief=tk.FLAT)
        self.ent_server_port.insert(0, str(DEFAULT_SERVER_PORT))
        self.ent_server_port.pack(side=tk.LEFT, padx=(4, 12))

        tk.Label(row1, text="SDN IP:", font=("DejaVu Sans", 9), fg=THEME["text_dim"], bg=THEME["card_bg"]).pack(side=tk.LEFT)
        self.ent_sdn_ip = tk.Entry(row1, font=("DejaVu Sans", 9), width=12, bg=THEME["entry_bg"],
                                   fg=THEME["text"], insertbackground=THEME["text"], relief=tk.FLAT)
        self.ent_sdn_ip.insert(0, DEFAULT_SDN_IP)
        self.ent_sdn_ip.pack(side=tk.LEFT, padx=(4, 12))

        tk.Label(row1, text="SDN Port:", font=("DejaVu Sans", 9), fg=THEME["text_dim"], bg=THEME["card_bg"]).pack(side=tk.LEFT)
        self.ent_sdn_port = tk.Entry(row1, font=("DejaVu Sans", 9), width=6, bg=THEME["entry_bg"],
                                     fg=THEME["text"], insertbackground=THEME["text"], relief=tk.FLAT)
        self.ent_sdn_port.insert(0, str(DEFAULT_SDN_PORT))
        self.ent_sdn_port.pack(side=tk.LEFT, padx=(4, 0))

        # --- LEFT: File Selection Card ---
        file_card = ModernCard(left_col)
        file_card.pack(fill=tk.X, pady=(0, 8))

        file_top = tk.Frame(file_card, bg=THEME["card_bg"])
        file_top.pack(fill=tk.X)

        tk.Label(file_top, text="📁 Selected File for Transfer", font=("DejaVu Sans", 10, "bold"),
                 fg=THEME["accent"], bg=THEME["card_bg"]).pack(side=tk.LEFT)

        btn_browse = tk.Button(file_top, text="Choose File from Device...", font=("DejaVu Sans", 9, "bold"),
                               bg=THEME["accent"], fg="#11111b", relief=tk.FLAT, padx=12, pady=4,
                               cursor="hand2", command=self.choose_file_dialog)
        btn_browse.pack(side=tk.RIGHT)

        self.lbl_file_path = tk.Label(file_card, text="No file selected yet.", font=("DejaVu Sans", 9),
                                      fg=THEME["yellow"], bg=THEME["card_bg"], anchor=tk.W)
        self.lbl_file_path.pack(fill=tk.X, pady=(6, 2))

        self.lbl_file_meta = tk.Label(file_card, text="Size: 0 bytes | Chunks: 0 | SHA-256: -",
                                      font=("DejaVu Sans", 8), fg=THEME["text_dim"], bg=THEME["card_bg"], anchor=tk.W)
        self.lbl_file_meta.pack(fill=tk.X)

        # Default sample file if exists
        default_file = "files/sample.bin"
        if os.path.exists(default_file):
            self.set_selected_file(default_file)

        # --- LEFT: Transfer Controls & Progress Card ---
        ctl_card = ModernCard(left_col)
        ctl_card.pack(fill=tk.X, pady=(0, 8))

        btn_row = tk.Frame(ctl_card, bg=THEME["card_bg"])
        btn_row.pack(fill=tk.X, pady=(0, 8))

        self.btn_send_start = tk.Button(btn_row, text="▶ Start File Transfer", font=("DejaVu Sans", 10, "bold"),
                                        bg=THEME["green"], fg="#11111b", relief=tk.FLAT, padx=16, pady=7,
                                        cursor="hand2", command=self.start_client_transfer)
        self.btn_send_start.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_send_abort = tk.Button(btn_row, text="⏹ Abort", font=("DejaVu Sans", 10, "bold"),
                                        bg=THEME["red"], fg="#11111b", relief=tk.FLAT, padx=14, pady=7,
                                        state=tk.DISABLED, cursor="hand2", command=self.abort_client_transfer)
        self.btn_send_abort.pack(side=tk.LEFT)

        self.lbl_sender_status = tk.Label(btn_row, text="Status: IDLE", font=("DejaVu Sans", 9, "bold"),
                                          fg=THEME["text_dim"], bg=THEME["card_bg"])
        self.lbl_sender_status.pack(side=tk.RIGHT, padx=6)

        prog_row = tk.Frame(ctl_card, bg=THEME["card_bg"])
        prog_row.pack(fill=tk.X, pady=2)

        self.sender_prog = ttk.Progressbar(prog_row, style="Green.Horizontal.TProgressbar",
                                           orient=tk.HORIZONTAL, mode="determinate")
        self.sender_prog.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        self.lbl_sender_pct = tk.Label(prog_row, text="0.0%", font=("DejaVu Sans", 11, "bold"),
                                       fg=THEME["green"], bg=THEME["card_bg"], width=6)
        self.lbl_sender_pct.pack(side=tk.RIGHT)

        self.lbl_sender_chunks = tk.Label(ctl_card, text="Packets Transferred: 0 / 0", font=("DejaVu Sans", 8),
                                          fg=THEME["text_dim"], bg=THEME["card_bg"], anchor=tk.W)
        self.lbl_sender_chunks.pack(fill=tk.X, pady=(2, 0))

        # --- LEFT: Live Metrics Grid ---
        metrics_card = ModernCard(left_col)
        metrics_card.pack(fill=tk.X, pady=(0, 8))

        tk.Label(metrics_card, text="📊 Live Protocol Telemetry & Loss Rate", font=("DejaVu Sans", 10, "bold"),
                 fg=THEME["accent"], bg=THEME["card_bg"]).pack(anchor=tk.W, pady=(0, 6))

        m_grid = tk.Frame(metrics_card, bg=THEME["card_bg"])
        m_grid.pack(fill=tk.X)

        self.stat_c_sent = self._create_stat_badge(m_grid, "Packets Sent", "0", 0, 0)
        self.stat_c_acked = self._create_stat_badge(m_grid, "Transferred (ACK)", "0", 0, 1)
        self.stat_c_retrans = self._create_stat_badge(m_grid, "Retransmissions", "0", 0, 2)
        self.stat_c_loss = self._create_stat_badge(m_grid, "Loss %", "0.00%", 1, 0, color=THEME["orange"])
        self.stat_c_speed = self._create_stat_badge(m_grid, "Throughput", "0.0 kbps", 1, 1, color=THEME["green"])
        self.stat_c_alerts = self._create_stat_badge(m_grid, "SDN Alerts", "0", 1, 2, color=THEME["yellow"])

        # --- LEFT: Real-Time Event Log Console ---
        log_card = ModernCard(left_col)
        log_card.pack(fill=tk.BOTH, expand=True)

        tk.Label(log_card, text="📜 Protocol Event Stream", font=("DejaVu Sans", 9, "bold"),
                 fg=THEME["text_dim"], bg=THEME["card_bg"]).pack(anchor=tk.W, pady=(0, 4))

        self.txt_sender_log = tk.Text(log_card, bg=THEME["console_bg"], fg=THEME["text"],
                                      insertbackground=THEME["text"], font=("Monospace", 8),
                                      relief=tk.FLAT, height=10)
        self.txt_sender_log.pack(fill=tk.BOTH, expand=True)

        self.txt_sender_log.tag_config("SYN", foreground=THEME["accent"])
        self.txt_sender_log.tag_config("ACK", foreground=THEME["green"])
        self.txt_sender_log.tag_config("DATA", foreground=THEME["text"])
        self.txt_sender_log.tag_config("TIMEOUT", foreground=THEME["red"])
        self.txt_sender_log.tag_config("SDN", foreground=THEME["yellow"])
        self.txt_sender_log.tag_config("FIN", foreground="#b4befe")

        # --- RIGHT: Embedded Matplotlib Real-Time Graphs ---
        graph_card = ModernCard(right_col)
        graph_card.pack(fill=tk.BOTH, expand=True)

        tk.Label(graph_card, text="📈 Real-Time Performance & Loss Telemetry", font=("DejaVu Sans", 10, "bold"),
                 fg=THEME["accent"], bg=THEME["card_bg"]).pack(anchor=tk.W, pady=(0, 4))

        self.fig_client = Figure(figsize=(5.5, 6.2), dpi=100, facecolor=THEME["card_bg"])
        self.ax_c_progress = self.fig_client.add_subplot(211)
        self.ax_c_loss = self.fig_client.add_subplot(212)
        self.fig_client.tight_layout(pad=2.8)

        self._init_client_graphs()

        self.canvas_client = FigureCanvasTkAgg(self.fig_client, master=graph_card)
        self.canvas_client.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def set_target_ip(self, ip_str: str):
        if hasattr(self, "ent_server_ip"):
            self.ent_server_ip.delete(0, tk.END)
            self.ent_server_ip.insert(0, ip_str)

    def _create_stat_badge(self, parent, label: str, value: str, row: int, col: int, color: str = THEME["text"]):
        frame = tk.Frame(parent, bg=THEME["entry_bg"], highlightbackground=THEME["border"],
                         highlightthickness=1, padx=8, pady=6)
        frame.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")
        parent.grid_columnconfigure(col, weight=1)

        tk.Label(frame, text=label, font=("DejaVu Sans", 8), fg=THEME["text_dim"], bg=THEME["entry_bg"]).pack(anchor=tk.W)
        lbl_val = tk.Label(frame, text=value, font=("DejaVu Sans", 11, "bold"), fg=color, bg=THEME["entry_bg"])
        lbl_val.pack(anchor=tk.W, pady=(2, 0))
        return lbl_val

    def _init_client_graphs(self):
        for ax in (self.ax_c_progress, self.ax_c_loss):
            ax.set_facecolor(THEME["entry_bg"])
            ax.tick_params(colors=THEME["text_dim"], labelsize=8)
            for spine in ax.spines.values():
                spine.set_color(THEME["border"])

        self.ax_c_progress.set_title("Packet Transfer Progress (ACKed vs Sent)", color=THEME["text"], fontsize=9, pad=6)
        self.ax_c_progress.set_xlabel("Elapsed Time (s)", color=THEME["text_dim"], fontsize=8)
        self.ax_c_progress.set_ylabel("Sequence Number", color=THEME["text_dim"], fontsize=8)

        self.ax_c_loss.set_title("Packet Delivery & Loss Breakdown", color=THEME["text"], fontsize=9, pad=6)
        self.ax_c_loss.set_ylabel("Packets", color=THEME["text_dim"], fontsize=8)

    def choose_file_dialog(self):
        filepath = filedialog.askopenfilename(
            title="Select File to Transfer",
            initialdir=os.getcwd(),
            filetypes=[("All Files", "*.*"), ("PDF Documents", "*.pdf"), ("Binary Files", "*.bin"), ("Text Files", "*.txt")]
        )
        if filepath:
            self.set_selected_file(filepath)

    def set_selected_file(self, filepath: str):
        if not os.path.exists(filepath):
            return
        self.selected_filepath = filepath
        filesize = os.path.getsize(filepath)
        chunks = read_file_chunks(filepath, CHUNK_SIZE)
        total_chunks = len(chunks)
        sha = get_file_sha256(filepath)

        size_str = f"{filesize} bytes"
        if filesize > 1024 * 1024:
            size_str = f"{filesize / (1024 * 1024):.2f} MB"
        elif filesize > 1024:
            size_str = f"{filesize / 1024:.2f} KB"

        self.lbl_file_path.config(text=f"📄 {os.path.basename(filepath)} ({filepath})")
        self.lbl_file_meta.config(text=f"Size: {size_str}  |  Chunks: {total_chunks}  |  SHA-256: {sha[:20]}...")

    def log_sender(self, message: str, tag: str = "DATA"):
        if hasattr(self, "txt_sender_log"):
            self.txt_sender_log.insert(tk.END, message + "\n", tag)
            self.txt_sender_log.see(tk.END)

    def start_client_transfer(self):
        if not hasattr(self, "selected_filepath") or not os.path.exists(self.selected_filepath):
            messagebox.showwarning("No File Selected", "Please choose a file to transfer first.")
            return

        server_ip = self.ent_server_ip.get().strip()
        try:
            port = int(self.ent_server_port.get().strip())
            sdn_port = int(self.ent_sdn_port.get().strip())
        except ValueError:
            messagebox.showerror("Invalid Port", "Port numbers must be valid integers.")
            return

        sdn_ip = self.ent_sdn_ip.get().strip()

        # Reset UI & stats
        self.client_stop_event.clear()
        self.btn_send_start.config(state=tk.DISABLED)
        self.btn_send_abort.config(state=tk.NORMAL)
        self.lbl_sender_status.config(text="Status: CONNECTING...", fg=THEME["yellow"])
        self.sender_prog["value"] = 0
        self.lbl_sender_pct.config(text="0.0%")
        self.txt_sender_log.delete("1.0", tk.END)

        self.client_stats = {
            "total_sent": 0,
            "acked": 0,
            "retrans": 0,
            "timeouts": 0,
            "loss_pct": 0.0,
            "throughput_kbps": 0.0,
            "total_chunks": 0,
            "start_time": time.time(),
            "history_time": [0.0],
            "history_acked": [0],
            "history_sent": [0]
        }

        # Worker thread with safe queue communication
        def _worker():
            try:
                def _client_callback(evt_type, data):
                    self.event_queue.put(("CLIENT_EVENT", (evt_type, data)))

                metrics = run_client(
                    server_ip=server_ip,
                    port=port,
                    filepath=self.selected_filepath,
                    sdn_ip=sdn_ip,
                    sdn_port=sdn_port,
                    on_event=_client_callback,
                    stop_event=self.client_stop_event
                )
            except Exception as e:
                err_text = str(e)
                self.event_queue.put(("CLIENT_ERROR", err_text))

        self.client_thread = threading.Thread(target=_worker, daemon=True)
        self.client_thread.start()

    def abort_client_transfer(self):
        self.client_stop_event.set()
        self.lbl_sender_status.config(text="Status: ABORTED", fg=THEME["red"])
        self.btn_send_start.config(state=tk.NORMAL)
        self.btn_send_abort.config(state=tk.DISABLED)
        self.log_sender("[!] Transfer aborted by user.", "TIMEOUT")

    # =========================================================================
    # RECEIVER (SERVER) VIEW
    # =========================================================================
    def _build_receiver_view(self):
        left_col = tk.Frame(self.content_frame, bg=THEME["bg"])
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        right_col = tk.Frame(self.content_frame, bg=THEME["bg"])
        right_col.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(8, 0))

        # --- LEFT: Config & Controls ---
        srv_cfg = ModernCard(left_col)
        srv_cfg.pack(fill=tk.X, pady=(0, 8))

        lbl_s_hdr = tk.Label(srv_cfg, text="⚙️ Receiver Server Configuration", font=("DejaVu Sans", 10, "bold"),
                             fg=THEME["green"], bg=THEME["card_bg"])
        lbl_s_hdr.pack(anchor=tk.W, pady=(0, 6))

        r_row = tk.Frame(srv_cfg, bg=THEME["card_bg"])
        r_row.pack(fill=tk.X, pady=2)

        tk.Label(r_row, text="Bind IP:", font=("DejaVu Sans", 9), fg=THEME["text_dim"], bg=THEME["card_bg"]).pack(side=tk.LEFT)
        self.ent_recv_ip = tk.Entry(r_row, font=("DejaVu Sans", 9), width=12, bg=THEME["entry_bg"],
                                    fg=THEME["text"], insertbackground=THEME["text"], relief=tk.FLAT)
        self.ent_recv_ip.insert(0, "0.0.0.0")
        self.ent_recv_ip.pack(side=tk.LEFT, padx=(4, 12))

        tk.Label(r_row, text="Port:", font=("DejaVu Sans", 9), fg=THEME["text_dim"], bg=THEME["card_bg"]).pack(side=tk.LEFT)
        self.ent_recv_port = tk.Entry(r_row, font=("DejaVu Sans", 9), width=7, bg=THEME["entry_bg"],
                                      fg=THEME["text"], insertbackground=THEME["text"], relief=tk.FLAT)
        self.ent_recv_port.insert(0, str(DEFAULT_SERVER_PORT))
        self.ent_recv_port.pack(side=tk.LEFT, padx=(4, 12))

        tk.Label(r_row, text="Save To:", font=("DejaVu Sans", 9), fg=THEME["text_dim"], bg=THEME["card_bg"]).pack(side=tk.LEFT)
        self.ent_recv_dir = tk.Entry(r_row, font=("DejaVu Sans", 9), width=14, bg=THEME["entry_bg"],
                                     fg=THEME["text"], insertbackground=THEME["text"], relief=tk.FLAT)
        self.ent_recv_dir.insert(0, "received")
        self.ent_recv_dir.pack(side=tk.LEFT, padx=(4, 0))

        # Control buttons
        btn_bar = tk.Frame(srv_cfg, bg=THEME["card_bg"])
        btn_bar.pack(fill=tk.X, pady=(10, 2))

        self.btn_recv_toggle = tk.Button(btn_bar, text="▶ Start Server", font=("DejaVu Sans", 10, "bold"),
                                         bg=THEME["green"], fg="#11111b", relief=tk.FLAT, padx=14, pady=6,
                                         cursor="hand2", command=self.toggle_server)
        self.btn_recv_toggle.pack(side=tk.LEFT, padx=(0, 8))

        btn_open_folder = tk.Button(btn_bar, text="📂 Open Received Folder", font=("DejaVu Sans", 9),
                                    bg=THEME["entry_bg"], fg=THEME["text"], relief=tk.GROOVE, padx=10, pady=6,
                                    cursor="hand2", command=self.open_received_folder)
        btn_open_folder.pack(side=tk.LEFT)

        self.lbl_server_status = tk.Label(btn_bar, text="Status: STOPPED", font=("DejaVu Sans", 9, "bold"),
                                          fg=THEME["red"], bg=THEME["card_bg"])
        self.lbl_server_status.pack(side=tk.RIGHT, padx=6)

        # --- LEFT: Live Reception Progress ---
        rx_prog_card = ModernCard(left_col)
        rx_prog_card.pack(fill=tk.X, pady=(0, 8))

        tk.Label(rx_prog_card, text="📥 Active Transfer Status & Progress", font=("DejaVu Sans", 10, "bold"),
                 fg=THEME["green"], bg=THEME["card_bg"]).pack(anchor=tk.W, pady=(0, 6))

        self.lbl_rx_info = tk.Label(rx_prog_card, text="Waiting for client SYN connection...",
                                    font=("DejaVu Sans", 9), fg=THEME["text_dim"], bg=THEME["card_bg"], anchor=tk.W)
        self.lbl_rx_info.pack(fill=tk.X, pady=(0, 4))

        rx_p_row = tk.Frame(rx_prog_card, bg=THEME["card_bg"])
        rx_p_row.pack(fill=tk.X, pady=2)

        self.recv_prog = ttk.Progressbar(rx_p_row, style="Green.Horizontal.TProgressbar",
                                         orient=tk.HORIZONTAL, mode="determinate")
        self.recv_prog.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        self.lbl_recv_pct = tk.Label(rx_p_row, text="0.0%", font=("DejaVu Sans", 11, "bold"),
                                     fg=THEME["green"], bg=THEME["card_bg"], width=6)
        self.lbl_recv_pct.pack(side=tk.RIGHT)

        self.lbl_recv_chunks = tk.Label(rx_prog_card, text="Chunks Received: 0 / 0", font=("DejaVu Sans", 8),
                                        fg=THEME["text_dim"], bg=THEME["card_bg"], anchor=tk.W)
        self.lbl_recv_chunks.pack(fill=tk.X, pady=(2, 0))

        # --- LEFT: Receiver Stats Grid ---
        rx_stats_card = ModernCard(left_col)
        rx_stats_card.pack(fill=tk.X, pady=(0, 8))

        tk.Label(rx_stats_card, text="📊 Packet Reception & In-Order Telemetry", font=("DejaVu Sans", 10, "bold"),
                 fg=THEME["green"], bg=THEME["card_bg"]).pack(anchor=tk.W, pady=(0, 6))

        rx_grid = tk.Frame(rx_stats_card, bg=THEME["card_bg"])
        rx_grid.pack(fill=tk.X)

        self.stat_r_cumack = self._create_stat_badge(rx_grid, "Cumulative ACK", "0", 0, 0, color=THEME["green"])
        self.stat_r_dups = self._create_stat_badge(rx_grid, "Duplicates", "0", 0, 1, color=THEME["orange"])
        self.stat_r_buffered = self._create_stat_badge(rx_grid, "Buffered (Gaps)", "0", 0, 2, color=THEME["yellow"])
        self.stat_r_integrity = self._create_stat_badge(rx_grid, "SHA-256 Status", "PENDING", 1, 0, color=THEME["text_dim"])
        self.stat_r_file = self._create_stat_badge(rx_grid, "Current File", "-", 1, 1)
        self.stat_r_client = self._create_stat_badge(rx_grid, "Client Endpoint", "-", 1, 2)

        # --- LEFT: Server Event Log ---
        rx_log_card = ModernCard(left_col)
        rx_log_card.pack(fill=tk.BOTH, expand=True)

        tk.Label(rx_log_card, text="📜 Server Event Stream", font=("DejaVu Sans", 9, "bold"),
                 fg=THEME["text_dim"], bg=THEME["card_bg"]).pack(anchor=tk.W, pady=(0, 4))

        self.txt_recv_log = tk.Text(rx_log_card, bg=THEME["console_bg"], fg=THEME["text"],
                                    insertbackground=THEME["text"], font=("Monospace", 8),
                                    relief=tk.FLAT, height=10)
        self.txt_recv_log.pack(fill=tk.BOTH, expand=True)

        self.txt_recv_log.tag_config("SYN", foreground=THEME["accent"])
        self.txt_recv_log.tag_config("ACK", foreground=THEME["green"])
        self.txt_recv_log.tag_config("DATA", foreground=THEME["text"])
        self.txt_recv_log.tag_config("DUP", foreground=THEME["orange"])
        self.txt_recv_log.tag_config("GAP", foreground=THEME["yellow"])
        self.txt_recv_log.tag_config("FIN", foreground="#b4befe")

        # --- RIGHT: Receiver Matplotlib Telemetry Graphs ---
        rx_graph_card = ModernCard(right_col)
        rx_graph_card.pack(fill=tk.BOTH, expand=True)

        tk.Label(rx_graph_card, text="📈 Packet Arrival & In-Order Delivery Graphs", font=("DejaVu Sans", 10, "bold"),
                 fg=THEME["green"], bg=THEME["card_bg"]).pack(anchor=tk.W, pady=(0, 4))

        self.fig_server = Figure(figsize=(5.5, 6.2), dpi=100, facecolor=THEME["card_bg"])
        self.ax_s_progress = self.fig_server.add_subplot(211)
        self.ax_s_dist = self.fig_server.add_subplot(212)
        self.fig_server.tight_layout(pad=2.8)

        self._init_server_graphs()

        self.canvas_server = FigureCanvasTkAgg(self.fig_server, master=rx_graph_card)
        self.canvas_server.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def _init_server_graphs(self):
        for ax in (self.ax_s_progress, self.ax_s_dist):
            ax.set_facecolor(THEME["entry_bg"])
            ax.tick_params(colors=THEME["text_dim"], labelsize=8)
            for spine in ax.spines.values():
                spine.set_color(THEME["border"])

        self.ax_s_progress.set_title("Chunks Received & In-Order Delivery Progression", color=THEME["text"], fontsize=9, pad=6)
        self.ax_s_progress.set_xlabel("Elapsed Time (s)", color=THEME["text_dim"], fontsize=8)
        self.ax_s_progress.set_ylabel("Sequence Number", color=THEME["text_dim"], fontsize=8)

        self.ax_s_dist.set_title("Packet Classification (In-Order vs Duplicate vs Buffered)", color=THEME["text"], fontsize=9, pad=6)
        self.ax_s_dist.set_ylabel("Count", color=THEME["text_dim"], fontsize=8)

    def log_receiver(self, message: str, tag: str = "DATA"):
        if hasattr(self, "txt_recv_log"):
            self.txt_recv_log.insert(tk.END, message + "\n", tag)
            self.txt_recv_log.see(tk.END)

    def toggle_server(self):
        if self.server_running:
            self.stop_server_thread()
        else:
            self.start_server_thread()

    def start_server_thread(self):
        if self.server_running:
            return

        ip = self.ent_recv_ip.get().strip() if hasattr(self, "ent_recv_ip") else "0.0.0.0"
        try:
            port = int(self.ent_recv_port.get().strip()) if hasattr(self, "ent_recv_port") else DEFAULT_SERVER_PORT
        except ValueError:
            messagebox.showerror("Invalid Port", "Port number must be an integer.")
            return

        save_dir = self.ent_recv_dir.get().strip() if hasattr(self, "ent_recv_dir") else "received"
        self.server_stop_event.clear()

        def _worker():
            try:
                def _server_callback(evt_type, data):
                    self.event_queue.put(("SERVER_EVENT", (evt_type, data)))

                run_server(ip=ip, port=port, save_dir=save_dir,
                           on_event=_server_callback, stop_event=self.server_stop_event)
            except Exception as e:
                err_text = str(e)
                self.event_queue.put(("SERVER_ERROR", err_text))

        self.server_thread = threading.Thread(target=_worker, daemon=True)
        self.server_thread.start()
        self.server_running = True
        if hasattr(self, "btn_recv_toggle"):
            self.btn_recv_toggle.config(text="⏹ Stop Server", bg=THEME["red"])
            self.lbl_server_status.config(text=f"Status: LISTENING ON {ip}:{port}", fg=THEME["green"])
        self.log_receiver(f"[*] Server started listening on UDP {ip}:{port}...", "SYN")

    def stop_server_thread(self):
        if not self.server_running:
            return
        self.server_stop_event.set()
        self.server_running = False
        if hasattr(self, "btn_recv_toggle"):
            self.btn_recv_toggle.config(text="▶ Start Server", bg=THEME["green"])
            self.lbl_server_status.config(text="Status: STOPPED", fg=THEME["red"])
        self.log_receiver("[*] Server stopped.", "FIN")

    def open_received_folder(self):
        save_dir = self.ent_recv_dir.get().strip() if hasattr(self, "ent_recv_dir") else "received"
        os.makedirs(save_dir, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(save_dir)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", save_dir])
            else:
                subprocess.Popen(["xdg-open", save_dir])
        except Exception as e:
            messagebox.showerror("Could Not Open Folder", str(e))

    # =========================================================================
    # THREAD-SAFE EVENT QUEUE DISPATCHER
    # =========================================================================
    def _poll_event_queue(self):
        """Processes worker thread events cleanly on Tkinter main thread."""
        try:
            if not self.winfo_exists():
                return
            while not self.event_queue.empty():
                kind, payload = self.event_queue.get_nowait()
                if kind == "CLIENT_EVENT":
                    evt, data = payload
                    self._handle_client_event(evt, data)
                elif kind == "CLIENT_ERROR":
                    self._handle_client_error(payload)
                elif kind == "SERVER_EVENT":
                    evt, data = payload
                    self._handle_server_event(evt, data)
                elif kind == "SERVER_ERROR":
                    self.log_receiver(f"[!] Server error: {payload}", "DUP")
        except queue.Empty:
            pass
        except Exception as e:
            pass

        # Reschedule check every 30ms if window is still open
        try:
            if self.winfo_exists():
                self._poll_timer = self.after(30, self._poll_event_queue)
        except Exception:
            pass

    def _handle_client_error(self, err_msg: str):
        self.lbl_sender_status.config(text="Status: FAILED", fg=THEME["red"])
        self.btn_send_start.config(state=tk.NORMAL)
        self.btn_send_abort.config(state=tk.DISABLED)
        self.log_sender(f"[ERROR] {err_msg}", "TIMEOUT")

        # Friendly explanation for common connection timeout
        hint = ""
        if "after 10 attempts" in err_msg or "timed out" in err_msg:
            hint = "\n\n💡 Tip: Is the Receiver Server running on the destination IP?\n" \
                   "If testing locally on this laptop, make sure Server IP is set to 127.0.0.1!"

        messagebox.showerror("Transfer Failed", f"{err_msg}{hint}")

    def _handle_client_event(self, evt: str, data: dict):
        now = time.time()
        elapsed = max(now - self.client_stats["start_time"], 0.001)

        if evt == "START":
            self.client_stats["total_chunks"] = data.get("total_packets", 1)
            self.lbl_sender_status.config(text="Status: TRANSMITTING...", fg=THEME["accent"])
            self.log_sender(f"[SYN] Initiating transfer: {data.get('filename')} ({data.get('total_packets')} chunks)", "SYN")

        elif evt == "SYN_ACK":
            self.log_sender(f"[ACK] Server SYN-ACK received from {data.get('server')}. Handshake complete!", "ACK")

        elif evt == "DATA_SENT":
            seq = data.get("seq", 0)
            self.client_stats["total_sent"] += 1
            self.stat_c_sent.config(text=str(self.client_stats["total_sent"]))
            self.log_sender(f"[DATA] Sent Seq {seq}", "DATA")

            self.client_stats["history_time"].append(elapsed)
            self.client_stats["history_sent"].append(seq)
            self.client_stats["history_acked"].append(self.client_stats["acked"])
            self._refresh_client_graphs_throttled()

        elif evt == "ACK_RECEIVED":
            ack = data.get("ack", 0)
            total = data.get("total_packets", max(self.client_stats["total_chunks"], 1))
            self.client_stats["acked"] = ack
            self.stat_c_acked.config(text=str(ack))

            pct = min((ack / total) * 100.0, 100.0)
            self.sender_prog["value"] = pct
            self.lbl_sender_pct.config(text=f"{pct:.1f}%")
            self.lbl_sender_chunks.config(text=f"Packets Transferred: {ack} / {total}")

            total_transmissions = self.client_stats["total_sent"] + self.client_stats["retrans"]
            loss_pct = (self.client_stats["retrans"] / total_transmissions * 100.0) if total_transmissions > 0 else 0.0
            self.client_stats["loss_pct"] = loss_pct
            self.stat_c_loss.config(text=f"{loss_pct:.2f}%")

            speed = (ack * CHUNK_SIZE * 8 / 1024) / elapsed
            self.stat_c_speed.config(text=f"{speed:.1f} kbps")

            self.log_sender(f"[ACK] Received Cumulative ACK {ack} (Window slides)", "ACK")
            self._refresh_client_graphs_throttled()

        elif evt == "TIMEOUT":
            self.client_stats["timeouts"] = data.get("timeouts", 0)
            self.client_stats["retrans"] = data.get("retransmissions", 0)
            self.stat_c_retrans.config(text=str(self.client_stats["retrans"]))

            total_transmissions = self.client_stats["total_sent"] + self.client_stats["retrans"]
            loss_pct = (self.client_stats["retrans"] / total_transmissions * 100.0) if total_transmissions > 0 else 0.0
            self.client_stats["loss_pct"] = loss_pct
            self.stat_c_loss.config(text=f"{loss_pct:.2f}%")

            self.log_sender(f"[TIMEOUT] Retransmitting window from Seq {data.get('base')} ({data.get('unacked')} pkts)", "TIMEOUT")
            self._refresh_client_graphs_throttled()

        elif evt == "SDN_ALERT":
            alerts = int(self.stat_c_alerts.cget("text")) + 1
            self.stat_c_alerts.config(text=str(alerts))
            self.log_sender(f"[SDN ALERT] Dispatched path problem alert to SDN Controller ({data.get('sdn_ip')}:{data.get('sdn_port')})", "SDN")

        elif evt == "FINISH":
            metrics = data.get("metrics", {})
            self.btn_send_start.config(state=tk.NORMAL)
            self.btn_send_abort.config(state=tk.DISABLED)

            self._draw_client_graphs_immediate()

            if metrics.get("integrity_ok"):
                self.lbl_sender_status.config(text="Status: VERIFIED (PASS)", fg=THEME["green"])
                self.log_sender(f"[FIN] Transfer complete! SHA-256 match verified across all chunks.", "FIN")
                messagebox.showinfo("Transfer Success",
                                    f"File transferred successfully!\n\n"
                                    f"Duration: {metrics.get('duration_sec')}s\n"
                                    f"Throughput: {metrics.get('throughput_kbps')} kbps\n"
                                    f"Retransmissions: {metrics.get('retransmissions')}\n"
                                    f"Loss Percentage: {self.client_stats['loss_pct']:.2f}%\n"
                                    f"Integrity: SHA-256 Match Verified")
            else:
                self.lbl_sender_status.config(text="Status: FINISHED (FAIL)", fg=THEME["red"])
                self.log_sender("[FIN] Transfer finished but file integrity failed!", "TIMEOUT")

    def _refresh_client_graphs_throttled(self):
        now = time.time()
        if now - self.last_client_graph_update > 0.12:
            self.last_client_graph_update = now
            self._draw_client_graphs_immediate()

    def _draw_client_graphs_immediate(self):
        if not hasattr(self, "ax_c_progress"):
            return
        try:
            self.ax_c_progress.clear()
            self.ax_c_loss.clear()
            self._init_client_graphs()

            t = self.client_stats["history_time"]
            acked = self.client_stats["history_acked"]
            sent = self.client_stats["history_sent"]

            # Downsample if history has thousands of points for ultra-smooth rendering
            step = max(len(t) // 250, 1)
            t_plot = t[::step]
            acked_plot = acked[::step]
            sent_plot = sent[::step]

            if len(t_plot) > 1:
                self.ax_c_progress.plot(t_plot, sent_plot, color=THEME["accent"], label="Sent Chunks", lw=1.8)
                self.ax_c_progress.plot(t_plot, acked_plot, color=THEME["green"], label="ACKed Chunks", lw=2)
                self.ax_c_progress.legend(loc="upper left", facecolor=THEME["card_bg"], edgecolor=THEME["border"],
                                          labelcolor=THEME["text"], fontsize=8)

            categories = ["ACKed (Success)", "Retransmitted", "Timeouts"]
            values = [self.client_stats["acked"], self.client_stats["retrans"], self.client_stats["timeouts"]]
            colors = [THEME["green"], THEME["orange"], THEME["red"]]

            bars = self.ax_c_loss.bar(categories, values, color=colors, width=0.45)
            self.ax_c_loss.bar_label(bars, color=THEME["text"], fontsize=8, padding=2)

            self.canvas_client.draw_idle()
        except Exception:
            pass

    def _handle_server_event(self, evt: str, data: dict):
        if evt == "SYN_RECEIVED":
            self.server_stats["total_chunks"] = data.get("total_packets", 1)
            self.server_stats["in_order"] = 0
            self.server_stats["duplicates"] = 0
            self.server_stats["buffered"] = 0
            self.server_stats["history_time"] = [0.0]
            self.server_stats["history_chunks"] = [0]
            self.server_stats["start_time"] = time.time()

            self.recv_prog["value"] = 0
            self.lbl_recv_pct.config(text="0.0%")
            self.lbl_rx_info.config(text=f"Receiving '{data.get('filename')}' ({data.get('total_packets')} chunks) from {data.get('client')}")
            self.stat_r_file.config(text=data.get("filename", "-"))
            self.stat_r_client.config(text=data.get("client", "-"))
            self.stat_r_integrity.config(text="IN PROGRESS", fg=THEME["yellow"])

            self.log_receiver(f"[SYN] Handshake from {data.get('client')}: File '{data.get('filename')}' ({data.get('total_packets')} chunks)", "SYN")

        elif evt == "DATA_RECEIVED":
            seq = data.get("seq", 0)
            cum_ack = data.get("cum_ack", 0)
            total = data.get("total_packets", max(self.server_stats["total_chunks"], 1))

            self.server_stats["in_order"] = cum_ack
            self.server_stats["buffered"] = data.get("buffered_count", 0)
            self.stat_r_cumack.config(text=str(cum_ack))
            self.stat_r_buffered.config(text=str(self.server_stats["buffered"]))

            pct = min((cum_ack / total) * 100.0, 100.0)
            self.recv_prog["value"] = pct
            self.lbl_recv_pct.config(text=f"{pct:.1f}%")
            self.lbl_recv_chunks.config(text=f"Chunks Received: {cum_ack} / {total}")

            elapsed = max(time.time() - self.server_stats.get("start_time", time.time()), 0.001)
            self.server_stats["history_time"].append(elapsed)
            self.server_stats["history_chunks"].append(cum_ack)

            self.log_receiver(f"[DATA] Received Seq {seq} -> Cumulative ACK {cum_ack}", "DATA")
            self._refresh_server_graphs_throttled()

        elif evt == "DUPLICATE_RECEIVED":
            seq = data.get("seq", 0)
            self.server_stats["duplicates"] = data.get("duplicates", 0)
            self.stat_r_dups.config(text=str(self.server_stats["duplicates"]))
            self.log_receiver(f"[DUP] Duplicate packet Seq {seq} received -> Resent ACK {data.get('cum_ack')}", "DUP")
            self._refresh_server_graphs_throttled()

        elif evt == "GAP_DETECTED":
            self.server_stats["buffered"] = data.get("buffered_count", 0)
            self.stat_r_buffered.config(text=str(self.server_stats["buffered"]))
            self.log_receiver(f"[GAP] Out-of-order Seq {data.get('got')} arrived (Waiting for {data.get('expected')}) -> Buffered", "GAP")
            self._refresh_server_graphs_throttled()

        elif evt == "FIN_COMPLETED":
            ok = data.get("integrity_ok", False)
            self._draw_server_graphs_immediate()
            if ok:
                self.stat_r_integrity.config(text="PASS (SHA-256)", fg=THEME["green"])
                self.log_receiver(f"[FIN] Transfer complete! SHA-256 VERIFIED for {data.get('filename')}", "FIN")
            else:
                self.stat_r_integrity.config(text="FAIL (MISMATCH)", fg=THEME["red"])
                self.log_receiver(f"[FIN] Transfer complete but SHA-256 hash failed!", "DUP")

    def _refresh_server_graphs_throttled(self):
        now = time.time()
        if now - self.last_server_graph_update > 0.12:
            self.last_server_graph_update = now
            self._draw_server_graphs_immediate()

    def _draw_server_graphs_immediate(self):
        if not hasattr(self, "ax_s_progress"):
            return
        try:
            self.ax_s_progress.clear()
            self.ax_s_dist.clear()
            self._init_server_graphs()

            t = self.server_stats.get("history_time", [])
            chunks = self.server_stats.get("history_chunks", [])

            step = max(len(t) // 250, 1)
            t_plot = t[::step]
            chunks_plot = chunks[::step]

            if len(t_plot) > 1:
                self.ax_s_progress.plot(t_plot, chunks_plot, color=THEME["green"], lw=2, label="In-Order Delivery")
                self.ax_s_progress.legend(loc="upper left", facecolor=THEME["card_bg"], edgecolor=THEME["border"],
                                          labelcolor=THEME["text"], fontsize=8)

            categories = ["In-Order Delivered", "Duplicates", "Out-of-Order Gaps"]
            values = [self.server_stats.get("in_order", 0),
                      self.server_stats.get("duplicates", 0),
                      self.server_stats.get("buffered", 0)]
            colors = [THEME["green"], THEME["orange"], THEME["yellow"]]

            bars = self.ax_s_dist.bar(categories, values, color=colors, width=0.45)
            self.ax_s_dist.bar_label(bars, color=THEME["text"], fontsize=8, padding=2)

            self.canvas_server.draw_idle()
        except Exception:
            pass

    def destroy(self):
        """Cancel scheduled timers before destroying widget to prevent Tcl error messages."""
        if hasattr(self, "_poll_timer") and self._poll_timer:
            try:
                self.after_cancel(self._poll_timer)
            except Exception:
                pass
            self._poll_timer = None
        super().destroy()

    def on_window_close(self):
        """Clean shutdown of running threads on window close."""
        self.client_stop_event.set()
        self.stop_server_thread()
        self.destroy()


def main():
    initial_mode = sys.argv[1].lower() if len(sys.argv) > 1 else None
    app = AppGUI(initial_mode=initial_mode)
    app.mainloop()


if __name__ == "__main__":
    main()
