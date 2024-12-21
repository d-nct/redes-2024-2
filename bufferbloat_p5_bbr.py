#!/usr/bin/env python
# bufferbloat_reno.py
#
# Experimento de Bufferbloat com TCP Reno
#

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.log import info
from mininet.util import dumpNodeConnections
from mininet.cli import CLI
from mininet.node import CPULimitedHost
from mininet.link import TCLink

from subprocess import Popen
from time import sleep, time
from multiprocessing import Process
from argparse import ArgumentParser

from monitor import monitor_qlen

import sys
import os
import math

# -----------------------------------------------------------------------------
# Argumentos da linha de comando
# -----------------------------------------------------------------------------
parser = ArgumentParser(description="Bufferbloat tests with TCP Reno")
parser.add_argument('--bw-host', '-B',
                    type=float,
                    help="Bandwidth of host links (Mb/s)",
                    default=1000)
parser.add_argument('--bw-net', '-b',
                    type=float,
                    help="Bandwidth of bottleneck (network) link (Mb/s)",
                    required=True)
parser.add_argument('--delay',
                    type=float,
                    help="Link propagation delay (ms)",
                    required=True,
                    default=5)
parser.add_argument('--dir', '-d',
                    help="Directory to store outputs",
                    required=True)
parser.add_argument('--time', '-t',
                    help="Duration (sec) to run the experiment",
                    type=int,
                    default=10)
parser.add_argument('--maxq',
                    type=int,
                    help="Max buffer size of network interface in packets",
                    default=100)
parser.add_argument('--cong',
                    help="Congestion control algorithm to use (TCP fallback)",
                    default="bbr")

args = parser.parse_args()

# -----------------------------------------------------------------------------
# Topologia
# -----------------------------------------------------------------------------
class BBTopo(Topo):
    "Simple topology for bufferbloat experiment (2 hosts + 1 switch)."
    def build(self, n=2):
        # Hosts
        h1 = self.addHost('h1')
        h2 = self.addHost('h2')
        # Switch
        switch = self.addSwitch('s0')
        # Links
        self.addLink(
            h1, switch,
            bw=args.bw_host,
            delay=f"{args.delay}ms",
            max_queue_size=args.maxq
        )
        self.addLink(
            h2, switch,
            bw=args.bw_net,
            delay=f"{args.delay}ms",
            max_queue_size=args.maxq
        )

# -----------------------------------------------------------------------------
# Monitor de fila
# -----------------------------------------------------------------------------
def start_qmon(iface, interval_sec=0.01, outfile="q.txt"):
    monitor = Process(target=monitor_qlen,
                      args=(iface, interval_sec, outfile))
    monitor.start()
    return monitor

# -----------------------------------------------------------------------------
# Ping para medir RTT
# -----------------------------------------------------------------------------
def start_ping(net):
    h1 = net.get('h1')
    h2 = net.get('h2')
    ping_output = os.path.join(args.dir, 'ping.txt')
    print(f"Iniciando ping: h1 -> h2, salvando em {ping_output}")
    h1.popen(f"ping -i 0.1 -c 100 {h2.IP()} > {ping_output}", shell=True)

# -----------------------------------------------------------------------------
# Fluxo de longa duração em TCP (exemplo: iperf)
# -----------------------------------------------------------------------------
def start_tcp_long_flow(net):
    """
    Inicia iperf: h2 como servidor e h1 como cliente (fluxo longo).
    """
    h1 = net.get('h1')
    h2 = net.get('h2')

    # Servidor
    print("Iniciando iperf servidor em h2...")
    server = h2.popen("iperf -s -w 16m")

    # Cliente
    print("Iniciando iperf cliente em h1 (fluxo de 60s)...")
    client = h1.popen(f"iperf -c {h2.IP()} -t 60") 
    return server, client

# -----------------------------------------------------------------------------
# Workload adicional: Navegação Web com Páginas Complexas
# -----------------------------------------------------------------------------
def start_complex_web_server(net):
    """
    Inicia um servidor Python simples que hospeda uma 'página complexa'.
    Exemplo usando 'python3 -m http.server' na porta 8000.
    """
    h1 = net.get('h1')
    # Vamos supor que exista uma pasta 'web_complex/' com index.html, imagens etc.
    web_dir = "web_complex"
    if not os.path.exists(web_dir):
        # Crie a pasta e algum conteúdo se precisar
        os.makedirs(web_dir)
        # Exemplo de um index.html que referencia vários recursos
        with open(os.path.join(web_dir, "index.html"), "w") as f:
            f.write("<html><head><title>Complex Page</title></head>\n")
            f.write("<body>\n")
            f.write("<h1>Complex Page</h1>\n")
            f.write('<img src="image1.jpg" />\n')
            f.write('<img src="image2.jpg" />\n')
            f.write('<script src="script.js"></script>\n')
            f.write("</body></html>\n")
        # Crie alguns arquivos dummy
        with open(os.path.join(web_dir, "image1.jpg"), "wb") as f:
            f.write(os.urandom(200000))  # ~200 KB
        with open(os.path.join(web_dir, "image2.jpg"), "wb") as f:
            f.write(os.urandom(500000))  # ~500 KB
        with open(os.path.join(web_dir, "script.js"), "w") as f:
            f.write("console.log('JS loaded');")

    server_cmd = f"cd {web_dir} && python3 -m http.server 8000"
    print("Iniciando servidor HTTP de página complexa em h1:", server_cmd)
    return h1.popen(server_cmd, shell=True)

def start_complex_web_client(net):
    """
    Simula um 'navegador' fazendo várias requisições ao servidor.
    Aqui faremos manualmente para cada recurso, ou usaremos 'wget -r'.
    """
    h1 = net.get('h1')
    h2 = net.get('h2')

    # Exemplo: usar wget recursivo para baixar tudo de h1:8000
    # (isso segue links, baixa imagens, etc.)
    client_cmd = f"wget -r -np -N -p http://{h1.IP()}:8000/index.html"
    print("Iniciando cliente 'complex page' em h2:", client_cmd)
    return h2.popen(client_cmd, shell=True)

# -----------------------------------------------------------------------------
# Função principal
# -----------------------------------------------------------------------------
def bufferbloat_bbr():
    print(args)
    if not os.path.exists(args.dir):
        os.makedirs(args.dir)

    # Congestion Control = BBR
    os.system(f"sysctl -w net.ipv4.tcp_congestion_control={args.cong}")

    # Monta topologia e inicia
    topo = BBTopo()
    net = Mininet(topo=topo, host=CPULimitedHost, link=TCLink)
    net.start()

    dumpNodeConnections(net.hosts)
    net.pingAll()

    qmon = start_qmon(iface='s0-eth2', outfile=f'{args.dir}/q.txt')

    # Inicia iperf (TCP BBR)
    server_proc, client_proc = start_tcp_long_flow(net)

    # Inicia ping
    start_ping(net)

    # (Opcional) Navegação complexa
    web_server_proc = start_complex_web_server(net)
    sleep(2)
    web_client_proc = start_complex_web_client(net)

    sleep(args.time)
    qmon.terminate()

    # Finaliza processos
    client_proc.terminate()
    server_proc.terminate()
    web_server_proc.terminate()
    web_client_proc.terminate()
    net.stop()

if __name__ == "__main__":
    bufferbloat_bbr()

