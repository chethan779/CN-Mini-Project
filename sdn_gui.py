"""
sdn_gui.py - Dedicated Graphical SDN Controller & Topology Visualizer
=====================================================================
A completely standalone Tkinter interface for the Software Defined Network (SDN)
Controller. Visualizes the 4-switch multipath network topology, highlights
the active path in real-time (Primary s1->s2->s4 vs Backup s1->s3->s4), animates
flow packets, logs client timeout alerts, and provides live telemetry graphs.

Uses thread-safe queue communication to prevent UI freezing and lambda closure bugs.
"""

import os
import sys
import time
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from sdn_controller import SDNController

THEME = {
    "bg": "#181825",
    "card_bg": "#1e1e2e",
    "border": "#313244",
    "text": "#cdd6f4",
    "text_dim": "#a6adc8",
    "accent": "#89b4fa",
    "green": "#a6e3a1",
    "red": "#f38ba8",
    "orange": "#fab387",
    "yellow": "#f9e2af",
    "canvas_bg": "#11111b"
}


class ModernCard(tk.Frame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=THEME["card_bg"], highlightbackground=THEME["border"],
                         highlightthickness=1, padx=12, pady=10, **kwargs)


class SDNGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SDN OpenFlow 1.3 Controller & Route Manager")
        self.geometry("1120x820")
        self.minsize(980, 720)
        self.configure(bg=THEME["bg"])

        self.event_queue: queue.Queue = queue.Queue()
        self._poll_timer = None
        self._anim_timer = None
        self.controller: Optional[SDNController] = None
        self.controller_thread: Optional[threading.Thread] = None
        self.running = False
        self.active_path = "PRIMARY"
        self.alerts_count = 0
        self.reroutes_count = 0
        self.start_time = time.time()

        # Telemetry history for graphs
        self.history_time = [0.0]
        self.history_path = [0]   # 0 = Primary, 1 = Backup
        self.history_alerts = [0]
        self.last_graph_update = 0.0

        # Topology animation state
        self.anim_step = 0

        self._build_header()
        self._build_main_layout()

        self.protocol("WM_DELETE_WINDOW", self.on_window_close)
        self._poll_event_queue()
        self._start_animation_loop()

        # Automatically start the SDN Controller on launch for plug-and-play operation
        self.after(200, self.start_controller)

    def _build_header(self):
        nav_bar = tk.Frame(self, bg=THEME["card_bg"], height=56, highlightbackground=THEME["border"],
                           highlightthickness=1)
        nav_bar.pack(fill=tk.X, side=tk.TOP, padx=16, pady=12)

        lbl_title = tk.Label(nav_bar, text="🌐 SDN Central OpenFlow 1.3 Controller",
                             font=("DejaVu Sans", 14, "bold"), fg=THEME["accent"], bg=THEME["card_bg"])
        lbl_title.pack(side=tk.LEFT, padx=16, pady=8)

        lbl_sub = tk.Label(nav_bar, text="|  Dynamic Multipath Failover & Topology Health",
                           font=("DejaVu Sans", 10), fg=THEME["text_dim"], bg=THEME["card_bg"])
        lbl_sub.pack(side=tk.LEFT, pady=8)

        self.lbl_head_status = tk.Label(nav_bar, text="CONTROLLER: STOPPED", font=("DejaVu Sans", 10, "bold"),
                                        fg=THEME["red"], bg=THEME["bg"], padx=10, pady=4,
                                        highlightbackground=THEME["border"], highlightthickness=1)
        self.lbl_head_status.pack(side=tk.RIGHT, padx=16, pady=8)

    def _build_main_layout(self):
        content = tk.Frame(self, bg=THEME["bg"])
        content.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 14))

        left_col = tk.Frame(content, bg=THEME["bg"])
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        right_col = tk.Frame(content, bg=THEME["bg"])
        right_col.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(8, 0))

        # --- LEFT 1: Controller Controls & Settings ---
        ctl_card = ModernCard(left_col)
        ctl_card.pack(fill=tk.X, pady=(0, 8))

        tk.Label(ctl_card, text="⚙️ Controller Configuration & Controls", font=("DejaVu Sans", 10, "bold"),
                 fg=THEME["accent"], bg=THEME["card_bg"]).pack(anchor=tk.W, pady=(0, 6))

        row_cfg = tk.Frame(ctl_card, bg=THEME["card_bg"])
        row_cfg.pack(fill=tk.X, pady=2)

        tk.Label(row_cfg, text="Alert Listen Port:", font=("DejaVu Sans", 9), fg=THEME["text_dim"], bg=THEME["card_bg"]).pack(side=tk.LEFT)
        self.ent_port = tk.Entry(row_cfg, font=("DejaVu Sans", 9), width=7, bg=THEME["canvas_bg"],
                                 fg=THEME["text"], insertbackground=THEME["text"], relief=tk.FLAT)
        self.ent_port.insert(0, "9999")
        self.ent_port.pack(side=tk.LEFT, padx=(4, 16))

        tk.Label(row_cfg, text="Mode:", font=("DejaVu Sans", 9), fg=THEME["text_dim"], bg=THEME["card_bg"]).pack(side=tk.LEFT)
        self.var_mode = tk.StringVar(value="dynamic")
        rb_dyn = tk.Radiobutton(row_cfg, text="Dynamic (Auto-Failover)", variable=self.var_mode, value="dynamic",
                                bg=THEME["card_bg"], fg=THEME["text"], selectcolor=THEME["canvas_bg"],
                                activebackground=THEME["card_bg"], font=("DejaVu Sans", 9))
        rb_dyn.pack(side=tk.LEFT, padx=4)

        rb_sta = tk.Radiobutton(row_cfg, text="Static (Baseline)", variable=self.var_mode, value="static",
                                bg=THEME["card_bg"], fg=THEME["text"], selectcolor=THEME["canvas_bg"],
                                activebackground=THEME["card_bg"], font=("DejaVu Sans", 9))
        rb_sta.pack(side=tk.LEFT, padx=4)

        # Action buttons row
        btn_row = tk.Frame(ctl_card, bg=THEME["card_bg"])
        btn_row.pack(fill=tk.X, pady=(8, 2))

        self.btn_toggle = tk.Button(btn_row, text="▶ Start Controller", font=("DejaVu Sans", 10, "bold"),
                                    bg=THEME["green"], fg="#11111b", relief=tk.FLAT, padx=14, pady=6,
                                    cursor="hand2", command=self.toggle_controller)
        self.btn_toggle.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_force_backup = tk.Button(btn_row, text="⚡ Simulate Link Failure (Force Backup)",
                                          font=("DejaVu Sans", 9, "bold"), bg=THEME["orange"], fg="#11111b",
                                          relief=tk.FLAT, padx=12, pady=6, cursor="hand2",
                                          command=self.manual_switch_backup)
        self.btn_force_backup.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_force_primary = tk.Button(btn_row, text="🔄 Restore Primary Path",
                                           font=("DejaVu Sans", 9, "bold"), bg=THEME["accent"], fg="#11111b",
                                           relief=tk.FLAT, padx=12, pady=6, cursor="hand2",
                                           command=self.manual_switch_primary)
        self.btn_force_primary.pack(side=tk.LEFT)

        # --- LEFT 2: Interactive Network Topology Map ---
        topo_card = ModernCard(left_col)
        topo_card.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        topo_head = tk.Frame(topo_card, bg=THEME["card_bg"])
        topo_head.pack(fill=tk.X, pady=(0, 4))

        tk.Label(topo_head, text="🗺️ Live OpenFlow Network Topology Map", font=("DejaVu Sans", 10, "bold"),
                 fg=THEME["accent"], bg=THEME["card_bg"]).pack(side=tk.LEFT)

        self.lbl_path_badge = tk.Label(topo_head, text="ACTIVE: PRIMARY PATH (s1 -> s2 -> s4)",
                                       font=("DejaVu Sans", 9, "bold"), fg=THEME["green"], bg=THEME["canvas_bg"],
                                       padx=8, pady=2, highlightbackground=THEME["border"], highlightthickness=1)
        self.lbl_path_badge.pack(side=tk.RIGHT)

        self.canvas_topo = tk.Canvas(topo_card, bg=THEME["canvas_bg"], highlightthickness=0, height=270)
        self.canvas_topo.pack(fill=tk.BOTH, expand=True)

        # --- LEFT 3: SDN Telemetry Cards ---
        stat_card = ModernCard(left_col)
        stat_card.pack(fill=tk.X)

        s_grid = tk.Frame(stat_card, bg=THEME["card_bg"])
        s_grid.pack(fill=tk.X)

        self.stat_path = self._create_stat_badge(s_grid, "Active Routing Path", "PRIMARY (s1->s2->s4)", 0, 0, color=THEME["green"])
        self.stat_reroutes = self._create_stat_badge(s_grid, "Dynamic Reroutes", "0", 0, 1, color=THEME["orange"])
        self.stat_alerts = self._create_stat_badge(s_grid, "Client Alerts (Port 9999)", "0", 0, 2, color=THEME["yellow"])
        self.stat_clients = self._create_stat_badge(s_grid, "Connected Clients", "0", 1, 0, color=THEME["accent"])
        self.stat_session = self._create_stat_badge(s_grid, "Active Transfer", "Idle", 1, 1, color=THEME["text"])
        self.stat_control = self._create_stat_badge(s_grid, "Control Plane", "UDP 9999", 1, 2, color=THEME["green"])

        # --- RIGHT: Telemetry Graphs & Alert Stream ---
        graph_card = ModernCard(right_col)
        graph_card.pack(fill=tk.X, pady=(0, 8))

        tk.Label(graph_card, text="📈 Dynamic Reroute & Alert Timeline", font=("DejaVu Sans", 10, "bold"),
                 fg=THEME["accent"], bg=THEME["card_bg"]).pack(anchor=tk.W, pady=(0, 4))

        self.fig_sdn = Figure(figsize=(5.2, 3.4), dpi=100, facecolor=THEME["card_bg"])
        self.ax_path = self.fig_sdn.add_subplot(211)
        self.ax_alerts = self.fig_sdn.add_subplot(212)
        self.fig_sdn.tight_layout(pad=2.2)

        self._init_graphs()
        self.canvas_graph = FigureCanvasTkAgg(self.fig_sdn, master=graph_card)
        self.canvas_graph.get_tk_widget().pack(fill=tk.X)

        # 2. Alerts & Log Stream
        log_card = ModernCard(right_col)
        log_card.pack(fill=tk.BOTH, expand=True)

        tk.Label(log_card, text="📜 Socket-SDN Closed-Loop Alert Log", font=("DejaVu Sans", 9, "bold"),
                 fg=THEME["text_dim"], bg=THEME["card_bg"]).pack(anchor=tk.W, pady=(0, 4))

        self.txt_log = tk.Text(log_card, bg=THEME["canvas_bg"], fg=THEME["text"],
                               insertbackground=THEME["text"], font=("Monospace", 8),
                               relief=tk.FLAT, height=12)
        self.txt_log.pack(fill=tk.BOTH, expand=True)

        self.txt_log.tag_config("INFO", foreground=THEME["accent"])
        self.txt_log.tag_config("ALERT", foreground=THEME["orange"])
        self.txt_log.tag_config("REROUTE", foreground=THEME["green"])
        self.txt_log.tag_config("WARN", foreground=THEME["red"])

    def _create_stat_badge(self, parent, label: str, value: str, row: int, col: int, color: str = THEME["text"]):
        frame = tk.Frame(parent, bg=THEME["canvas_bg"], highlightbackground=THEME["border"],
                         highlightthickness=1, padx=8, pady=6)
        frame.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")
        parent.grid_columnconfigure(col, weight=1)

        tk.Label(frame, text=label, font=("DejaVu Sans", 8), fg=THEME["text_dim"], bg=THEME["canvas_bg"]).pack(anchor=tk.W)
        lbl_val = tk.Label(frame, text=value, font=("DejaVu Sans", 10, "bold"), fg=color, bg=THEME["canvas_bg"])
        lbl_val.pack(anchor=tk.W, pady=(2, 0))
        return lbl_val

    def _init_graphs(self):
        for ax in (self.ax_path, self.ax_alerts):
            ax.set_facecolor(THEME["canvas_bg"])
            ax.tick_params(colors=THEME["text_dim"], labelsize=8)
            for spine in ax.spines.values():
                spine.set_color(THEME["border"])

        self.ax_path.set_title("Active Path State (0=Primary, 1=Backup)", color=THEME["text"], fontsize=8, pad=4)
        self.ax_path.set_yticks([0, 1])
        self.ax_path.set_yticklabels(["Primary", "Backup"])

        self.ax_alerts.set_title("Cumulative Client Alerts Received", color=THEME["text"], fontsize=8, pad=4)
        self.ax_alerts.set_xlabel("Elapsed Time (s)", color=THEME["text_dim"], fontsize=8)

    def log(self, message: str, tag: str = "INFO"):
        if hasattr(self, "txt_log"):
            timestamp = time.strftime("%H:%M:%S")
            self.txt_log.insert(tk.END, f"[{timestamp}] {message}\n", tag)
            self.txt_log.see(tk.END)

    def toggle_controller(self):
        if self.running:
            self.stop_controller()
        else:
            self.start_controller()

    def start_controller(self):
        if self.running:
            return

        try:
            port = int(self.ent_port.get().strip())
        except ValueError:
            messagebox.showerror("Invalid Port", "Port number must be a valid integer.")
            return

        mode = self.var_mode.get()

        def _callback(evt, data):
            self.event_queue.put(("SDN_EVENT", (evt, data)))

        self.controller = SDNController(mode=mode, listen_port=port, on_event=_callback)

        def _worker():
            try:
                self.controller.start()
            except Exception as e:
                err_text = str(e)
                self.event_queue.put(("SDN_ERROR", err_text))

        self.controller_thread = threading.Thread(target=_worker, daemon=True)
        self.controller_thread.start()

        self.running = True
        self.active_path = "PRIMARY"
        self.btn_toggle.config(text="⏹ Stop Controller", bg=THEME["red"])
        self.lbl_head_status.config(text=f"CONTROLLER: ACTIVE (PORT {port})", fg=THEME["green"])
        self.lbl_path_badge.config(text="ACTIVE: PRIMARY PATH (s1 -> s2 -> s4)", fg=THEME["green"])
        self.stat_path.config(text="PRIMARY (s1->s2->s4)", fg=THEME["green"])
        self.log(f"SDN Controller initialized in {mode.upper()} mode on UDP port {port}.", "INFO")
        self.draw_topology()

    def stop_controller(self):
        if not self.running:
            return
        if self.controller:
            self.controller.stop()
        self.running = False
        self.btn_toggle.config(text="▶ Start Controller", bg=THEME["green"])
        self.lbl_head_status.config(text="CONTROLLER: STOPPED", fg=THEME["red"])
        self.log("SDN Controller stopped.", "WARN")
        self.draw_topology()

    def manual_switch_backup(self):
        if not self.running or not self.controller:
            messagebox.showwarning("Controller Inactive", "Please start the SDN Controller first.")
            return
        self.controller.install_backup_path("Manual Link Severance Injection via GUI")
        self.active_path = "BACKUP"
        self.reroutes_count += 1
        self.stat_reroutes.config(text=str(self.reroutes_count))
        self.lbl_path_badge.config(text="ACTIVE: BACKUP PATH (s1 -> s3 -> s4)", fg=THEME["orange"])
        self.stat_path.config(text="BACKUP (s1->s3->s4)", fg=THEME["orange"])
        self.log("[FAILOVER] Manually triggered link severance! Switched to BACKUP PATH.", "REROUTE")
        self._record_telemetry(path_val=1)
        self.draw_topology()

    def manual_switch_primary(self):
        if not self.running or not self.controller:
            messagebox.showwarning("Controller Inactive", "Please start the SDN Controller first.")
            return
        self.controller.install_primary_path()
        self.active_path = "PRIMARY"
        self.lbl_path_badge.config(text="ACTIVE: PRIMARY PATH (s1 -> s2 -> s4)", fg=THEME["green"])
        self.stat_path.config(text="PRIMARY (s1->s2->s4)", fg=THEME["green"])
        self.log("[RESTORE] Restored flow rules to PRIMARY PATH (s1 -> s2 -> s4).", "INFO")
        self._record_telemetry(path_val=0)
        self.draw_topology()

    # =========================================================================
    # THREAD-SAFE QUEUE POLLING
    # =========================================================================
    def _poll_event_queue(self):
        try:
            if not self.winfo_exists():
                return
            while not self.event_queue.empty():
                kind, payload = self.event_queue.get_nowait()
                if kind == "SDN_EVENT":
                    evt, data = payload
                    self._handle_sdn_event(evt, data)
                elif kind == "SDN_ERROR":
                    self.log(f"Controller failure: {payload}", "WARN")
        except queue.Empty:
            pass
        except Exception:
            pass

        try:
            if self.winfo_exists():
                self._poll_timer = self.after(30, self._poll_event_queue)
        except Exception:
            pass

    def _handle_sdn_event(self, evt: str, data: dict):
        if evt == "CLIENT_CONNECTED":
            client_ip = data.get("client_ip", "Client")
            active_c = data.get("active_clients", 1)
            self.stat_clients.config(text=f"{active_c} ({client_ip})")
            self.log(f"[CLIENT CONNECTED] Handshake confirmed from {data.get('addr')} (Path: {data.get('path')})", "INFO")

        elif evt == "SESSION_START":
            filename = data.get("filename", "-")
            total_pkts = data.get("total_packets", 0)
            self.stat_session.config(text=f"{filename} ({total_pkts} pkts)")
            self.log(f"[SESSION START] {data.get('addr')} -> File: '{filename}' ({data.get('filesize')} B) via {data.get('path')} PATH", "REROUTE")

        elif evt == "SESSION_END":
            filename = data.get("filename", "-")
            status = data.get("status", "COMPLETED")
            self.stat_session.config(text="Idle")
            self.log(f"[SESSION COMPLETE] '{filename}' finished with status: {status}", "INFO")

        elif evt == "ALERT_RECEIVED":
            self.alerts_count += 1
            self.stat_alerts.config(text=str(self.alerts_count))
            reason = data.get("reason", "Degraded link")
            self.log(f"[ALERT FROM {data.get('addr')}] {reason}", "ALERT")
            self._record_telemetry(path_val=(1 if self.active_path == "BACKUP" else 0))

        elif evt == "PATH_CHANGED":
            new_path = data.get("path", "PRIMARY")
            self.active_path = new_path
            self.reroutes_count = data.get("reroutes", self.reroutes_count)
            self.stat_reroutes.config(text=str(self.reroutes_count))

            if new_path == "BACKUP":
                self.lbl_path_badge.config(text="ACTIVE: BACKUP PATH (s1 -> s3 -> s4)", fg=THEME["orange"])
                self.stat_path.config(text="BACKUP (s1->s3->s4)", fg=THEME["orange"])
                self.log(f"[DYNAMIC REROUTE] Path switched to BACKUP (Reason: {data.get('reason')})", "REROUTE")
                self._record_telemetry(path_val=1)
            else:
                self.lbl_path_badge.config(text="ACTIVE: PRIMARY PATH (s1 -> s2 -> s4)", fg=THEME["green"])
                self.stat_path.config(text="PRIMARY (s1->s2->s4)", fg=THEME["green"])
                self._record_telemetry(path_val=0)

            self.draw_topology()

    def _record_telemetry(self, path_val: int):
        elapsed = max(time.time() - self.start_time, 0.001)
        self.history_time.append(elapsed)
        self.history_path.append(path_val)
        self.history_alerts.append(self.alerts_count)
        self._refresh_graphs()

    def _refresh_graphs(self):
        try:
            self.ax_path.clear()
            self.ax_alerts.clear()
            self._init_graphs()

            t = self.history_time
            p = self.history_path
            a = self.history_alerts

            if len(t) > 1:
                self.ax_path.step(t, p, color=THEME["orange"] if p[-1] == 1 else THEME["green"], where="post", lw=2)
                self.ax_alerts.plot(t, a, color=THEME["yellow"], lw=1.8)

            self.canvas_graph.draw_idle()
        except Exception:
            pass

    # =========================================================================
    # TOPOLOGY VISUALIZER & ANIMATION
    # =========================================================================
    def draw_topology(self):
        self.canvas_topo.delete("all")
        node_coords = {
            "h1": (50, 80),
            "h2": (50, 190),
            "s1": (160, 135),
            "s2": (280, 60),
            "s3": (280, 210),
            "s4": (400, 135),
            "h3": (500, 135)
        }

        is_backup = (self.active_path == "BACKUP")

        # Ingress links
        self._draw_link(node_coords["h1"], node_coords["s1"], THEME["border"], width=2)
        self._draw_link(node_coords["h2"], node_coords["s1"], THEME["border"], width=2)

        # Primary Path links
        color_pri = THEME["green"] if not is_backup else "#45475a"
        dash_pri = () if not is_backup else (4, 4)
        width_pri = 4 if not is_backup else 2
        self._draw_link(node_coords["s1"], node_coords["s2"], color_pri, width=width_pri, dash=dash_pri)
        self._draw_link(node_coords["s2"], node_coords["s4"], color_pri, width=width_pri, dash=dash_pri)

        # Backup Path links
        color_bk = THEME["orange"] if is_backup else "#45475a"
        dash_bk = () if is_backup else (4, 4)
        width_bk = 4 if is_backup else 2
        self._draw_link(node_coords["s1"], node_coords["s3"], color_bk, width=width_bk, dash=dash_bk)
        self._draw_link(node_coords["s3"], node_coords["s4"], color_bk, width=width_bk, dash=dash_bk)

        # Egress link
        self._draw_link(node_coords["s4"], node_coords["h3"], THEME["border"], width=2)

        self.canvas_topo.create_text(280, 32, text="PRIMARY PATH (s1 -> s2 -> s4)",
                                     fill=THEME["green"] if not is_backup else THEME["text_dim"],
                                     font=("DejaVu Sans", 8, "bold"))
        self.canvas_topo.create_text(280, 240, text="BACKUP PATH (s1 -> s3 -> s4)",
                                     fill=THEME["orange"] if is_backup else THEME["text_dim"],
                                     font=("DejaVu Sans", 8, "bold"))

        # Draw Nodes
        for name, (x, y) in node_coords.items():
            if name.startswith("h"):
                self.canvas_topo.create_rectangle(x - 22, y - 16, x + 22, y + 16,
                                                  fill=THEME["card_bg"], outline=THEME["accent"], width=2)
                role = "Sender" if name == "h1" else ("Server" if name == "h3" else "Host")
                self.canvas_topo.create_text(x, y - 4, text=name, fill=THEME["text"], font=("DejaVu Sans", 9, "bold"))
                self.canvas_topo.create_text(x, y + 7, text=role, fill=THEME["text_dim"], font=("DejaVu Sans", 6))
            else:
                active_color = THEME["green"] if not is_backup else THEME["orange"]
                if name == "s2" and is_backup:
                    border_color = THEME["red"]
                elif name == "s3" and not is_backup:
                    border_color = THEME["border"]
                else:
                    border_color = active_color

                self.canvas_topo.create_oval(x - 20, y - 20, x + 20, y + 20,
                                             fill=THEME["card_bg"], outline=border_color, width=2.5)
                self.canvas_topo.create_text(x, y, text=name, fill=THEME["text"], font=("DejaVu Sans", 10, "bold"))

        # Animated packet pulse along active path
        if self.running:
            p_step = (self.anim_step % 20) / 20.0
            if not is_backup:
                if p_step < 0.5:
                    sub = p_step / 0.5
                    px = node_coords["s1"][0] + sub * (node_coords["s2"][0] - node_coords["s1"][0])
                    py = node_coords["s1"][1] + sub * (node_coords["s2"][1] - node_coords["s1"][1])
                else:
                    sub = (p_step - 0.5) / 0.5
                    px = node_coords["s2"][0] + sub * (node_coords["s4"][0] - node_coords["s2"][0])
                    py = node_coords["s2"][1] + sub * (node_coords["s4"][1] - node_coords["s2"][1])
                self.canvas_topo.create_oval(px - 5, py - 5, px + 5, py + 5, fill=THEME["green"], outline="#ffffff", width=1)
            else:
                if p_step < 0.5:
                    sub = p_step / 0.5
                    px = node_coords["s1"][0] + sub * (node_coords["s3"][0] - node_coords["s1"][0])
                    py = node_coords["s1"][1] + sub * (node_coords["s3"][1] - node_coords["s1"][1])
                else:
                    sub = (p_step - 0.5) / 0.5
                    px = node_coords["s3"][0] + sub * (node_coords["s4"][0] - node_coords["s3"][0])
                    py = node_coords["s3"][1] + sub * (node_coords["s4"][1] - node_coords["s3"][1])
                self.canvas_topo.create_oval(px - 5, py - 5, px + 5, py + 5, fill=THEME["orange"], outline="#ffffff", width=1)

    def _draw_link(self, p1, p2, color, width=2, dash=()):
        self.canvas_topo.create_line(p1[0], p1[1], p2[0], p2[1], fill=color, width=width, dash=dash)

    def _start_animation_loop(self):
        def _loop():
            self.anim_step += 1
            if self.winfo_exists():
                self.draw_topology()
                self._anim_timer = self.after(100, _loop)
        self._anim_timer = self.after(200, _loop)

    def destroy(self):
        """Cancel scheduled timers before destroying widget to prevent Tcl error messages."""
        if hasattr(self, "_poll_timer") and self._poll_timer:
            try:
                self.after_cancel(self._poll_timer)
            except Exception:
                pass
            self._poll_timer = None
        if hasattr(self, "_anim_timer") and self._anim_timer:
            try:
                self.after_cancel(self._anim_timer)
            except Exception:
                pass
            self._anim_timer = None
        super().destroy()

    def on_window_close(self):
        self.stop_controller()
        self.destroy()


def main():
    app = SDNGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
