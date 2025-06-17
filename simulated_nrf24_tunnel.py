import time
import argparse
import threading
import struct
import os
import select
import socket
import sys
import logging
import signal
import queue
import random
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('nrf24_sim.log')
    ]
)
logger = logging.getLogger(__name__)

try:
    import pytun
except ImportError:
    logger.error("Error: pytun library not found. Please install it: pip3 install python-pytun")
    sys.exit(1)


BASE_STATION_IP = "192.168.2.107"
MOBILE_UNIT_IP = "192.168.2.105"   
BASE_STATION_PORT = 5000           
MOBILE_UNIT_PORT = 5001            


TUN_IF_NAME = "myG"              
TUN_IF_IP_BASE = "10.0.0.1"        
TUN_IF_IP_MOBILE = "10.0.0.2"      
TUN_IF_NETMASK = "255.255.255.0"
TUN_MTU = 1400

STAT_PRINT = True

NRF_PAYLOAD_SIZE = 32               # Max payload size for NRF24L01+
HEADER_LEN = 4                     # 4 bytes for header (same as original design)
MAX_DATA_PER_PACKET = NRF_PAYLOAD_SIZE - HEADER_LEN  # 28 bytes for actual data

# Artificial constraints (optional)
ARTIFICIAL_PACKET_LOSS = 0.0       # Probability of packet loss (0.0 = no loss)
ARTIFICIAL_DELAY_MS = 0            # Artificial delay in milliseconds

# Reassembly Buffer
reassembly_buffer = {}  # Key: packet_id, Value: {'fragments': {offset: data}, 'total_len_approx': 0, 'last_seen': time.time()}
REASSEMBLY_TIMEOUT = 30 # Seconds to keep fragments before discarding

ACK_PREFIX = b'ACK'
ack_queue = queue.Queue()

current_packet_id = 0

performance_stats = {
    'packets_sent': 0,
    'packets_received': 0,
    'fragments_sent': 0,
    'fragments_received': 0,
    'bytes_sent': 0,
    'bytes_received': 0,
    'start_time': None,
    'packet_latencies': [],  # List of (packet_id, latency_ms) tuples
}

# --- Helper Functions ---
def get_next_packet_id():
    global current_packet_id
    current_packet_id = (current_packet_id + 1) % 256
    return current_packet_id

def apply_artificial_constraints(should_drop=True):
    # Simulate packet loss
    if should_drop and ARTIFICIAL_PACKET_LOSS > 0:
        if random.random() < ARTIFICIAL_PACKET_LOSS:
            return False  # Drop packet
    
    # Simulate transmission delay
    if ARTIFICIAL_DELAY_MS > 0:
        time.sleep(ARTIFICIAL_DELAY_MS / 1000.0)
    
    return True  # Packet not dropped

def update_stats(action, bytes_count=0, is_fragment=False, packet_id=None, latency_ms=None):
    global performance_stats
    
    if performance_stats['start_time'] is None:
        performance_stats['start_time'] = time.time()
    
    if action == 'send':
        if is_fragment:
            performance_stats['fragments_sent'] += 1
        else:
            performance_stats['packets_sent'] += 1
        performance_stats['bytes_sent'] += bytes_count
    
    elif action == 'receive':
        if is_fragment:
            performance_stats['fragments_received'] += 1
        else:
            performance_stats['packets_received'] += 1
        performance_stats['bytes_received'] += bytes_count
    
    if packet_id is not None and latency_ms is not None:
        performance_stats['packet_latencies'].append((packet_id, latency_ms))

