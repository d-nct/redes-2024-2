#!/usr/bin/env python
# bufferbloat_quic.py
#
# Experimento de Bufferbloat com QUIC (aioquic)
#

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.util import dumpNodeConnections
from mininet.node import CPULimitedHost
from mininet.link import TCLink
from subprocess import Popen
from time import sleep, time
from multiprocessing import Process
from argparse import ArgumentParser

from monitor import monitor_qlen

import os
import math

# -----------------------------------------------------------------------------
# Argumentos
# -----------------------------------------------------------------------------
parser = ArgumentParser(description="Bufferbloat tests with QUIC (aioquic)")
parser.add_argument('--bw-host', '-B', type=float, default=1000)
parser.add_argument('--bw-net', '-b', type=float, required=True)
parser.add_argument('--delay', type=float, required=True, default=5)
parser.add_argument('--dir', '-d', required=True)
parser.add_argument('--time', '-t', type=int, default=10)
parser.add_argument('--maxq', type=int, default=100)
parser.add_argument('--cong', default="reno")  # Cong TCP não afeta QUIC, mas deixamos

args = parser.parse_args()

# -----------------------------------------------------------------------------
# Topologia
# -----------------------------------------------------------------------------
class BBTopo(Topo):
    def build(self, n=2):
        h1 = self.addHost('h1')
        h2 = self.addHost('h2')
        switch = self.addSwitch('s0')
        self.addLink(h1, switch, bw=args.bw_host, delay=f"{args.delay}ms", max_queue_size=args.maxq)
        self.addLink(h2, switch, bw=args.bw_net, delay=f"{args.delay}ms", max_queue_size=args.maxq)

# -----------------------------------------------------------------------------
# Monitor de fila
# -----------------------------------------------------------------------------
def start_qmon(iface, interval_sec=0.1, outfile="q.txt"):
    monitor = Process(target=monitor_qlen, args=(iface, interval_sec, outfile))
    monitor.start()
    return monitor

# -----------------------------------------------------------------------------
# Ping
# -----------------------------------------------------------------------------
def start_ping(net):
    h1 = net.get('h1')
    h2 = net.get('h2')
    ping_output = os.path.join(args.dir, 'ping.txt')
    print(f"Iniciando ping: h1 -> h2, salvando em {ping_output}")
    h1.popen(f"ping -i 0.1 -c 100 {h2.IP()} > {ping_output}", shell=True)

# -----------------------------------------------------------------------------
# Servidor QUIC
# -----------------------------------------------------------------------------
def start_quic_server(net):
    """
    Inicia http3_server em h1, servindo web_complex/ como estático.
    """
    h1 = net.get('h1')
    cert_path = "cert.pem"
    key_path = "key.pem"

    # Crie ou verifique o diretório web_complex
    web_dir = "web_complex"
    if not os.path.exists(web_dir):
        os.makedirs(web_dir)
        # Coloque seus arquivos (index.html, imagens...) como antes

    # Inicia o servidor QUIC com --static-dir
    server_cmd = (
        f"python -m aioquic.examples.http3_server "
        f"--certificate {cert_path} "
        f"--private-key {key_path} "
        f"--host {h1.IP()} "
        f"--port 4433 "
        f"--quic-log {args.dir}/quic_server.log "
        f"--static-dir {web_dir} "
    )
    print(f"Iniciando servidor QUIC em h1: {server_cmd}")
    proc = h1.popen(server_cmd, shell=True)
    sleep(2)
    return proc

# -----------------------------------------------------------------------------
# Fluxo longo QUIC
# -----------------------------------------------------------------------------
def start_quic_long_flow(net):
    h1 = net.get('h1')
    h2 = net.get('h2')

    client_cmd = (
        f"python -m aioquic.examples.http3_client "
        f"--connect {h1.IP()}:4433 "
        f"https://{h1.IP()}:4433/index.html "
    )
    print(f"Iniciando fluxo QUIC em h2: {client_cmd}")
    proc = h2.popen(client_cmd, shell=True)
    return proc

# -----------------------------------------------------------------------------
# Workload adicional: Navegação Complexa em QUIC
# -----------------------------------------------------------------------------
def start_complex_web_navigation_quic(net):
    """
    Múltiplas requisições para simular página complexa (imagens, scripts, etc.).
    Como o http3_client não faz parsing automático do HTML,
    podemos 'forçar' cada download.
    """
    h1 = net.get('h1')
    h2 = net.get('h2')

    # Supondo que temos "index.html", "image1.jpg", "image2.jpg", "script.js", etc.
    # Faremos loop de requisições:
    resources = ["index.html", "image1.jpg", "image2.jpg", "script.js"]
    for res in resources:
        client_cmd = (
            f"python -m aioquic.examples.http3_client "
            f"--connect {h1.IP()}:4433 "
            f"https://{h1.IP()}:4433/{res} "
            f"--output-file /dev/null"
        )
        print(f"Baixando {res} via QUIC: {client_cmd}")
        h2.cmd(client_cmd)  # bloqueante
        sleep(1)

# -----------------------------------------------------------------------------
# Função principal
# -----------------------------------------------------------------------------
def bufferbloat_quic():
    print(args)
    if not os.path.exists(args.dir):
        os.makedirs(args.dir)

    # Ajusta congestion control do TCP no SO (não afeta QUIC, mas mantemos)
    os.system(f"sysctl -w net.ipv4.tcp_congestion_control={args.cong}")

    # Inicia rede
    topo = BBTopo()
    net = Mininet(topo=topo, host=CPULimitedHost, link=TCLink)
    net.start()

    dumpNodeConnections(net.hosts)
    net.pingAll()

    # Monitor
    qmon = start_qmon(iface='s0-eth2', outfile=f'{args.dir}/q.txt')

    # Servidor QUIC
    quic_server_proc = start_quic_server(net)

    # Fluxo longo
    quic_long_flow_proc = start_quic_long_flow(net)

    # Ping
    start_ping(net)

    # Teste de “Navegação Complexa” em QUIC
    start_complex_web_navigation_quic(net)

    # Exemplo de medir tempo de fetch de index.html repetidas vezes
    h1 = net.get('h1')
    h2 = net.get('h2')
    fetch_times = []
    for i in range(3):
        cmd = (
            f"python -m aioquic.examples.http3_client "
            f"--connect {h1.IP()}:4433 "
            f"https://{h1.IP()}:4433/index.html "
            f"--output-file /dev/null"
        )
        start_t = time()
        h2.cmd(cmd)  # bloqueante
        end_t = time()

        ft = end_t - start_t
        fetch_times.append(ft)
        print(f"[Fetch {i+1}] Tempo: {ft:.4f} s")
        sleep(2)

    avg_fetch = sum(fetch_times)/len(fetch_times)
    var_fetch = sum((x-avg_fetch)**2 for x in fetch_times)/len(fetch_times)
    stddev_fetch = math.sqrt(var_fetch)
    print(f"Average page fetch time (QUIC): {avg_fetch:.2f} s, std dev: {stddev_fetch:.2f} s")

    sleep(args.time)
    qmon.terminate()

    quic_long_flow_proc.terminate()
    quic_server_proc.terminate()

    # Mata possíveis processos remanescentes
    Popen("pgrep -f http3_server | xargs kill -9", shell=True).wait()

    net.stop()

if __name__ == "__main__":
    bufferbloat_quic()

