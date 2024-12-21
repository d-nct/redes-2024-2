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
parser = ArgumentParser(description="Bufferbloat tests with QUIC (aioquic)")
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
# Servidor QUIC (substituindo o servidor TCP)
# -----------------------------------------------------------------------------
def start_quic_server(net):
    """
    Inicia um servidor QUIC em h1.
    Usaremos o servidor exemplo do aioquic (http3_server).
    Certifique-se de que os arquivos cert.pem e key.pem existam no diretório.
    """
    h1 = net.get('h1')
    # Ajuste o caminho do cert e key se estiverem em outra pasta
    cert_path = "cert.pem"
    key_path = "key.pem"
    # Porta padrão escolhida 4433 (pode ser outra)
    server_cmd = (
        f"python -m aioquic.examples.http3_server "
        f"--certificate {cert_path} "
        f"--private-key {key_path} "
        f"--host {h1.IP()} "
        f"--port 4433 "
        f"--quic-log {args.dir}/quic_server.log "
        f"--output-dir . "
        # Se quiser servir algum arquivo grande, crie um 'largefile' local
        # e coloque em --static-dir, por exemplo.
        # f"--static-dir . "
    )
    print(f"Iniciando servidor QUIC em h1: {server_cmd}")
    proc = h1.popen(server_cmd, shell=True)
    sleep(2)
    return proc

# -----------------------------------------------------------------------------
# Cliente QUIC de fluxo longo (substituindo iperf)
# -----------------------------------------------------------------------------
def start_quic_long_flow(net):
    """
    Inicia um cliente QUIC em h2 que faz uma transferência "longa" do servidor
    (equivalente ao fluxo de longa duração do iperf).
    Podemos usar, por exemplo, um arquivo grande para simular.
    """
    h1 = net.get('h1')
    h2 = net.get('h2')
    
    # A ideia aqui é ficar baixando algum arquivo do servidor QUIC por 'args.time' segundos.
    # O http3_client não tem opção nativa de 'limitar tempo' como o iperf,
    # então podemos simplesmente baixar repetidamente, ou baixar um arquivo grande de ~100MB.
    # Exemplo de download simples de index.html repetido (5 vezes, etc.).
    # Ajuste conforme necessidade.
    
    client_cmd = (
        f"python -m aioquic.examples.http3_client "
        f"--connect {h1.IP()}:4433 "
        # f"https://{h1.IP()}:4433/index.html " # Arquivo normal
        f"https://{h1.IP()}:4433/largefile --output-file /dev/null " # Arquivo grande
    )

    print(f"Iniciando fluxo QUIC (long) em h2: {client_cmd}")

    # Execute de forma assíncrona para “rodar em paralelo”
    proc = h2.popen(client_cmd, shell=True)

    # Fluxos extras para aumentar o tráfego
    for _ in range(4):  # 4 fluxos simultâneos
        h2.popen(client_cmd, shell=True)
 
    return proc

# -----------------------------------------------------------------------------
# Webserver via QUIC (opcional, se quiser manter algo tipo webserver.py)
# -----------------------------------------------------------------------------
# Aqui, se você quiser continuar usando seu "webserver.py" clássico em TCP, ignore.
# Se quiser adaptá-lo para QUIC, teria de reescrever em aioquic. Mas,
# como já temos um servidor QUIC acima, podemos usá-lo como "webserver QUIC".

def start_webserver_quic(net):
    """
    (Opcional) Se quiser rodar algo estilo 'webserver.py',
    mas em QUIC, você pode adaptá-lo ou usar o start_quic_server acima.
    """
    pass

# -----------------------------------------------------------------------------
# Função principal do experimento
# -----------------------------------------------------------------------------
def bufferbloat_quic():
    print(args)
    if not os.path.exists(args.dir):
        os.makedirs(args.dir)

    # Ajusta congestion control do TCP no SO, embora não vá afetar QUIC diretamente.
    os.system(f"sysctl -w net.ipv4.tcp_congestion_control={args.cong}")

    # Constrói e inicia a topologia
    topo = BBTopo()
    net = Mininet(topo=topo, host=CPULimitedHost, link=TCLink)
    net.start()

    # Dump das conexões e teste de ping inicial
    dumpNodeConnections(net.hosts)
    net.pingAll()

    # Inicia monitor de fila na interface do gargalo. 
    # Verifique se é s0-eth1 ou s0-eth2 dependendo da ordem de criação do link.
    # Aqui, assumiremos s0-eth2 novamente.
    qmon = start_qmon(iface='s0-eth2', outfile=f'{args.dir}/q.txt')

    # Inicia servidor QUIC em h1
    quic_server_proc = start_quic_server(net)

    # Inicia fluxo QUIC longo (substituindo iperf) em h2
    quic_long_flow_proc = start_quic_long_flow(net)

    # Inicia ping para medir RTT
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
            f"--output-file /dev/null"
        )
        print(f"Teste de fetch QUIC {i+1} -> {cmd}")

        start_t = time()
        h2.cmd(cmd)
        end_t = time()

        fetch_time = end_t - start_t
        fetch_times.append(fetch_time)
        print(f"[Fetch {i+1}] Tempo: {fetch_time:.4f} s")
        sleep(2)

    # Calcula média e desvio-padrão
    avg_fetch_time = sum(fetch_times) / len(fetch_times)
    variance = sum((x - avg_fetch_time) ** 2 for x in fetch_times) / len(fetch_times)
    stddev_fetch_time = math.sqrt(variance)
    print(f"Average page fetch time (QUIC): {avg_fetch_time:.2f} s")
    print(f"Standard deviation: {stddev_fetch_time:.2f} s")

    # Aguarda experimento terminar
    sleep(args.time)

    # Encerra monitor de fila
    qmon.terminate()

    # Encerra processos QUIC (caso ainda estejam rodando)
    quic_long_flow_proc.terminate()
    quic_server_proc.terminate()

    # Para evitar processos em segundo plano
    Popen("pgrep -f http3_server | xargs kill -9", shell=True).wait()

    net.stop()

# -----------------------------------------------------------------------------
# Execução
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    bufferbloat_quic()
