from mininet.topo import Topo
from mininet.net import Mininet
from mininet.log import lg, info
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
parser = ArgumentParser(description="Bufferbloat tests with QUIC (aioquic) and complex workloads")
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
                    default="reno")

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
def start_qmon(iface, interval_sec=0.1, outfile="q.txt"):
    monitor = Process(target=monitor_qlen,
                      args=(iface, interval_sec, outfile))
    monitor.start()
    return monitor

# -----------------------------------------------------------------------------
# Ping para medir RTT
# -----------------------------------------------------------------------------
def start_ping(net):
    """
    Dispara pings de h1 -> h2 a cada 0.1s, gravando no arquivo de saída.
    """
    h1 = net.get('h1')
    h2 = net.get('h2')
    ping_output = os.path.join(args.dir, 'ping.txt')
    print(f"Iniciando ping: h1 -> h2, salvando em {ping_output}")
    h1.popen(f"ping -i 0.1 -c 100 {h2.IP()} > {ping_output}", shell=True)

# -----------------------------------------------------------------------------
# Servidor TCP (HTTP) - Navegação Complexa (para TCP Reno/BBR)
# -----------------------------------------------------------------------------
def start_complex_tcp_server(net):
    """
    Inicia um servidor HTTP simples (TCP) em h1 para servir múltiplos arquivos estáticos
    no diretório './static'. Você pode alterar a porta se desejar.
    """
    h1 = net.get('h1')
    server_cmd = "cd static && python3 -m http.server 8080"
    print(f"Iniciando servidor TCP HTTP (porta 8080) em h1: {server_cmd}")
    return h1.popen(server_cmd, shell=True)

def start_complex_web_browsing_tcp(net):
    """
    Simula um cliente que 'navega' em h2, requisitando vários arquivos de h1 (TCP).
    """
    h1 = net.get('h1')
    h2 = net.get('h2')
    server_ip = h1.IP()
    
    # Lista de arquivos que existem no diretório 'static'
    resources = ["index.html", "image.jpg", "script.js", "video.mp4"]
    
    for res in resources:
        cmd = f"curl -o /dev/null -s -w %{{time_total}} http://{server_ip}:8080/{res}"
        print(f"Carregando {res} via TCP: {cmd}")
        output = h2.cmd(cmd).strip()
        print(f"Tempo para {res}: {output} s")

# -----------------------------------------------------------------------------
# Servidor QUIC (HTTP/3) - Navegação Complexa
# -----------------------------------------------------------------------------
def start_complex_quic_server(net):
    """
    Inicia um servidor QUIC em h1, servindo a pasta 'static' via HTTP/3.
    Requer aioquic instalado e arquivos cert.pem/key.pem.
    """
    h1 = net.get('h1')
    server_ip = h1.IP()
    cert_path = "cert.pem"
    key_path = "key.pem"
    
    # Porta 4433 por default
    server_cmd = (
        f"python -m aioquic.examples.http3_server "
        f"--certificate {cert_path} "
        f"--private-key {key_path} "
        f"--host {server_ip} "
        f"--port 4433 "
        f"--static-dir ./static "
        f"--quic-log {args.dir}/quic_server.log"
    )
    print(f"Iniciando servidor QUIC (HTTP/3) em {server_ip}:4433 -> {server_cmd}")
    return h1.popen(server_cmd, shell=True)

def start_complex_web_browsing_quic(net):
    """
    Simula um cliente 'navegador' em h2, requisitando vários arquivos (index, image, script, video)
    do servidor QUIC (HTTP/3) em h1.
    """
    h1 = net.get('h1')
    h2 = net.get('h2')
    server_ip = h1.IP()
    
    # Recursos disponíveis em 'static/'
    resources = ["index.html", "image.jpg", "script.js", "video.mp4"]
    
    for res in resources:
        cmd = (
            f"python -m aioquic.examples.http3_client "
            f"--connect {server_ip}:4433 "
            f"https://{server_ip}:4433/{res} "
            f"--output-file /dev/null "
            f"--insecure "  # se for um cert autoassinado
        )
        print(f"Carregando {res} via QUIC: {cmd}")
        start_t = time()
        h2.cmd(cmd)  # executa a requisição
        end_t = time()
        elapsed = end_t - start_t
        print(f"Tempo para {res}: {elapsed:.3f} s")

# -----------------------------------------------------------------------------
# Servidor QUIC (versão longa, substituindo iperf) - já existia no seu script
# -----------------------------------------------------------------------------
def start_quic_server(net):
    """
    Servidor QUIC simples em h1 (para teste de fluxo longo).
    """
    h1 = net.get('h1')
    cert_path = "cert.pem"
    key_path = "key.pem"
    server_cmd = (
        f"python -m aioquic.examples.http3_server "
        f"--certificate {cert_path} "
        f"--private-key {key_path} "
        f"--host {h1.IP()} "
        f"--port 4433 "
        f"--quic-log {args.dir}/quic_server.log "
        f"--output-dir . "
    )
    print(f"Iniciando servidor QUIC em h1: {server_cmd}")
    proc = h1.popen(server_cmd, shell=True)
    sleep(2)
    return proc

