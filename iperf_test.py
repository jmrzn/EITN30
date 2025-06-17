
import subprocess
import time
import json
import datetime

class Iperf3Test:
    def __init__(self, host, port=5201):
        self.host = host
        self.port = port

    def run_server(self):
        print(f"Starting iperf3 server on port {self.port}...")
        command = ["iperf3", "-s", "-p", str(self.port), "-J"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print("iperf3 server started. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Stopping iperf3 server.")
            process.terminate()
            process.wait()

    def run_client(self, protocol="UDP", bandwidth="1M", duration=10):
        print(f"Running iperf3 client to {self.host}:{self.port} with {protocol} at {bandwidth} for {duration}s...")
        command = ["iperf3", "-c", self.host, "-p", str(self.port), "-J", "-t", str(duration)]
        if protocol == "UDP":
            command.extend(["-u", "-b", bandwidth])
        
        process = subprocess.run(command, capture_output=True, text=True)
        
        if process.returncode == 0:
            try:
                results = json.loads(process.stdout)
                return results
            except json.JSONDecodeError:
                print("Error decoding JSON:", process.stdout)
                return None
        else:
            print("iperf3 client error:", process.stderr)
            return None

def log_results(data, filename="iperf_results.json"):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_entry = {"timestamp": timestamp, "data": data}
    
    try:
        with open(filename, "r+") as f:
            file_data = json.load(f)
            file_data.append(log_entry)
            f.seek(0)
            json.dump(file_data, f, indent=4)
    except FileNotFoundError:
        with open(filename, "w") as f:
            json.dump([log_entry], f, indent=4)
    print(f"Results logged to {filename}")

def clear_json():
	with open("iperf_results.json", "w") as f:
		json.dump([], f)

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] == "server":
            tester = Iperf3Test(host="0.0.0.0") # Listen on all interfaces
            tester.run_server()
        elif sys.argv[1] == "client" and len(sys.argv) > 2:
            clear_json()
            server_ip = sys.argv[2]
            tester = Iperf3Test(host=server_ip)
            
            # Example: Run UDP tests with increasing bandwidths
            bandwidths = ["1K", "10K", "25K", "50K", "75K", "100K", "125K", "150K", "200K", "250K", "300K"]
            all_results = []
            for bw in bandwidths:
                print(f"\n--- Testing with bandwidth: {bw} ---")
                results = tester.run_client(protocol="UDP", bandwidth=bw, duration=5)
                if results:
                    all_results.append({"bandwidth": bw, "results": results})
                    log_results({"bandwidth": bw, "results": results}, "iperf_results.json")
                    # For now, just print some key info
                    if "end" in results and "udp" in results["end"]:
                        udp_stats = results["end"]["udp"]
                        print(f"  Throughput: {udp_stats['bits_per_second'] / 1_000_000:.2f} Mbps")
                        if 'jitter_ms' in udp_stats:
                            print(f"  Jitter: {udp_stats['jitter_ms']:.2f} ms")
                        if 'lost_packets' in udp_stats and 'packets' in udp_stats:
                            print(f"  Packet Loss: {udp_stats['lost_packets']}/{udp_stats['packets']} ({udp_stats['lost_percent']:.2f}%) ")
                    elif "error" in results:
                        print(f"  Error: {results['error']}")
                time.sleep(2) # Small delay between tests
            
            print("\nAll UDP tests completed.")

        else:
            print("Usage: python3 iperf_test.py server")
            print("       python3 iperf_test.py client <server_ip_address>")
    else:
        print("Usage: python3 iperf_test.py server")
        print("       python3 iperf_test.py client <server_ip_address>")


