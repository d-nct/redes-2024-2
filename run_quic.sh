#!/bin/bash

# Note: Mininet must be run as root.  So invoke this shell script
# using sudo.

time=90
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
    python3 bufferbloat.py \
        --bw-host $bw_host \
        --bw-net $bwnet \
        --delay $delay \
        --dir $dir \
        --time $time \
        --maxq $qsize \
        # --cong bbr   # Isso é só do TCP


    # TODO: Ensure the input file names match the ones you use in
    # bufferbloat.py script.  Also ensure the plot file names match
    # the required naming convention when submitting your tarball.
    python3 plot_queue.py -f $dir/q.txt -o quic-buffer-q$qsize.png
    python3 plot_ping.py -f $dir/ping.txt -o quic-rtt-q$qsize.png
done
