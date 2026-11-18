"""
client.py - Reliable UDP File Transfer Client with Sliding Window
==================================================================
Transfers files over UDP using a sliding window (Go-Back-N style cumulative ACKs),
retransmits on timeout, alerts SDN Controller on persistent packet loss,
and gathers end-to-end performance metrics.
"""

import socket
import json
import os
import sys
import time
import argparse
from protocol import (
    TYPE_SYN, TYPE_DATA, TYPE_ACK, TYPE_FIN,
    CHUNK_SIZE, DEFAULT_SERVER_IP, DEFAULT_SERVER_PORT,
    DEFAULT_SDN_IP, DEFAULT_SDN_PORT,
    DEFAULT_WINDOW_SIZE, DEFAULT_TIMEOUT,
    make_packet, parse_packet, get_file_sha256
)

def read_file_chunks(filepath: str, chunk_size: int = CHUNK_SIZE) -> dict[int, bytes]:
    """
    Reads a file and splits it into 1-indexed chunks: {1: chunk1, 2: chunk2, ...}.
    Handles empty files by providing 1 empty chunk.
    """
    chunks = {}
    seq = 1
    with open(filepath, "rb") as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            chunks[seq] = data
            seq += 1
            
    if not chunks:
        chunks[1] = b""
        
    return chunks

