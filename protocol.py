"""
protocol.py - Reliable UDP File Transfer Protocol Definition
=============================================================
Defines packet types, binary header formatting, and SHA-256 integrity utilities.

Packet Header Layout (13 bytes total, Network Byte Order '!'):
  +--------------+------------------+------------------+----------------------+
  | pkt_type (1B)|  seq_num (4B)    |  ack_num (4B)    |  payload_len (4B)    |
  +--------------+------------------+------------------+----------------------+
  Followed by `payload_len` bytes of payload data.
"""

import struct
import hashlib
import os

# --- Packet Types ---
TYPE_SYN  = 1   # Handshake / metadata exchange
TYPE_DATA = 2   # File data chunk
TYPE_ACK  = 3   # Acknowledgment (cumulative)
TYPE_FIN  = 4   # End of transfer & verification request

# Human-readable mapping for logging
PACKET_NAMES = {
    TYPE_SYN:  "SYN",
    TYPE_DATA: "DATA",
    TYPE_ACK:  "ACK",
    TYPE_FIN:  "FIN",
}

# --- Header Specification ---
# ! = Network byte order (big-endian)
# B = unsigned char (1 byte)
# I = unsigned int  (4 bytes)
HEADER_FORMAT = "!BIII"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 13 bytes

# --- Default Protocol Parameters ---
CHUNK_SIZE = 1024               # 1 KB per chunk
DEFAULT_SERVER_IP = "10.0.0.3"  # Default h3 server IP in Mininet
DEFAULT_SERVER_PORT = 5005      # Server UDP listening port
DEFAULT_SDN_IP = "127.0.0.1"    # Default SDN controller IP
DEFAULT_SDN_PORT = 9999         # SDN controller alert UDP port
DEFAULT_WINDOW_SIZE = 4         # Sliding window size
DEFAULT_TIMEOUT = 0.3           # Socket timeout in seconds


def make_packet(pkt_type: int, seq_num: int, ack_num: int, payload: bytes = b"") -> bytes:
    """
    Packs a packet header and appends the payload.
    Header: '!BIII' -> pkt_type (1B), seq_num (4B), ack_num (4B), len(payload) (4B).
    """
    if isinstance(payload, str):
        payload = payload.encode('utf-8')
    header = struct.pack(HEADER_FORMAT, pkt_type, seq_num, ack_num, len(payload))
    return header + payload


def parse_packet(data: bytes) -> tuple[int, int, int, bytes]:
    """
    Unpacks raw bytes into (pkt_type, seq_num, ack_num, payload).
    Raises ValueError if data is smaller than the 13-byte header.
    """
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Packet too short: {len(data)} bytes (expected at least {HEADER_SIZE})")
    
    pkt_type, seq_num, ack_num, payload_len = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
    payload = data[HEADER_SIZE : HEADER_SIZE + payload_len]
    return pkt_type, seq_num, ack_num, payload


def get_file_sha256(filepath: str) -> str:
    """
    Computes and returns the SHA-256 hexadecimal digest of a file.
    Reads in 64KB blocks to handle large files efficiently.
    """
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

