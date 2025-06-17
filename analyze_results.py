
import json
import matplotlib.pyplot as plt

def parse_iperf_results(filename="iperf_results.json"):
    data = []
    try:
        with open(filename, "r") as f:
            log_entries = json.load(f)
            print("Opened log entries")
            for entry in log_entries:
                bw = entry["data"]["bandwidth"]
                results = entry["data"]["results"]
                print("On bandwidth", bw)
                if "end" in results:
                    udp_stats = results["end"]["streams"][0]["udp"]
                    throughput_kbps = udp_stats["bits_per_second"] / 1_000_000
                    jitter_ms = udp_stats.get("jitter_ms", 0) # Default to 0 if not present
                    lost_percent = udp_stats.get("lost_percent", 0) # Default to 0 if not present
                    print("emd")
                    
                    data.append({
                        "bandwidth": bw,
                        "throughput_kbps": throughput_kbps,
                        "jitter_ms": jitter_ms,
                        "lost_percent": lost_percent
                    })
                elif "error" in results:
                    print("errpr in results")
                #    print(f"Error in entry for bandwidth {bw}: {results['error']}")
    except FileNotFoundError:
        print(f"Error: {filename} not found.")
    return data

def generate_plots(data):
    if not data:
        print("No data to plot.")
        return

    # Extracting data for plotting
    arrival_rates = [float(d["bandwidth"].replace("K", "")) for d in data] # Convert '1M' to 1.0
    throughputs = [d["throughput_kbps"] for d in data]
    latencies = [d["jitter_ms"] for d in data]
    packet_losses = [d["lost_percent"] for d in data]

    # Plot 1: Throughput vs. Arrival Rate
    plt.figure(figsize=(10, 6))
    plt.plot(arrival_rates, throughputs, marker='o', linestyle='-')
    plt.title('Output Throughput vs. Arrival Rate (UDP)')
    plt.xlabel('Arrival Rate (Kbps)')
    plt.ylabel('Output Throughput (Kbps)')
    plt.grid(True)
    plt.savefig('throughput_vs_arrival_rate.png')
    plt.close()
    print("Generated throughput_vs_arrival_rate.png")

    # Plot 2: Latency (Jitter) vs. Arrival Rate
    plt.figure(figsize=(10, 6))
    plt.plot(arrival_rates, latencies, marker='o', linestyle='-')
    plt.title('Latency (Jitter) vs. Arrival Rate (UDP)')
    plt.xlabel('Arrival Rate (Kbps)')
    plt.ylabel('Latency (Jitter) (ms)')
    plt.grid(True)
    plt.savefig('latency_vs_arrival_rate.png')
    plt.close()
    print("Generated latency_vs_arrival_rate.png")

    # Plot 3: Packet Loss vs. Arrival Rate
    plt.figure(figsize=(10, 6))
    plt.plot(arrival_rates, packet_losses, marker='o', linestyle='-')
    plt.title('Packet Loss vs. Arrival Rate (UDP)')
    plt.xlabel('Arrival Rate (Kbps)')
    plt.ylabel('Packet Loss (%)')
    plt.grid(True)
    plt.savefig('packet_loss_vs_arrival_rate.png')
    plt.close()
    print("Generated packet_loss_vs_arrival_rate.png")

if __name__ == "__main__":
    results_data = parse_iperf_results()
    if results_data:
        generate_plots(results_data)
    else:
        print("Could not parse iperf results or no data available.")


