"""
server.py - Reliable UDP File Transfer Server
============================================
Listens on UDP 0.0.0.0:5005, supports multiple concurrent clients,
tracks sequence numbers, buffers out-of-order packets, detects duplicates,
and verifies file integrity using SHA-256.
"""

import socket
import json
import os
import sys
import argparse
from protocol import (
    TYPE_SYN, TYPE_DATA, TYPE_ACK, TYPE_FIN,
    PACKET_NAMES, DEFAULT_SERVER_PORT,
    make_packet, parse_packet, get_file_sha256
)

def run_server(ip: str = "0.0.0.0", port: int = DEFAULT_SERVER_PORT, save_dir: str = "received"):
    """
    Runs the UDP file transfer server.
    """
    os.makedirs(save_dir, exist_ok=True)
    
    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Enable address reuse for smooth testing/restarts
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((ip, port))
    
    print("=" * 70)
    print(f"[*] Reliable UDP Server started on {ip}:{port}")
    print(f"[*] Saving received files to directory: ./{save_dir}/")
    print(f"[*] Waiting for client file transfers...")
    print("=" * 70)
    
    # Dictionary to maintain separate state for each concurrent client:
    # Key: client_addr (ip, port)
    # Value: dict containing session state
    clients = {}
    
    try:
        while True:
            data, client_addr = sock.recvfrom(4096)
            try:
                pkt_type, seq_num, ack_num, payload = parse_packet(data)
            except ValueError as e:
                print(f"[!] Dropping malformed packet from {client_addr}: {e}")
                continue
            
            # -------------------------------------------------------------
            # 1. TYPE_SYN: Initial Handshake & File Metadata
            # -------------------------------------------------------------
            if pkt_type == TYPE_SYN:
                try:
                    meta = json.loads(payload.decode('utf-8'))
                except Exception:
                    meta = {}
                
                raw_filename = meta.get("filename", "unnamed.bin")
                safe_filename = os.path.basename(raw_filename)
                expected_sha = meta.get("sha256", "")
                total_packets = meta.get("total_packets", 0)
                
                save_path = os.path.join(save_dir, f"recv_{client_addr[1]}_{safe_filename}")
                
                # Close any existing open file for this client if previously incomplete
                if client_addr in clients and clients[client_addr].get("file"):
                    try:
                        clients[client_addr]["file"].close()
                    except Exception:
                        pass
                
                file_handle = open(save_path, "wb")
                
                clients[client_addr] = {
                    "filename": safe_filename,
                    "save_path": save_path,
                    "expected_sha256": expected_sha,
                    "total_packets": total_packets,
                    "expected_seq": 1,
                    "buffer": {},           # out-of-order packets {seq: payload}
                    "duplicates": 0,
                    "missing_events": 0,
                    "file": file_handle,
                }
                
                print(f"[SYN] Handshake from {client_addr}: File '{safe_filename}' "
                      f"({total_packets} chunks, SHA-256: {expected_sha[:16]}...)")
                
                # Send SYN-ACK (ack_num=0)
                syn_ack_pkt = make_packet(TYPE_ACK, seq_num=0, ack_num=0, payload=b"SYN_ACK")
                sock.sendto(syn_ack_pkt, client_addr)
                print(f"[ACK] Sent SYN-ACK (Ack 0) to {client_addr}")
            
            # -------------------------------------------------------------
            # 2. TYPE_DATA: In-order Delivery, Duplicate & Gap Handling
            # -------------------------------------------------------------
            elif pkt_type == TYPE_DATA:
                if client_addr not in clients:
                    # Client sent DATA without SYN; send cumulative ACK 0 to trigger sync
                    ack_pkt = make_packet(TYPE_ACK, seq_num=0, ack_num=0, payload=b"NO_SESSION")
                    sock.sendto(ack_pkt, client_addr)
                    continue
                
                session = clients[client_addr]
                expected_seq = session["expected_seq"]
                
                if seq_num == expected_seq:
                    # In-order packet arrived
                    session["file"].write(payload)
                    session["expected_seq"] += 1
                    
                    # Drain any contiguous buffered packets
                    while session["expected_seq"] in session["buffer"]:
                        buffered_seq = session["expected_seq"]
                        buffered_data = session["buffer"].pop(buffered_seq)
                        session["file"].write(buffered_data)
                        print(f"      -> In-order delivered buffered Seq {buffered_seq} for {client_addr}")
                        session["expected_seq"] += 1
                    
                    cum_ack = session["expected_seq"] - 1
                    ack_pkt = make_packet(TYPE_ACK, seq_num=0, ack_num=cum_ack)
                    sock.sendto(ack_pkt, client_addr)
                    print(f"[DATA] Received Seq {seq_num} from {client_addr} ({len(payload)} bytes) -> Cumulative ACK {cum_ack}")
                
                elif seq_num < expected_seq:
                    # Duplicate packet received
                    session["duplicates"] += 1
                    print(f"[DUPLICATE PACKET DETECTED] Seq {seq_num} already received from {client_addr}")
                    cum_ack = session["expected_seq"] - 1
                    ack_pkt = make_packet(TYPE_ACK, seq_num=0, ack_num=cum_ack)
                    sock.sendto(ack_pkt, client_addr)
                    print(f"[ACK] Resent Cumulative ACK {cum_ack} to {client_addr}")
                
                else: # seq_num > expected_seq
                    # Out-of-order packet (Packet gap / Missing packet)
                    if seq_num not in session["buffer"]:
                        session["missing_events"] += 1
                        session["buffer"][seq_num] = payload
                        print(f"[MISSING PACKET DETECTED] Expected Seq {expected_seq}, but got Seq {seq_num} (Buffering Seq {seq_num})")
                    else:
                        session["duplicates"] += 1
                        print(f"[DUPLICATE PACKET DETECTED] Buffered Seq {seq_num} already in buffer from {client_addr}")
                    
                    cum_ack = session["expected_seq"] - 1
                    ack_pkt = make_packet(TYPE_ACK, seq_num=0, ack_num=cum_ack)
                    sock.sendto(ack_pkt, client_addr)
                    print(f"[ACK] Resent Cumulative ACK {cum_ack} to {client_addr}")
            
            # -------------------------------------------------------------
            # 3. TYPE_FIN: Transfer Complete & SHA-256 Integrity Verification
            # -------------------------------------------------------------
            elif pkt_type == TYPE_FIN:
                if client_addr not in clients:
                    continue
                
                session = clients[client_addr]
                try:
                    session["file"].close()
                except Exception:
                    pass
                
                save_path = session["save_path"]
                actual_sha = get_file_sha256(save_path) if os.path.exists(save_path) else ""
                expected_sha = session["expected_sha256"]
                integrity_ok = (actual_sha.lower() == expected_sha.lower())
                
                print("-" * 70)
                if integrity_ok:
                    print(f"[FILE INTEGRITY VERIFIED] SHA-256 Match! ({actual_sha})")
                else:
                    print(f"[FILE INTEGRITY FAILED] Hash mismatch!")
                    print(f"  Expected: {expected_sha}")
                    print(f"  Got:      {actual_sha}")
                print(f"[FIN] Session closed for {client_addr}. File saved to: {save_path}")
                print(f"      Duplicates detected: {session['duplicates']}, Missing/Out-of-order events: {session['missing_events']}")
                print("-" * 70)
                
                report = {
                    "integrity_ok": integrity_ok,
                    "sha256": actual_sha,
                    "duplicates": session["duplicates"],
                    "missing_events": session["missing_events"],
                }
                
                # Reply with ACK containing JSON verification report
                fin_ack_pkt = make_packet(TYPE_ACK, seq_num=seq_num, ack_num=session["expected_seq"], payload=json.dumps(report).encode('utf-8'))
                sock.sendto(fin_ack_pkt, client_addr)
                
                # Cleanup session
                del clients[client_addr]

    except KeyboardInterrupt:
        print("\n[*] Server stopping on user interrupt...")
    finally:
        # Close any lingering client files
        for c, s in clients.items():
            try:
                s["file"].close()
            except Exception:
                pass
        sock.close()
        print("[*] Server socket closed. Goodbye.")

def main():
    parser = argparse.ArgumentParser(description="Reliable UDP File Transfer Server")
    parser.add_argument("--ip", default="0.0.0.0", help="IP address to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=DEFAULT_SERVER_PORT, help=f"Port to bind (default: {DEFAULT_SERVER_PORT})")
    parser.add_argument("--save-dir", default="received", help="Directory to save received files (default: received)")
    args = parser.parse_args()
    
    run_server(ip=args.ip, port=args.port, save_dir=args.save_dir)

if __name__ == "__main__":
    main()

