sudo ip tuntap add dev myG mode tun
sudo ip addr add 10.0.0.2/24 dev myG
sudo ip link set myG up

sudo ip route add default via 10.0.0.1 dev myG

