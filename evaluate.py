"""
evaluate.py - Performance Evaluation & SDN Baseline Comparison
==============================================================
Runs automated performance benchmarks across 4 scenarios:
  1. Normal Network (0% Loss)
  2. 10% Packet Loss (Static Baseline - No SDN Reroute)
  3. 10% Packet Loss (Dynamic SDN Reroute)
  4. Link Failure s1-s2 (Dynamic SDN Failover)

Collects metrics (Throughput, Transfer Duration, Retransmissions, Timeouts, Duplicates),
saves results to 'results/metrics.json', and generates a high-resolution 3-panel
comparison chart at 'results/comparison_graph.png'.
"""

import os
import sys
import time
import json
import random
import socket
import select
import threading
import argparse

import matplotlib
matplotlib.use('Agg')  # Headless backend for terminal/server execution
import matplotlib.pyplot as plt

from protocol import (
    TYPE_SYN, TYPE_DATA, TYPE_ACK, TYPE_FIN,
    parse_packet, make_packet, DEFAULT_SERVER_PORT, DEFAULT_SDN_PORT,
    get_file_sha256
)
from client import run_client
from server import run_server
from sdn_controller import SDNController

# Ensure sample files exist
def ensure_sample_files():
    os.makedirs("files", exist_ok=True)
    os.makedirs("received", exist_ok=True)
    os.makedirs("results", exist_ok=True)
    
    sample_bin = "files/sample.bin"
    if not os.path.exists(sample_bin) or os.path.getsize(sample_bin) < 100000:
        print("[*] Generating 100 KB sample test file: files/sample.bin...")
        with open(sample_bin, "wb") as f:
            for i in range(100):
                block = f"CHUNK_{i:04d}_".encode('ascii') + os.urandom(1024 - 11)
                f.write(block)
                
    sample_txt = "files/sample.txt"
    if not os.path.exists(sample_txt):
        with open(sample_txt, "w") as f:
            for i in range(200):
                f.write(f"Line {i:03d}: Computer Networks Mini-Project - Reliable UDP File Transfer.\n")

class SimulatedNetworkProxy:
    """
    Transparent UDP proxy running on localhost that simulates path impairments
    (loss, link failure) and reacts to SDN controller dynamic rerouting.
    """
    def __init__(self, listen_port=5004, target_port=5005, mode="normal", controller_port=9999):
        self.listen_port = listen_port
        self.target_port = target_port
        self.mode = mode  # 'normal', 'static_loss', 'dynamic_loss', 'dynamic_failover'
        self.controller_port = controller_port
        self.running = False
        self.sock = None
        self.thread = None
        self.client_addr = None
        self.rerouted = False
        self.drop_count = 0
        self.pkt_counter = 0

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", self.listen_port))
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def notify_sdn_reroute(self):
        """Called when SDN controller triggers a reroute to backup path."""
        if not self.rerouted:
            self.rerouted = True
            print("[PROXY -> SDN] Active path switched to BACKUP (0% Loss restored)!")

    def _should_drop(self, pkt_type, seq_num) -> bool:
        """Determines whether a packet should be dropped based on scenario."""
        # Never drop handshake SYN or teardown FIN so session flow remains clean
        if pkt_type in (TYPE_SYN, TYPE_FIN):
            return False

        if self.mode == "normal":
            return False

        elif self.mode == "static_loss":
            # Continuous 10% packet loss (drops 1 in every 10 packets)
            self.pkt_counter += 1
            if self.pkt_counter % 10 == 0:
                self.drop_count += 1
                return True
            return False

        elif self.mode == "dynamic_loss":
            if self.rerouted:
                # Backup path has 0% loss!
                return False
            else:
                self.pkt_counter += 1
                # Drop packets 5 and 6 to trigger consecutive timeouts -> client alerts SDN
                if seq_num in (5, 6) or (self.pkt_counter % 10 == 0):
                    self.drop_count += 1
                    return True
                return False

        elif self.mode == "dynamic_failover":
            if self.rerouted:
                # Switched to backup path: 0% loss
                return False
            else:
                # Primary link is severed after packet 12
                if seq_num >= 12:
                    self.drop_count += 1
                    return True
                return False

        return False

    def _run(self):
        while self.running:
            r, _, _ = select.select([self.sock], [], [], 0.05)
            if not r:
                continue
            try:
                data, addr = self.sock.recvfrom(4096)
                if addr[1] != self.target_port:
                    # Packet from client -> to server
                    self.client_addr = addr
                    try:
                        pkt_type, seq_num, ack_num, _ = parse_packet(data)
                    except Exception:
                        pkt_type, seq_num = TYPE_DATA, 0

                    if self._should_drop(pkt_type, seq_num):
                        print(f"      [SIMULATED PATH DROP] Dropped Data Packet Seq {seq_num}")
                        continue
                    
                    self.sock.sendto(data, ("127.0.0.1", self.target_port))
                else:
                    # Packet from server -> to client
                    if self.client_addr:
                        self.sock.sendto(data, self.client_addr)
            except Exception:
                pass

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