def print_stats():
    global performance_stats
    
    if performance_stats['start_time'] is None:
        logger.info("No statistics available yet.")
        return
    
    elapsed = time.time() - performance_stats['start_time']
    if elapsed == 0:
        elapsed = 0.001  # Avoid division by zero
    
    logger.info("=== Performance Statistics ===")
    logger.info(f"Running time: {elapsed:.2f} seconds")
    logger.info(f"Packets sent: {performance_stats['packets_sent']}")
    logger.info(f"Packets received: {performance_stats['packets_received']}")
    logger.info(f"Fragments sent: {performance_stats['fragments_sent']}")
    logger.info(f"Fragments received: {performance_stats['fragments_received']}")
    logger.info(f"Bytes sent: {performance_stats['bytes_sent']}")
    logger.info(f"Bytes received: {performance_stats['bytes_received']}")
    
    send_rate = (performance_stats['bytes_sent'] * 8) / elapsed / 1000  # kbps
    recv_rate = (performance_stats['bytes_received'] * 8) / elapsed / 1000  # kbps
    
    logger.info(f"Send rate: {send_rate:.2f} kbps")
    logger.info(f"Receive rate: {recv_rate:.2f} kbps")
    
    # Calculate average latency if we have data
    if performance_stats['packet_latencies']:
        avg_latency = sum(lat for _, lat in performance_stats['packet_latencies']) / len(performance_stats['packet_latencies'])
        logger.info(f"Average packet latency: {avg_latency:.2f} ms")
    
    logger.info("=============================")