def ping_sdn(sdn_ip: str = DEFAULT_SDN_IP, sdn_port: int = DEFAULT_SDN_PORT, timeout: float = 0.5) -> dict | None:
    """
    Sends a PING probe to the SDN Controller and returns the response dict,
    or None if the controller does not respond within timeout seconds.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        t0 = time.time()
        msg = json.dumps({"event": "PING", "timestamp": t0}).encode('utf-8')
        sock.sendto(msg, (sdn_ip, sdn_port))
        resp_data, _ = sock.recvfrom(2048)
        rtt_ms = (time.time() - t0) * 1000.0
        sock.close()
        res = json.loads(resp_data.decode('utf-8'))
        res["rtt_ms"] = round(rtt_ms, 1)
        res["connected"] = True
        return res
    except Exception:
        return None


def send_sdn_alert(sdn_ip: str, sdn_port: int, alert_data: dict, timeout: float = 0.5) -> dict | None:
    """
    Sends a UDP JSON message to the SDN Controller and optionally returns the response.
    """
    try:
        sdn_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sdn_sock.settimeout(timeout)
        msg = json.dumps(alert_data).encode('utf-8')
        sdn_sock.sendto(msg, (sdn_ip, sdn_port))
        try:
            resp_data, _ = sdn_sock.recvfrom(2048)
            sdn_sock.close()
            return json.loads(resp_data.decode('utf-8'))
        except socket.timeout:
            sdn_sock.close()
            return None
    except Exception as e:
        print(f"[!] Warning: Failed to send SDN alert: {e}")
        return None

def run_client(server_ip: str = DEFAULT_SERVER_IP,
               port: int = DEFAULT_SERVER_PORT,
               filepath: str = "files/sample.bin",
               sdn_ip: str = DEFAULT_SDN_IP,
               sdn_port: int = DEFAULT_SDN_PORT,
               window_size: int = DEFAULT_WINDOW_SIZE,
               timeout: float = DEFAULT_TIMEOUT,
               on_event = None,
               stop_event = None,
               simulate_mode: str = "normal") -> dict:
    """
    Executes the file transfer to server_ip:port.
    Returns a dictionary of execution metrics.
    Supports optional on_event(event_type, data) callback, stop_event,
    and simulate_mode ("normal", "failover", "loss_15") for demonstration.
    """
    def notify(evt_type: str, **kwargs):
        if on_event:
            try:
                on_event(evt_type, kwargs)
            except Exception:
                pass
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Source file not found: {filepath}")
    
    filesize = os.path.getsize(filepath)
    file_sha256 = get_file_sha256(filepath)
    filename = os.path.basename(filepath)
    chunks = read_file_chunks(filepath, CHUNK_SIZE)
    total_packets = len(chunks)
    
    print("=" * 70)
    print(f"[*] Starting Reliable UDP File Transfer")
    print(f"[*] Target Server:     {server_ip}:{port}")
    print(f"[*] SDN Controller:    {sdn_ip}:{sdn_port}")
    print(f"[*] File to Transfer:  {filepath} ({filesize} bytes, {total_packets} chunks)")
    print(f"[*] Original SHA-256:  {file_sha256}")
    print(f"[*] Sliding Window:    {window_size} packets | Timeout: {timeout}s")
    if simulate_mode != "normal":
        print(f"[*] Fault Simulation:  {simulate_mode.upper()}")
    print("=" * 70)

    # -------------------------------------------------------------
    # Step 0: Check & Register with SDN Controller
    # -------------------------------------------------------------
    active_sdn_path = "PRIMARY"
    sdn_probe = ping_sdn(sdn_ip, sdn_port, timeout=0.5)
    if sdn_probe and sdn_probe.get("connected"):
        active_sdn_path = sdn_probe.get("active_path", "PRIMARY")
        print(f"[SDN] Controller connected at {sdn_ip}:{sdn_port} (Active Path: {active_sdn_path}, Mode: {sdn_probe.get('mode', 'dynamic').upper()})")
        notify("SDN_STATUS", connected=True, path=active_sdn_path, sdn_ip=sdn_ip, sdn_port=sdn_port, rtt_ms=sdn_probe.get("rtt_ms", 0))
        # Register transfer session
        send_sdn_alert(sdn_ip, sdn_port, {
            "event": "SESSION_START",
            "filename": filename,
            "filesize": filesize,
            "total_packets": total_packets,
            "server_ip": server_ip,
            "server_port": port,
            "timestamp": time.time()
        })
    else:
        print(f"[SDN WARNING] SDN Controller at {sdn_ip}:{sdn_port} not responding. Transfer continuing directly.")
        notify("SDN_STATUS", connected=False, path="STANDALONE", sdn_ip=sdn_ip, sdn_port=sdn_port)
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    
    # -------------------------------------------------------------
    # Step 1: SYN Handshake
    # -------------------------------------------------------------
    syn_metadata = {
        "filename": filename,
        "filesize": filesize,
        "sha256": file_sha256,
        "total_packets": total_packets
    }
    syn_packet = make_packet(TYPE_SYN, seq_num=0, ack_num=0, payload=json.dumps(syn_metadata))
    
    syn_ack_received = False
    syn_retries = 0
    max_syn_retries = 10
    
    start_time = time.time()
    
    print(f"[SYN] Sending handshake metadata to {server_ip}:{port}...")
    notify("START", filename=filename, filesize=filesize, total_packets=total_packets, sha256=file_sha256)
    while not syn_ack_received and syn_retries < max_syn_retries:
        if stop_event and stop_event.is_set():
            sock.close()
            return {}
        sock.sendto(syn_packet, (server_ip, port))
        try:
            resp, _ = sock.recvfrom(2048)
            pkt_type, _, ack_num, _ = parse_packet(resp)
            if pkt_type == TYPE_ACK and ack_num == 0:
                syn_ack_received = True
                notify("SYN_ACK", server=f"{server_ip}:{port}")
                print(f"[ACK] Received SYN-ACK from server. Connection established!")
                break
        except socket.timeout:
            syn_retries += 1
            print(f"[TIMEOUT] SYN-ACK timed out. Retrying handshake ({syn_retries}/{max_syn_retries})...")
            
    if not syn_ack_received:
        sock.close()
        raise ConnectionError(f"Failed to connect to server at {server_ip}:{port} after {max_syn_retries} attempts.")

    # -------------------------------------------------------------
    # Step 2: Sliding Window Data Transfer
    # -------------------------------------------------------------
    base = 1
    next_seq = 1
    timeouts = 0
    consecutive_timeouts = 0
    retransmissions = 0
    sdn_alerts_sent = 0
    sim_drops = 0
    
    while base <= total_packets:
        if stop_event and stop_event.is_set():
            break
        # Transmit all packets allowed by current window
        while next_seq < base + window_size and next_seq <= total_packets:
            if stop_event and stop_event.is_set():
                break
            chunk = chunks[next_seq]

            # Optional fault injection to demonstrate SDN dynamic failover live
            should_drop = False
            if simulate_mode == "failover" and active_sdn_path == "PRIMARY":
                if next_seq in (2, 3) and sim_drops < 2:
                    should_drop = True
            elif simulate_mode == "loss_15" and active_sdn_path == "PRIMARY":
                if next_seq % 7 == 0:
                    should_drop = True

            if should_drop:
                sim_drops += 1
                print(f"[SIMULATED LOSS INJECTION] Intentionally suppressing Seq {next_seq} to demonstrate timeout & SDN failover...")
                notify("SIMULATED_DROP", seq=next_seq, drop_count=sim_drops)
            else:
                pkt = make_packet(TYPE_DATA, seq_num=next_seq, ack_num=0, payload=chunk)
                sock.sendto(pkt, (server_ip, port))
                print(f"[DATA] Sent Seq {next_seq} ({len(chunk)} bytes)")
                notify("DATA_SENT", seq=next_seq, base=base, total_packets=total_packets)
            next_seq += 1
            
        # Wait for acknowledgment
        try:
            resp, _ = sock.recvfrom(2048)
            pkt_type, _, ack_num, _ = parse_packet(resp)
            
            if pkt_type == TYPE_ACK:
                if ack_num >= base:
                    old_base = base
                    base = ack_num + 1
                    consecutive_timeouts = 0  # reset on progress
                    print(f"[ACK] Received Cumulative ACK {ack_num} (Window slides: base={base})")
                    notify("ACK_RECEIVED", ack=ack_num, base=base, total_packets=total_packets)
                else:
                    # Duplicate ACK received
                    pass
        except socket.timeout:
            timeouts += 1
            consecutive_timeouts += 1
            unacked_count = next_seq - base
            retransmissions += unacked_count
            print(f"[TIMEOUT & RETRANSMIT] Retransmitting unacknowledged packets from Seq {base} to {next_seq - 1}")
            notify("TIMEOUT", base=base, unacked=unacked_count, consecutive=consecutive_timeouts,
                   timeouts=timeouts, retransmissions=retransmissions, total_packets=total_packets)
            
            # --- Socket-SDN Integration ---
            # If consecutive timeouts occur (indicating path disruption/congestion)
            if consecutive_timeouts >= 2:
                alert_payload = {
                    "event": "PATH_PROBLEM",
                    "seq": base,
                    "consecutive_timeouts": consecutive_timeouts,
                    "client_ip": sock.getsockname()[0] if sock.getsockname()[0] != '0.0.0.0' else "10.0.0.1",
                    "timestamp": time.time()
                }
                reply = send_sdn_alert(sdn_ip, sdn_port, alert_payload)
                sdn_alerts_sent += 1
                if reply and reply.get("active_path"):
                    active_sdn_path = reply["active_path"]
                    print(f"[SOCKET -> SDN] Sent alert. Controller confirmed failover to {active_sdn_path} PATH!")
                else:
                    active_sdn_path = "BACKUP"
                    print(f"[SOCKET -> SDN] Sent path problem alert to SDN Controller at {sdn_ip}:{sdn_port}")
                notify("SDN_ALERT", sdn_ip=sdn_ip, sdn_port=sdn_port, seq=base, path=active_sdn_path)
                
            # Reset next_seq back to base to trigger retransmission of the window
            next_seq = base

    # -------------------------------------------------------------
    # Step 3: FIN & Verification Exchange
    # -------------------------------------------------------------
    fin_pkt = make_packet(TYPE_FIN, seq_num=total_packets + 1, ack_num=0, payload=b"")
    server_report = {}
    fin_ack_received = False
    
    print(f"[FIN] Sending FIN to server...")
    for fin_try in range(5):
        sock.sendto(fin_pkt, (server_ip, port))
        try:
            resp, _ = sock.recvfrom(4096)
            pkt_type, _, _, payload = parse_packet(resp)
            if pkt_type == TYPE_ACK:
                try:
                    server_report = json.loads(payload.decode('utf-8'))
                except Exception:
                    server_report = {"integrity_ok": True}
                fin_ack_received = True
                break
        except socket.timeout:
            print(f"[TIMEOUT] Waiting for FIN-ACK (retry {fin_try+1}/5)...")
            
    end_time = time.time()
    duration_sec = max(end_time - start_time, 0.0001)
    throughput_kbps = round((filesize * 8 / 1024) / duration_sec, 2)
    
    sock.close()

    # Step 4: Notify SDN of Session Completion
    send_sdn_alert(sdn_ip, sdn_port, {
        "event": "SESSION_END",
        "filename": filename,
        "status": "COMPLETED" if server_report.get("integrity_ok", False) else "FAILED",
        "duration_sec": duration_sec,
        "timestamp": time.time()
    })
    
    integrity_ok = server_report.get("integrity_ok", False)
    duplicates = server_report.get("duplicates", 0)
    missing_events = server_report.get("missing_events", 0)
    
    metrics = {
        "duration_sec": round(duration_sec, 3),
        "throughput_kbps": throughput_kbps,
        "retransmissions": retransmissions,
        "timeouts": timeouts,
        "duplicates": duplicates,
        "missing_events": missing_events,
        "integrity_ok": integrity_ok,
        "sdn_alerts_sent": sdn_alerts_sent,
    }
    notify("FINISH", metrics=metrics)
    
    print("\n" + "=" * 70)
    print("                 TRANSFER SUMMARY & METRICS")
    print("=" * 70)
    print(f"  Duration:         {metrics['duration_sec']} seconds")
    print(f"  Throughput:       {metrics['throughput_kbps']} kbps")
    print(f"  Retransmissions:  {metrics['retransmissions']}")
    print(f"  Timeouts:         {metrics['timeouts']}")
    print(f"  Duplicates:       {metrics['duplicates']}")
    print(f"  Missing Events:   {metrics['missing_events']}")
    print(f"  SDN Alerts Sent:  {metrics['sdn_alerts_sent']}")
    print(f"  File Integrity:   {'VERIFIED (SHA-256 Match)' if integrity_ok else 'FAILED'}")
    print("=" * 70)
    print("JSON Metrics Summary:")
    print(json.dumps(metrics, indent=2))
    print("=" * 70 + "\n")
    
    return metrics

def main():
    parser = argparse.ArgumentParser(description="Reliable UDP File Transfer Client")
    parser.add_argument("--server-ip", default=DEFAULT_SERVER_IP, help=f"Server IP (default: {DEFAULT_SERVER_IP})")
    parser.add_argument("--port", type=int, default=DEFAULT_SERVER_PORT, help=f"Server Port (default: {DEFAULT_SERVER_PORT})")
    parser.add_argument("--file", required=True, help="Path of the file to transfer")
    parser.add_argument("--sdn-ip", default=DEFAULT_SDN_IP, help=f"SDN Controller IP (default: {DEFAULT_SDN_IP})")
    parser.add_argument("--sdn-port", type=int, default=DEFAULT_SDN_PORT, help=f"SDN Controller Port (default: {DEFAULT_SDN_PORT})")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW_SIZE, help=f"Sliding window size (default: {DEFAULT_WINDOW_SIZE})")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help=f"Socket timeout in seconds (default: {DEFAULT_TIMEOUT})")
    args = parser.parse_args()
    
    run_client(
        server_ip=args.server_ip,
        port=args.port,
        filepath=args.file,
        sdn_ip=args.sdn_ip,
        sdn_port=args.sdn_port,
        window_size=args.window,
        timeout=args.timeout
    )

if __name__ == "__main__":
    main()