def run_single_test(scenario_name: str, mode: str, is_sdn_dynamic: bool, filepath: str) -> dict:
    """
    Executes a single test run and returns metrics.
    """
    print("\n" + "=" * 75)
    print(f"  RUNNING BENCHMARK: {scenario_name}")
    print(f"  Mode: {mode} | SDN Controller: {'DYNAMIC' if is_sdn_dynamic else 'STATIC'}")
    print("=" * 75)
    
    server_port = 5005
    proxy_port = 5004
    sdn_port = 9999
    
    # Start server in background thread
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    server_thread = threading.Thread(
        target=run_server,
        kwargs={"ip": "127.0.0.1", "port": server_port, "save_dir": "received"},
        daemon=True
    )
    server_thread.start()
    time.sleep(0.15)
    
    # Start SDN Controller
    controller_mode = "dynamic" if is_sdn_dynamic else "static"
    controller = SDNController(mode=controller_mode, listen_port=sdn_port, interval=0.1)
    controller.start()
    
    # Start Simulated Network Proxy
    proxy = SimulatedNetworkProxy(
        listen_port=proxy_port,
        target_port=server_port,
        mode=mode,
        controller_port=sdn_port
    )
    proxy.start()
    time.sleep(0.1)
    
    # Hook proxy to SDN controller reroute events
    original_install_backup = controller.install_backup_path
    def hooked_backup(reason=""):
        res = original_install_backup(reason)
        if res:
            proxy.notify_sdn_reroute()
        return res
    controller.install_backup_path = hooked_backup
    
    # Execute client transfer
    metrics = run_client(
        server_ip="127.0.0.1",
        port=proxy_port,
        filepath=filepath,
        sdn_ip="127.0.0.1",
        sdn_port=sdn_port,
        window_size=4,
        timeout=0.3
    )
    
    # Cleanup components
    proxy.stop()
    controller.stop()
    time.sleep(0.1)
    
    return metrics

def plot_results(metrics_by_scenario: dict, output_chart: str = "results/comparison_graph.png"):
    """
    Plots a 3-panel publication-grade comparison bar chart using matplotlib:
      1. Throughput (kbps)
      2. Transfer Duration (seconds)
      3. Retransmissions (count)
    """
    labels = [
        "1. Normal (0% Loss)",
        "2. Static Baseline (10% Loss)",
        "3. Dynamic SDN (10% Loss)",
        "4. Dynamic SDN (Link Fail)"
    ]
    
    keys = list(metrics_by_scenario.keys())
    throughputs = [metrics_by_scenario[k]["throughput_kbps"] for k in keys]
    durations = [metrics_by_scenario[k]["duration_sec"] for k in keys]
    retransmissions = [metrics_by_scenario[k]["retransmissions"] for k in keys]
    
    # Color palette
    colors = ['#27ae60', '#e74c3c', '#2980b9', '#8e44ad']
    
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.5))
    fig.suptitle("Reliable UDP with SDN Dynamic Rerouting - Performance Evaluation", fontsize=15, fontweight='bold', y=0.98)
    
    # --- Subplot 1: Throughput ---
    bars1 = axes[0].bar(labels, throughputs, color=colors, width=0.55, edgecolor='black', linewidth=1.2)
    axes[0].set_title("Throughput (kbps) [Higher is Better]", fontsize=12, fontweight='bold')
    axes[0].set_ylabel("Throughput (kbps)", fontsize=11)
    axes[0].grid(axis='y', linestyle='--', alpha=0.6)
    axes[0].set_xticks(range(len(labels)))
    axes[0].set_xticklabels(labels, rotation=15, ha='right', fontsize=9)
    for bar in bars1:
        yval = bar.get_height()
        axes[0].text(bar.get_x() + bar.get_width() / 2, yval + (max(throughputs) * 0.02),
                     f"{yval:.1f}", ha='center', va='bottom', fontsize=9.5, fontweight='bold')
    axes[0].set_ylim(0, max(throughputs) * 1.15)
    
    # --- Subplot 2: Duration ---
    bars2 = axes[1].bar(labels, durations, color=colors, width=0.55, edgecolor='black', linewidth=1.2)
    axes[1].set_title("Transfer Duration (seconds) [Lower is Better]", fontsize=12, fontweight='bold')
    axes[1].set_ylabel("Duration (seconds)", fontsize=11)
    axes[1].grid(axis='y', linestyle='--', alpha=0.6)
    axes[1].set_xticks(range(len(labels)))
    axes[1].set_xticklabels(labels, rotation=15, ha='right', fontsize=9)
    for bar in bars2:
        yval = bar.get_height()
        axes[1].text(bar.get_x() + bar.get_width() / 2, yval + (max(durations) * 0.02),
                     f"{yval:.2f}s", ha='center', va='bottom', fontsize=9.5, fontweight='bold')
    axes[1].set_ylim(0, max(durations) * 1.15)

    # --- Subplot 3: Retransmissions ---
    bars3 = axes[2].bar(labels, retransmissions, color=colors, width=0.55, edgecolor='black', linewidth=1.2)
    axes[2].set_title("Retransmissions (Packets) [Lower is Better]", fontsize=12, fontweight='bold')
    axes[2].set_ylabel("Retransmission Count", fontsize=11)
    axes[2].grid(axis='y', linestyle='--', alpha=0.6)
    axes[2].set_xticks(range(len(labels)))
    axes[2].set_xticklabels(labels, rotation=15, ha='right', fontsize=9)
    for bar in bars3:
        yval = bar.get_height()
        axes[2].text(bar.get_x() + bar.get_width() / 2, yval + (max(max(retransmissions), 1) * 0.02),
                     f"{int(yval)}", ha='center', va='bottom', fontsize=9.5, fontweight='bold')
    axes[2].set_ylim(0, max(max(retransmissions), 1) * 1.15 + 1)
    
    plt.subplots_adjust(top=0.88, bottom=0.22, wspace=0.28)
    plt.savefig(output_chart, dpi=300)
    plt.close()
    print(f"[+] Performance comparison graph saved to: {output_chart}")