# --- UDP Socket Functions ---
def setup_udp_socket(local_ip, local_port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((local_ip, local_port))
    sock.setblocking(False)  # Non-blocking mode
    logger.info(f"UDP socket bound to {local_ip}:{local_port}")
    return sock

# --- TUN Interface Functions ---
def setup_tun_interface(if_name, ip_addr, netmask, mtu):
    try:
        tun = pytun.TunTapDevice(name=if_name, flags=pytun.IFF_TUN | pytun.IFF_NO_PI)
        tun.addr = ip_addr
        tun.netmask = netmask
        tun.mtu = mtu
        tun.up()
        logger.info(f"TUN interface {if_name} created. IP: {ip_addr}, MTU: {mtu}")
        return tun
    except Exception as e:
        logger.error(f"Error creating TUN interface: {e}")
        logger.error("Ensure you are running this script with sudo privileges.")
        sys.exit(1)

# --- Packet Processing Functions ---
def fragment_and_send(sock, ip_packet, dest_addr, dest_port, packet_id):
    packet_len = len(ip_packet)
    logger.debug(f"Fragmenting IP packet (ID: {packet_id}, len: {packet_len})")
    
    # Add timestamp for latency measurement (first 8 bytes of packet data)
    timestamp = int(time.time() * 1000)  # milliseconds
    
    offset = 0
    fragments_sent = 0
    
    while offset < packet_len:
        # Determine chunk size for this fragment
        chunk_size = min(MAX_DATA_PER_PACKET, packet_len - offset)
        chunk = ip_packet[offset:offset + chunk_size]
        offset += chunk_size
        
        # Set flags
        more_fragments = 1 if offset < packet_len else 0
        flags = more_fragments  # MF bit is bit 0
        
        # Calculate scaled offset (in 8-byte blocks)
        current_fragment_offset = offset - chunk_size
        scaled_offset = current_fragment_offset  # Divide by 8
        
        # Create header
        header = struct.pack("!BHB", packet_id, scaled_offset, flags)
        
        # Create UDP payload (simulated NRF24L01+ packet)
        udp_payload = header + chunk
        
        # Apply artificial constraints (packet loss, delay)
        if not apply_artificial_constraints():
            logger.debug(f"  Simulated packet loss for fragment: ID={packet_id}, Offset={current_fragment_offset}")
            continue
        
        # Send via UDP
        try:
            sock.sendto(udp_payload, (dest_addr, dest_port))
            #print(udp_payload)
            fragments_sent += 1
            update_stats('send', len(udp_payload), is_fragment=True)
            logger.debug(f"  Sent fragment: ID={packet_id}, Offset={current_fragment_offset}, Len={len(chunk)}, MF={more_fragments}")
        except Exception as e:
            logger.error(f"  Failed to send fragment: ID={packet_id}, Offset={current_fragment_offset}, Error: {e}")
            return False
        
        # Small delay between fragments to prevent overwhelming receiver
        time.sleep(0.001)
    
    update_stats('send', packet_len, is_fragment=False, packet_id=packet_id)
    logger.debug(f"Sent {fragments_sent} fragments for packet ID {packet_id}")
    return True

def reassemble_packet(packet_id, fragment_offset_scaled, flags, data):
    more_fragments = flags & 0x01
    fragment_offset = fragment_offset_scaled # Multiply by 8
    
    if packet_id not in reassembly_buffer:
        reassembly_buffer[packet_id] = {
            'fragments': {}, 
            'last_seen': time.time(), 
            'highest_offset_seen': -1, 
            'got_last_fragment': False
        }
    
    entry = reassembly_buffer[packet_id]
    entry['fragments'][fragment_offset] = data
    entry['last_seen'] = time.time()
    
    # Track the highest offset to estimate total length
    current_data_end_offset = fragment_offset + len(data)
    if current_data_end_offset > entry['highest_offset_seen']:
        entry['highest_offset_seen'] = current_data_end_offset
    
    if not more_fragments:
        entry['got_last_fragment'] = True
        entry['expected_total_length'] = current_data_end_offset
    
    # Check for completion
    if entry['got_last_fragment']:
        # We have the last fragment. Check if all prior fragments are present.
        sorted_offsets = sorted(entry['fragments'].keys())
        full_packet_data = bytearray()
        current_reassembled_offset = 0
        complete = True
        
        for off in sorted_offsets:
            frag_data = entry['fragments'][off]
            if off != current_reassembled_offset:
                complete = False  # Gap detected
                break
            full_packet_data.extend(frag_data)
            current_reassembled_offset += len(frag_data)
        
        if complete and current_reassembled_offset == entry['expected_total_length']:
            logger.debug(f"Packet ID {packet_id} reassembled successfully (len: {len(full_packet_data)}).")
            del reassembly_buffer[packet_id]
            update_stats('receive', len(full_packet_data), is_fragment=False, packet_id=packet_id)
            
            
            message = extract_custom_message(bytes(full_packet_data))
            if message:
                logger.info(f"Received custom message: {message.decode(errors='ignore')}")

            return bytes(full_packet_data)
        elif complete and current_reassembled_offset > entry['expected_total_length']:
            logger.warning(f"Reassembled packet ID {packet_id} is longer than expected. Discarding.")
            del reassembly_buffer[packet_id]
            return None
    
    return None  # Packet not yet complete

def extract_custom_message(ip_packet_data):
    #print(ip_packet_data)
    if ip_packet_data.startswith(b"[MyMessage]"):
        return b"[MyMessage]"
    return None

def cleanup_reassembly_buffer():
    now = time.time()
    expired_ids = [pid for pid, data in reassembly_buffer.items() if now - data['last_seen'] > REASSEMBLY_TIMEOUT]
    for pid in expired_ids:
        logger.debug(f"Timing out reassembly for packet ID {pid}")
        del reassembly_buffer[pid]

# --- Main Threads ---
def tun_to_udp_thread(tun, sock, remote_ip, remote_port):
    logger.info(f"Starting TUN -> UDP thread (sending to {remote_ip}:{remote_port})...")
    
    while True:
        try:
            # Non-blocking read from TUN with timeout
            ready_to_read, _, _ = select.select([tun.fileno()], [], [], 0.1)  # 100ms timeout
            if ready_to_read:
                #msg = b"[MyMessage] Hellow World!"
                ip_packet = tun.read(tun.mtu)  # Read up to MTU size
                #custom_ip = msg + ip_packet
                if ip_packet:
                    logger.debug(f"Read {len(ip_packet)} bytes from TUN interface {tun.name}")
                    packet_id = get_next_packet_id()
                    success = fragment_and_send(sock, ip_packet, remote_ip, remote_port, packet_id)
                    #message = extract_custom_message(bytes(custom_ip))
                    #print(f"Extracted encoded message {message}")
                    if success:
                        try:
                            acked_id = ack_queue.get(timeout=5)
                            #print("-------Waiting--------")
                            #time.sleep(20)
                            #STAT_PRINT = False
                            #msg = input("What is your next message: ")

                            
                            #STAT_PRINT = True
                            if acked_id != packet_id:
                                logger.warning(f"Unexpected ACK recieved (excpected {packet_id}, got {acked_id}")
                        except queue.Empty:
                            logger.warning(f"Timeout waiting for ACK for packet ID {packet_id}")                       
                        
        except Exception as e:
            logger.error(f"Error in tun_to_udp_thread: {e}")
            time.sleep(1)

def udp_to_tun_thread(sock, tun):
    logger.info("Starting UDP -> TUN thread...")
    
    while True:
        try:
            # Non-blocking receive with timeout
            ready_to_read, _, _ = select.select([sock.fileno()], [], [], 0.1)  # 100ms timeout
            if ready_to_read:
                try:
                    udp_payload, addr = sock.recvfrom(NRF_PAYLOAD_SIZE)
                    if udp_payload.startswith(ACK_PREFIX):
                        ack_id = struct.unpack("!B", udp_payload[3:4])[0]
                        logger.debug(f"Received ACK for packet ID {ack_id}")
                        ack_queue.put(ack_id)
                        
                    elif udp_payload and len(udp_payload) >= HEADER_LEN:
                        logger.debug(f"Received {len(udp_payload)} bytes from UDP {addr}")
                        
                        # Apply artificial constraints (packet loss only, delay already applied at sender)
                        if not apply_artificial_constraints(should_drop=False):
                            logger.debug(f"  Simulated packet loss for received fragment")
                            continue
                        
                        # Extract header and data
                        header_data = udp_payload[:HEADER_LEN]
                        ip_fragment_data = udp_payload[HEADER_LEN:]
                        
                        packet_id, fragment_offset_scaled, flags = struct.unpack("!BHB", header_data)
                        logger.debug(f"  Fragment: ID={packet_id}, ScaledOffset={fragment_offset_scaled}, Flags={flags}, DataLen={len(ip_fragment_data)}")
                        
                        update_stats('receive', len(udp_payload), is_fragment=True)
                        
                        # Reassemble packet
                        reassembled_ip_packet = reassemble_packet(packet_id, fragment_offset_scaled, flags, ip_fragment_data)
                        if reassembled_ip_packet:
                            logger.debug(f"Writing {len(reassembled_ip_packet)} bytes to TUN interface {tun.name}")
                            tun.write(reassembled_ip_packet)
                            #print("WRITTERN TO TUN")
                            ack_packet = ACK_PREFIX + struct.pack("!B", packet_id)
                            sock.sendto(ack_packet, addr)  # Send ACK back to sender
                            logger.debug(f"Sending an ACK for {packet_id}")
                            
                            
                            
                    elif udp_payload:
                        logger.warning(f"Received short UDP payload (len: {len(udp_payload)}), discarding.")
                except socket.error as e:
                    if e.errno != socket.EAGAIN and e.errno != socket.EWOULDBLOCK:
                        logger.error(f"Socket error: {e}")
            
            # Clean up reassembly buffer periodically
            cleanup_reassembly_buffer()
            
        except Exception as e:
            logger.error(f"Error in udp_to_tun_thread: {e}")
            time.sleep(1)

def stats_thread():
    while True:
        time.sleep(30)  # Print stats every 10 seconds
        if STAT_PRINT is True:
            print_stats()

# --- Traffic Control Functions ---
def setup_traffic_control(interface, rate_kbps=250, latency_ms=0):
    try:
        # Remove any existing qdisc
        os.system(f"sudo tc qdisc del dev {interface} root 2>/dev/null")
        
        # Add rate limiting
        if rate_kbps > 0:
            cmd = f"sudo tc qdisc add dev {interface} root tbf rate {rate_kbps}kbit burst 32kbit latency 400ms"
            logger.info(f"Setting bandwidth limit: {cmd}")
            ret = os.system(cmd)
            if ret != 0:
                logger.warning(f"Failed to set bandwidth limit on {interface}")
        
        # Add latency if specified
        if latency_ms > 0:
            cmd = f"sudo tc qdisc add dev {interface} root netem delay {latency_ms}ms"
            logger.info(f"Setting artificial latency: {cmd}")
            ret = os.system(cmd)
            if ret != 0:
                logger.warning(f"Failed to set latency on {interface}")
        
        logger.info(f"Traffic control configured on {interface}")
        return True
    except Exception as e:
        logger.error(f"Error setting up traffic control: {e}")
        return False

def cleanup_traffic_control(interface):
    try:
        os.system(f"sudo tc qdisc del dev {interface} root 2>/dev/null")
        logger.info(f"Traffic control removed from {interface}")
        return True
    except Exception as e:
        logger.error(f"Error cleaning up traffic control: {e}")
        return False

# --- Signal Handling ---
def signal_handler(sig, frame):
    logger.info("Shutting down...")
    print_stats()
    
    # Clean up resources
    if 'tun_device' in globals() and tun_device:
        tun_device.down()
        tun_device.close()
        logger.info("TUN interface closed.")
    
    # Remove traffic control if it was applied
    if args.tc_interface:
        cleanup_traffic_control(args.tc_interface)
    
    sys.exit(0)

# --- Main Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulated NRF24L01+ IP Tunneling Script")
    parser.add_argument("--role", type=str, choices=["base", "mobile"], required=True, help="Role of this node: 'base' or 'mobile'")
    parser.add_argument("--local-ip", type=str, help="Local IP address for UDP socket binding")
    parser.add_argument("--remote-ip", type=str, help="Remote IP address to send UDP packets to")
    parser.add_argument("--tun-ip", type=str, help="IP address for the TUN interface")
    parser.add_argument("--packet-loss", type=float, default=ARTIFICIAL_PACKET_LOSS, help="Artificial packet loss probability (0.0-1.0)")
    parser.add_argument("--delay", type=int, default=ARTIFICIAL_DELAY_MS, help="Artificial delay in milliseconds")
    parser.add_argument("--tc-interface", type=str, help="Interface to apply traffic control (bandwidth/latency limits)")
    parser.add_argument("--tc-rate", type=int, default=250, help="Traffic control bandwidth limit in kbps")
    parser.add_argument("--tc-latency", type=int, default=0, help="Traffic control latency in milliseconds")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    
    args = parser.parse_args()
    
    # Set debug level if requested
    if args.debug:
        logger.setLevel(logging.DEBUG)
    
    # Set artificial constraints
    ARTIFICIAL_PACKET_LOSS = args.packet_loss
    ARTIFICIAL_DELAY_MS = args.delay
    
    # Determine IP addresses and ports based on role
    if args.role == "base":
        local_ip = args.local_ip if args.local_ip else BASE_STATION_IP
        local_port = BASE_STATION_PORT
        remote_ip = args.remote_ip if args.remote_ip else MOBILE_UNIT_IP
        remote_port = MOBILE_UNIT_PORT
        tun_ip = args.tun_ip if args.tun_ip else TUN_IF_IP_BASE
    elif args.role == "mobile":
        local_ip = args.local_ip if args.local_ip else MOBILE_UNIT_IP
        local_port = MOBILE_UNIT_PORT
        remote_ip = args.remote_ip if args.remote_ip else BASE_STATION_IP
        remote_port = BASE_STATION_PORT
        tun_ip = args.tun_ip if args.tun_ip else TUN_IF_IP_MOBILE
    else:
        logger.error("Invalid role specified.")
        sys.exit(1)
    
    logger.info(f"Starting Simulated NRF24 IP Tunnel - Role: {args.role}, TUN IP: {tun_ip}")
    logger.info(f"Local UDP: {local_ip}:{local_port}, Remote UDP: {remote_ip}:{remote_port}")
    logger.info(f"Artificial constraints - Packet Loss: {ARTIFICIAL_PACKET_LOSS}, Delay: {ARTIFICIAL_DELAY_MS}ms")
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    if args.tc_interface:
        setup_traffic_control(args.tc_interface, args.tc_rate, args.tc_latency)
    
    tun_device = setup_tun_interface(TUN_IF_NAME, tun_ip, TUN_IF_NETMASK, TUN_MTU)
    
    udp_socket = setup_udp_socket(local_ip, local_port)
    
    thread1 = threading.Thread(target=tun_to_udp_thread, args=(tun_device, udp_socket, remote_ip, remote_port), daemon=True)
    thread2 = threading.Thread(target=udp_to_tun_thread, args=(udp_socket, tun_device), daemon=True)
    thread3 = threading.Thread(target=stats_thread, daemon=True)
    
    thread1.start()
    thread2.start()
    thread3.start()
    
    logger.info("Threads started. Tunnel is active. Press Ctrl+C to exit.")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Exiting...")
    finally:
        # Cleanup handled by signal_handler
        pass
