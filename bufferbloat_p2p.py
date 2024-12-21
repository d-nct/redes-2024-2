from mininet.topo import Topo
from mininet.net import Mininet
from mininet.log import lg, info
from mininet.util import dumpNodeConnections
from mininet.cli import CLI
from mininet.node import CPULimitedHost
from mininet.link import TCLink
#from mininet.util import cleanUpHosts


from subprocess import Popen, PIPE
from time import sleep, time
from multiprocessing import Process
from argparse import ArgumentParser

from monitor import monitor_qlen

import sys
import os
import math
import time
import threading

class BBTopo(Topo):
    "Simple topology for bufferbloat experiment."

    def build(self, n=2):
        server = self.addHost('server', ip='10.0.0.1')
        client2 = self.addHost('client1', ip='10.0.0.2')
        client2 = self.addHost('client2', ip='10.0.0.3')

        switch = self.addSwitch('s1')


def qmon_logger(switch, filename, duration=60, interval=0.1):
    """
    Monitor queue lengths on the switch and log to a file
    every `interval` seconds, for `duration` seconds.
    """
    with open(filename, 'w') as f:
        f.write("Time,QueueLength,Switch\n")
        start_time = time.time()
        while time.time() - start_time < duration:
            stats = monitor_qlen(switch)  # returns list of (iface, qlen)
            for iface, qlen in stats:
                timestamp = time.time() - start_time
                f.write(f"{timestamp},{qlen},{switch.name}-{iface}\n")
            time.sleep(interval)


def bufferbloat():
    net = Mininet(link=TCLink)

    server = net.addHost('server', ip='10.0.0.1')
    client1 = net.addHost('client1', ip='10.0.0.2')
    client2 = net.addHost('client2', ip='10.0.0.3')

    switch = net.addSwitch('s1')

    link1 = net.addLink(server,switch,bw=100,delay='120ms', max_queue_size=1000)
    link2 = net.addLink(client1,switch,cls=TCLink,bw=50,delay='50ms', max_queue_size=1000)
    link3 = net.addLink(client2,switch,bw=10,delay='30ms', max_queue_size=1000)

    net.start()

    log_file = "qmon_output.csv"
    print(f"Starting custom qmon logger, data will be saved to {log_file}")
    qmon_thread = Process(target=qmon_logger, args=(switch, log_file, 60, 0.1))
    qmon_thread.start()

    server.cmd('python3 p2p_server.py &')  # Start the QUIC server
    client1.cmd('python3 p2p_client.py 10.0.0.1 10.0.0.2 60 &')  # Start client 1
    client2.cmd('python3 p2p_client.py 10.0.0.1 10.0.0.3 60 &')  # Start client 2

    time.sleep(60)
    qmon_thread.terminate()
    net.stop()

if __name__ == "__main__":
    bufferbloat()
