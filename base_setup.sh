sudo ip tuntap add dev myG mode tun
sudo ip addr add 10.0.0.1/24 dev myG
sudo ip link set myG up

echo 1 | sudo tee /proc/sys/net/ipv4/ip_forward
sudo sysctl -p
sudo iptables -F
sudo iptables -t nat -A POSTROUTING -o wlan0  -j MASQUERADE
sudo iptables -A FORWARD -i wlan0  -o myG  -m state --state RELATED,ESTABLISHED -j ACCEPT
sudo iptables -A FORWARD -i myG  -o wlan0  -j ACCEPT
