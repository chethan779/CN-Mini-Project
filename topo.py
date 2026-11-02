"""
topo.py - Mininet Multipath SDN Topology
=======================================
Creates a 4-switch, 3-host topology with redundant paths:
  Primary: h1/h2 -> s1 -> s2 -> s4 -> h3
  Backup:  h1/h2 -> s1 -> s3 -> s4 -> h3

Switch Port Mapping:
  s1: Port 1=h1, Port 2=h2, Port 3=s2 (Primary), Port 4=s3 (Backup)
  s2: Port 1=s1, Port 2=s4
  s3: Port 1=s1, Port 2=s4
  s4: Port 1=h3, Port 2=s2 (Primary), Port 3=s3 (Backup)
"""

import sys
import os
import time

try:
    from mininet.topo import Topo
    from mininet.net import Mininet
    from mininet.node import OVSSwitch, RemoteController
    from mininet.link import TCLink
    from mininet.cli import CLI
    from mininet.log import setLogLevel, info
    MININET_AVAILABLE = True
except ImportError:
    MININET_AVAILABLE = False
    Topo = object  # dummy fallback for linting/importing

from sdn_controller import SDNController

class MultipathTopo(Topo):
    """
    Mininet topology with two parallel forwarding paths:
    Primary Path via s2, and Backup Path via s3.
    """
    def build(self):
        # Add 4 OpenFlow switches
        # failMode='secure' ensures switches do not fallback to standard learning switch
        s1 = self.addSwitch('s1', cls=OVSSwitch, protocols='OpenFlow13', failMode='secure')
        s2 = self.addSwitch('s2', cls=OVSSwitch, protocols='OpenFlow13', failMode='secure')
        s3 = self.addSwitch('s3', cls=OVSSwitch, protocols='OpenFlow13', failMode='secure')
        s4 = self.addSwitch('s4', cls=OVSSwitch, protocols='OpenFlow13', failMode='secure')

        # Add 3 Hosts
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        h2 = self.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')
        h3 = self.addHost('h3', ip='10.0.0.3/24', mac='00:00:00:00:00:03')

        # Host Links to Edge Switches
        # s1: Port 1 -> h1, Port 2 -> h2
        self.addLink(h1, s1, port2=1)
        self.addLink(h2, s1, port2=2)
        # s4: Port 1 -> h3 (Server)
        self.addLink(h3, s4, port2=1)

        # Inter-Switch Links
        # Primary Path: s1 (port 3) <-> s2 (port 1); s2 (port 2) <-> s4 (port 2)
        self.addLink(s1, s2, port1=3, port2=1)
        self.addLink(s2, s4, port1=2, port2=2)

        # Backup Path: s1 (port 4) <-> s3 (port 1); s3 (port 2) <-> s4 (port 3)
        self.addLink(s1, s3, port1=4, port2=1)
        self.addLink(s3, s4, port1=2, port2=3)

def run():
    if not MININET_AVAILABLE:
        print("[!] Mininet is not installed in this Python environment.")
        print("    To run in Mininet:")
        print("    1. Boot an Ubuntu VM with Mininet and Open vSwitch installed.")
        print("    2. Run: sudo python3 topo.py")
        sys.exit(1)

    setLogLevel('info')
    info('*** Initializing Multipath Topology...\n')
    topo = MultipathTopo()
    
    # We do not need an external controller because sdn_controller.py directly programs OVS flows
    net = Mininet(topo=topo, link=TCLink, switch=OVSSwitch, controller=None)
    
    info('*** Starting Network...\n')
    net.start()

    # Start SDN Controller with dynamic rerouting
    info('*** Starting SDN Controller (Dynamic Mode)...\n')
    controller = SDNController(mode="dynamic")
    controller.start()

    # Allow switch ports to settle and ARP to populate
    info('*** Verifying Connectivity (Ping h1 -> h3)...\n')
    time.sleep(1)
    h1 = net.get('h1')
    h3 = net.get('h3')
    net.ping([h1, h3])

    info('\n' + '=' * 70 + '\n')
    info('*** Mininet CLI is ready!\n')
    info('*** Instructions for live demo:\n')
    info('    1. Start server on h3:   h3 python3 server.py &\n')
    info('    2. Start client on h1:   h1 python3 client.py --file files/sample.bin --server-ip 10.0.0.3\n')
    info('    3. Introduce loss on s1-s2: s1 tc qdisc add dev s1-eth3 root netem loss 10%\n')
    info('    4. Or bring down link:   link s1 s2 down\n')
    info('    5. Observe automatic reroute to s3 in controller logs!\n')
    info('=' * 70 + '\n')

    CLI(net)

    info('*** Stopping SDN Controller and Network...\n')
    controller.stop()
    net.stop()

if __name__ == '__main__':
    run()

