#!/bin/bash

# Note: Mininet must be run as root.  So invoke this shell script
# using sudo.

time=30
bwnet=1.5
#bwnet=0.1
# TODO: If you want the RTT to be 20ms what should the delay on each
# link be?  Set this value correctly.

bw_host=1000
delay=5
#delay=100

# iperf_port=5001 # Não usamos mais o iperf

for qsize in 20 100; do
    dir=bb-q$qsize
    
    # Create output directory if it doesn't exist

    mkdir -p $dir


    # TODO: Run bufferbloat.py here...
    python3 bufferbloat_p5.py \
        --bw-host $bw_host \
        --bw-net $bwnet \
        --delay $delay \
        --dir ./results_p5_quic \
        --time $time \
        --maxq $qsize \
        --cong reno


    # TODO: Ensure the input file names match the ones you use in
    # bufferbloat.py script.  Also ensure the plot file names match
    # the required naming convention when submitting your tarball.
    python3 plot_queue.py -f $dir/q.txt -o results_p5_quic/p5-bbr-buffer-q$qsize.png
    python3 plot_ping.py -f $dir/ping.txt -o results_p5_quic/p5-bbr-rtt-q$qsize.png
done
