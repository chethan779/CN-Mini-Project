"""
sdn_controller.py - Software Defined Network (SDN) Controller
=============================================================
Manages OpenFlow rules on switches (s1, s2, s3, s4) via ovs-ofctl.
Monitors switch port statistics, link states (ip link / tc qdisc),
and listens on UDP port 9999 for socket-level timeout alerts from clients.
Dynamically reroutes traffic from Primary (s1->s2->s4) to Backup (s1->s3->s4).
"""

import socket
import json
import subprocess
import shutil
import time
import threading
import os
import sys
import argparse

class SDNController:
    def __init__(self, mode: str = "dynamic", listen_port: int = 9999, interval: float = 0.25, on_event = None):
        """
        mode: 'dynamic' (auto-reroute on failure) or 'static' (baseline, never reroute)
        listen_port: UDP port to receive alerts from UDP client (Socket-SDN integration)
        interval: polling interval for monitoring thread
        on_event: optional callback(event_type, data) for GUI integration
        """
        self.mode = mode.lower()
        self.listen_port = listen_port
        self.interval = interval
        self.current_path = "PRIMARY"
        self.running = False
        self.monitor_thread = None
        self.alert_socket = None
        self.reroute_count = 0
        self.has_ovs = shutil.which("ovs-ofctl") is not None
        self.on_event = on_event
        self.active_clients: dict[str, dict] = {}
        self.active_sessions: dict[str, dict] = {}

    def _notify(self, evt_type: str, data: dict):
        if self.on_event:
            try:
                self.on_event(evt_type, data)
            except Exception:
                pass
        
    def _run_cmd(self, cmd: list[str]) -> tuple[int, str, str]:
        """Helper to run a shell command safely."""
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2)
            return res.returncode, res.stdout.strip(), res.stderr.strip()
        except Exception as e:
            return -1, "", str(e)
            
    def _ovs(self, switch: str, action: str, *args) -> bool:
        """Executes ovs-ofctl OpenFlow 1.3 rule manipulation."""
        if not self.has_ovs:
            return True  # Mock success when testing outside Mininet
        cmd = ["ovs-ofctl", "-O", "OpenFlow13", action, switch] + list(args)
        code, out, err = self._run_cmd(cmd)
        if code != 0 and err:
            # Silently tolerate missing switch if running outside mininet topology
            pass
        return code == 0

    def install_primary_path(self):
        """
        Installs baseline OpenFlow flows for the Primary Path:
          h1, h2 <---> s1 <===(Primary)===> s2 <===(Primary)===> s4 <---> h3
        """
        print("[SDN] Installing OpenFlow rules for PRIMARY PATH (s1 -> s2 -> s4)...")
        self.current_path = "PRIMARY"
        
        # 1. Switch s1:
        # Clear existing flows
        self._ovs("s1", "del-flows")
        # ARP flood
        self._ovs("s1", "add-flow", "priority=50,arp,actions=FLOOD")
        # Forward traffic to h3 (10.0.0.3) -> Port 3 (Link to s2)
        self._ovs("s1", "add-flow", "priority=100,ip,nw_dst=10.0.0.3,actions=output:3")
        # Return traffic to h1 (10.0.0.1) -> Port 1
        self._ovs("s1", "add-flow", "priority=100,ip,nw_dst=10.0.0.1,actions=output:1")
        # Return traffic to h2 (10.0.0.2) -> Port 2
        self._ovs("s1", "add-flow", "priority=100,ip,nw_dst=10.0.0.2,actions=output:2")

        # 2. Switch s2 (Primary intermediate switch):
        self._ovs("s2", "del-flows")
        self._ovs("s2", "add-flow", "priority=50,arp,actions=FLOOD")
        # Port 1 (s1) <-> Port 2 (s4)
        self._ovs("s2", "add-flow", "priority=100,in_port=1,actions=output:2")
        self._ovs("s2", "add-flow", "priority=100,in_port=2,actions=output:1")

        # 3. Switch s3 (Backup intermediate switch, pre-configured):
        self._ovs("s3", "del-flows")
        self._ovs("s3", "add-flow", "priority=50,arp,actions=FLOOD")
        # Port 1 (s1) <-> Port 2 (s4)
        self._ovs("s3", "add-flow", "priority=100,in_port=1,actions=output:2")
        self._ovs("s3", "add-flow", "priority=100,in_port=2,actions=output:1")

        # 4. Switch s4:
        self._ovs("s4", "del-flows")
        self._ovs("s4", "add-flow", "priority=50,arp,actions=FLOOD")
        # Forward IP to h3 (10.0.0.3) -> Port 1
        self._ovs("s4", "add-flow", "priority=100,ip,nw_dst=10.0.0.3,actions=output:1")
        # Return traffic from h3 -> Port 2 (to s2, Primary Path)
        self._ovs("s4", "add-flow", "priority=100,in_port=1,ip,actions=output:2")
        
        print("[SDN] Primary path flows successfully configured.")
        self._notify("PATH_CHANGED", {"path": "PRIMARY", "mode": self.mode, "reroutes": self.reroute_count})

    def install_backup_path(self, reason: str = "Link failure / Packet loss alert"):
        """
        Reroutes traffic over the Backup Path:
          h1, h2 <---> s1 <===(Backup)===> s3 <===(Backup)===> s4 <---> h3
        In Dynamic mode: installs priority=200 override rules on s1 and s4.
        In Static mode: ignores reroute to preserve static baseline behavior.
        """
        print(f"\n[SDN PATH PROBLEM DETECTED] Reason: {reason}")
        
        if self.mode == "static":
            print(f"[SDN STATIC MODE] Controller is operating in STATIC mode. REROUTE DISABLED.")
            return False
            
        if self.current_path == "BACKUP":
            # Already rerouted
            return True
            
        print("[SDN REROUTE] Rerouting traffic from Primary (s1->s2->s4) to Backup Path (s1->s3->s4)")
        
        # Override rules with priority=200 on switch s1 (Port 4 is connected to s3)
        self._ovs("s1", "add-flow", "priority=200,ip,nw_dst=10.0.0.3,actions=output:4")
        self._ovs("s1", "add-flow", "priority=200,in_port=4,ip,nw_dst=10.0.0.1,actions=output:1")
        self._ovs("s1", "add-flow", "priority=200,in_port=4,ip,nw_dst=10.0.0.2,actions=output:2")

        # Override rules with priority=200 on switch s4 (Port 3 is connected to s3)
        self._ovs("s4", "add-flow", "priority=200,in_port=1,ip,actions=output:3")
        self._ovs("s4", "add-flow", "priority=200,in_port=3,ip,nw_dst=10.0.0.3,actions=output:1")

        self.current_path = "BACKUP"
        self.reroute_count += 1
        print(f"[SDN REROUTE COMPLETE] Active path switched to BACKUP (Total reroutes: {self.reroute_count})\n")
        self._notify("PATH_CHANGED", {"path": "BACKUP", "reason": reason, "mode": self.mode, "reroutes": self.reroute_count})
        return True

    def check_link_and_switch_health(self) -> tuple[bool, str]:
        """
        Inspects SDN switch ports and interfaces via ip link, tc qdisc, and ovs-ofctl.
        Returns (is_problem, reason).
        """
        # 1. Check if interface s1-eth3 (primary link to s2) is DOWN or NO-CARRIER
        code, out, _ = self._run_cmd(["ip", "link", "show", "s1-eth3"])
        if code == 0:
            if "state DOWN" in out or "NO-CARRIER" in out:
                return True, "Interface s1-eth3 is DOWN (Link Failure Detected)"
                
        # 2. Check if interface s2-eth1 is DOWN
        code, out, _ = self._run_cmd(["ip", "link", "show", "s2-eth1"])
        if code == 0:
            if "state DOWN" in out or "NO-CARRIER" in out:
                return True, "Interface s2-eth1 is DOWN (Link Failure Detected)"

        # 3. Check for severe packet loss configured on s1-eth3 via tc qdisc (netem loss)
        code, out, _ = self._run_cmd(["tc", "qdisc", "show", "dev", "s1-eth3"])
        if code == 0 and "loss" in out:
            return True, f"High packet loss detected on s1-eth3: {out}"

        # 4. Check OpenFlow port drop counters on s1
        if self.has_ovs:
            code, out, _ = self._run_cmd(["ovs-ofctl", "-O", "OpenFlow13", "dump-ports", "s1", "3"])
            if code == 0 and "drop=" in out:
                for line in out.splitlines():
                    if "drop=" in line:
                        try:
                            # e.g., tx dropped=5
                            parts = line.split("drop=")
                            drops = int(parts[1].split()[0].replace(',', ''))
                            if drops > 10:
                                return True, f"Excessive port drops ({drops}) on switch s1 port 3"
                        except Exception:
                            pass
                            
        return False, ""

    def _monitoring_loop(self):
        """Background thread monitoring both the UDP socket and network link states."""
        self.alert_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.alert_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.alert_socket.settimeout(self.interval)
        
        try:
            self.alert_socket.bind(("0.0.0.0", self.listen_port))
            print(f"[SDN] Controller listening for client alerts on UDP port {self.listen_port}...")
        except Exception as e:
            print(f"[!] Warning: Could not bind alert socket to port {self.listen_port}: {e}")

        while self.running:
            # 1. Listen for socket-level messages from UDP Client (Control Plane / Alerts)
            try:
                data, addr = self.alert_socket.recvfrom(2048)
                msg = json.loads(data.decode('utf-8'))
                evt = str(msg.get("event", "UNKNOWN")).upper()
                client_host = addr[0]
                client_endpoint = f"{addr[0]}:{addr[1]}"

                # Update active clients table
                self.active_clients[client_endpoint] = {
                    "ip": client_host,
                    "port": addr[1],
                    "last_seen": time.time(),
                    "status": "ONLINE"
                }

                if evt in ("PING", "HELLO", "CONNECT"):
                    # Connection probe / handshake
                    reply = {
                        "status": "OK",
                        "event": "PONG",
                        "controller": "ONLINE",
                        "active_path": self.current_path,
                        "mode": self.mode,
                        "reroutes": self.reroute_count,
                        "timestamp": time.time()
                    }
                    self.alert_socket.sendto(json.dumps(reply).encode('utf-8'), addr)
                    print(f"[SDN] Handshake verified with client at {client_endpoint} (Path: {self.current_path})")
                    self._notify("CLIENT_CONNECTED", {
                        "addr": client_endpoint,
                        "client_ip": client_host,
                        "path": self.current_path,
                        "mode": self.mode,
                        "active_clients": len(self.active_clients)
                    })

                elif evt == "SESSION_START":
                    # File transfer initiated by client
                    filename = msg.get("filename", "unknown")
                    filesize = msg.get("filesize", 0)
                    total_packets = msg.get("total_packets", 0)
                    server_target = f"{msg.get('server_ip', '127.0.0.1')}:{msg.get('server_port', 5005)}"

                    self.active_sessions[client_endpoint] = {
                        "filename": filename,
                        "filesize": filesize,
                        "total_packets": total_packets,
                        "server": server_target,
                        "start_time": time.time()
                    }

                    reply = {
                        "status": "OK",
                        "event": "SESSION_ACK",
                        "active_path": self.current_path,
                        "mode": self.mode
                    }
                    self.alert_socket.sendto(json.dumps(reply).encode('utf-8'), addr)
                    print(f"[SDN SESSION START] Client {client_endpoint} -> Server {server_target} | File: '{filename}' ({filesize} B, {total_packets} chunks) via {self.current_path}")
                    self._notify("SESSION_START", {
                        "addr": client_endpoint,
                        "filename": filename,
                        "filesize": filesize,
                        "total_packets": total_packets,
                        "server": server_target,
                        "path": self.current_path,
                        "active_sessions": len(self.active_sessions)
                    })

                elif evt in ("PATH_PROBLEM", "TIMEOUT", "TIMEOUT_ALERT") or ("consecutive_timeouts" in msg and msg.get("consecutive_timeouts", 0) >= 2):
                    # Packet loss / link degradation alert
                    seq = msg.get("seq", "?")
                    consecutive = msg.get("consecutive_timeouts", 2)
                    reason = f"Socket alert from {client_host}: {evt} (Seq {seq}, {consecutive} consecutive timeouts)"
                    self._notify("ALERT_RECEIVED", {"alert": msg, "addr": client_endpoint, "reason": reason})
                    rerouted = self.install_backup_path(reason)

                    reply = {
                        "status": "REROUTED" if rerouted else "NO_CHANGE",
                        "event": "REROUTE_ACK",
                        "active_path": self.current_path,
                        "reroutes": self.reroute_count,
                        "message": f"Active path is {self.current_path}"
                    }
                    try:
                        self.alert_socket.sendto(json.dumps(reply).encode('utf-8'), addr)
                    except Exception:
                        pass

                elif evt == "SESSION_END":
                    # File transfer completed
                    filename = msg.get("filename", "unknown")
                    status = msg.get("status", "COMPLETED")
                    if client_endpoint in self.active_sessions:
                        del self.active_sessions[client_endpoint]

                    reply = {"status": "OK", "event": "SESSION_END_ACK"}
                    try:
                        self.alert_socket.sendto(json.dumps(reply).encode('utf-8'), addr)
                    except Exception:
                        pass
                    print(f"[SDN SESSION COMPLETE] Transfer '{filename}' finished ({status}) for {client_endpoint}")
                    self._notify("SESSION_END", {
                        "addr": client_endpoint,
                        "filename": filename,
                        "status": status,
                        "active_sessions": len(self.active_sessions)
                    })

                else:
                    # Generic / legacy alert fallback
                    if "consecutive_timeouts" in msg:
                        reason = f"Socket alert from {client_host}: {evt}"
                        self._notify("ALERT_RECEIVED", {"alert": msg, "addr": client_endpoint, "reason": reason})
                        self.install_backup_path(reason)

            except socket.timeout:
                pass
            except Exception as e:
                pass

            # 2. Check SDN switch and link health directly
            problem, reason = self.check_link_and_switch_health()
            if problem and self.current_path == "PRIMARY":
                self.install_backup_path(reason)

            time.sleep(self.interval)

    def start(self):
        """Starts the SDN controller monitoring in a background thread."""
        self.running = True
        self.install_primary_path()
        self.monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.monitor_thread.start()
        print(f"[*] SDN Controller running in {self.mode.upper()} mode.")

    def stop(self):
        """Stops the SDN controller."""
        self.running = False
        if self.alert_socket:
            try:
                self.alert_socket.close()
            except Exception:
                pass
        print("[*] SDN Controller stopped.")

def main():
    parser = argparse.ArgumentParser(description="SDN OpenFlow Controller with Dynamic Rerouting")
    parser.add_argument("--mode", choices=["dynamic", "static"], default="dynamic",
                        help="Operating mode: 'dynamic' (auto-reroute) or 'static' (no reroute)")
    parser.add_argument("--port", type=int, default=9999, help="UDP port for client socket alerts (default: 9999)")
    parser.add_argument("--interval", type=float, default=0.25, help="Health check poll interval in seconds (default: 0.25)")
    args = parser.parse_args()

    controller = SDNController(mode=args.mode, listen_port=args.port, interval=args.interval)
    controller.start()

    print("\nPress Ctrl+C to stop the SDN controller.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping SDN Controller...")
        controller.stop()

if __name__ == "__main__":
    main()