def start_quic_long_flow(net):
    """
    Cliente QUIC em h2 para fluxo longo (baixar algo grande).
    """
    h1 = net.get('h1')
    h2 = net.get('h2')
    client_cmd = (
        f"python -m aioquic.examples.http3_client "
        f"--connect {h1.IP()}:4433 "
        f"https://{h1.IP()}:4433/largefile --output-file /dev/null "
    )
    print(f"Iniciando fluxo QUIC (long) em h2: {client_cmd}")
    proc = h2.popen(client_cmd, shell=True)
    return proc

# -----------------------------------------------------------------------------
# Função principal do experimento
# -----------------------------------------------------------------------------
def bufferbloat_quic():
    print(args)
    if not os.path.exists(args.dir):
        os.makedirs(args.dir)

    # Ajusta congestion control do TCP no SO (vale se estivermos testando TCP).
    os.system(f"sysctl -w net.ipv4.tcp_congestion_control={args.cong}")

    # Constrói e inicia a topologia
    topo = BBTopo()
    net = Mininet(topo=topo, host=CPULimitedHost, link=TCLink)
    net.start()

    # Dump das conexões e teste de ping inicial
    dumpNodeConnections(net.hosts)
    net.pingAll()

    # Inicia monitor de fila na interface do gargalo (verifique se é s0-eth2 ou s0-eth1)
    qmon = start_qmon(iface='s0-eth2', outfile=f'{args.dir}/q.txt')

    # ---------------------------
    # Exemplo de experimento:
    # 1) Workload: Navegação Complexa em TCP
    # ---------------------------
    print("\n=== [Fase 1] Navegação Complexa em TCP ===")
    tcp_server_proc = start_complex_tcp_server(net)
    sleep(2)  # dá tempo de iniciar
    start_ping(net)  # para medir RTT durante a navegação
    start_complex_web_browsing_tcp(net)
    tcp_server_proc.terminate()
    sleep(2)

    # ---------------------------
    # 2) Workload: Navegação Complexa em QUIC
    # ---------------------------
    print("\n=== [Fase 2] Navegação Complexa em QUIC ===")
    quic_server_proc_complex = start_complex_quic_server(net)
    sleep(2)  # tempo para iniciar
    start_ping(net)  # outro ping, ou pode manter o anterior
    start_complex_web_browsing_quic(net)
    quic_server_proc_complex.terminate()
    sleep(2)

    # ---------------------------
    # 3) Workload: Fluxo Longo QUIC (opcional)
    # ---------------------------
    print("\n=== [Fase 3] Fluxo Longo QUIC (substituindo iperf) ===")
    quic_server_proc = start_quic_server(net)
    quic_long_flow_proc = start_quic_long_flow(net)
    start_ping(net)
    
    # Mede tempo de fetch (equivalente ao "curl") usando QUIC
    # Baixar 'index.html' 3 vezes de h1 -> h2
    h1 = net.get('h1')
    h2 = net.get('h2')
    fetch_times = []
    for i in range(3):
        cmd = (
            f"python -m aioquic.examples.http3_client "
            f"--connect {h1.IP()}:4433 "
            f"https://{h1.IP()}:4433/index.html "
            f"--output-file /dev/null "
            f"--insecure "
        )
        print(f"Teste de fetch QUIC {i+1} -> {cmd}")
        start_t = time()
        h2.cmd(cmd)
        end_t = time()
        fetch_time = end_t - start_t
        fetch_times.append(fetch_time)
        print(f"[Fetch {i+1}] Tempo: {fetch_time:.4f} s")
        sleep(2)

    avg_fetch_time = sum(fetch_times) / len(fetch_times)
    variance = sum((x - avg_fetch_time) ** 2 for x in fetch_times) / len(fetch_times)
    stddev_fetch_time = math.sqrt(variance)
    print(f"Average page fetch time (QUIC): {avg_fetch_time:.2f} s")
    print(f"Standard deviation: {stddev_fetch_time:.2f} s")

    # Espera o tempo total do experimento
    sleep(args.time)

    # Encerra processos de monitor e servidores
    qmon.terminate()
    quic_long_flow_proc.terminate()
    quic_server_proc.terminate()

    Popen("pgrep -f http3_server | xargs kill -9", shell=True).wait()
    net.stop()

# -----------------------------------------------------------------------------
# Execução
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    bufferbloat_quic()