def main():
    parser = argparse.ArgumentParser(description="Evaluate Reliable UDP File Transfer with SDN Rerouting")
    parser.add_argument("--file", default="files/sample.bin", help="File to use for evaluation (default: files/sample.bin)")
    args = parser.parse_args()
    
    ensure_sample_files()
    
    print("=" * 80)
    print("     STARTING COMPREHENSIVE PERFORMANCE EVALUATION (4 SCENARIOS)")
    print("=" * 80)
    
    results = {}
    
    # 1. Normal (0% Loss)
    results["scenario_1_normal"] = run_single_test(
        scenario_name="Scenario 1: Normal (0% Loss)",
        mode="normal",
        is_sdn_dynamic=True,
        filepath=args.file
    )
    
    # 2. 10% Packet Loss (Static Baseline - No Reroute)
    results["scenario_2_static_loss"] = run_single_test(
        scenario_name="Scenario 2: 10% Packet Loss (Static Baseline - No Reroute)",
        mode="static_loss",
        is_sdn_dynamic=False,
        filepath=args.file
    )
    
    # 3. 10% Packet Loss (Dynamic SDN Reroute)
    results["scenario_3_dynamic_loss"] = run_single_test(
        scenario_name="Scenario 3: 10% Packet Loss (Dynamic SDN Reroute)",
        mode="dynamic_loss",
        is_sdn_dynamic=True,
        filepath=args.file
    )
    
    # 4. Link Failure s1-s2 (Dynamic SDN Failover)
    results["scenario_4_dynamic_failover"] = run_single_test(
        scenario_name="Scenario 4: Link Failure s1-s2 (Dynamic SDN Failover)",
        mode="dynamic_failover",
        is_sdn_dynamic=True,
        filepath=args.file
    )
    
    # Save JSON metrics
    metrics_file = "results/metrics.json"
    with open(metrics_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n[+] Raw metrics saved to: {metrics_file}")
    
    # Generate Comparison Graph
    graph_file = "results/comparison_graph.png"
    plot_results(results, graph_file)
    
    # Print Tabular Summary to Terminal
    print("\n" + "=" * 90)
    print(f"{'Scenario Name':<38} | {'Throughput':<12} | {'Duration':<10} | {'Retrans':<8} | {'Integrity':<10}")
    print("-" * 90)
    name_map = {
        "scenario_1_normal": "1. Normal (0% Loss)",
        "scenario_2_static_loss": "2. 10% Loss (Static Baseline)",
        "scenario_3_dynamic_loss": "3. 10% Loss (Dynamic SDN)",
        "scenario_4_dynamic_failover": "4. Link Fail (Dynamic SDN)",
    }
    for k, v in results.items():
        disp_name = name_map.get(k, k)
        tput = f"{v['throughput_kbps']} kbps"
        dur = f"{v['duration_sec']}s"
        ret = f"{v['retransmissions']}"
        integ = "PASS (SHA)" if v['integrity_ok'] else "FAIL"
        print(f"{disp_name:<38} | {tput:<12} | {dur:<10} | {ret:<8} | {integ:<10}")
    print("=" * 90)
    print("[*] Benchmark evaluation completed successfully!\n")

if __name__ == "__main__":
    main()
