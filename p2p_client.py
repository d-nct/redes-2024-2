import asyncio
import sys
import time
from aioquic.asyncio import connect
from aioquic.quic.events import StreamDataReceived

class QUICClientProtocol:
    def quic_event_received(self, event):
        if isinstance(event, StreamDataReceived):
            print(f"Received: {event.data.decode()}")

async def main(server_ip, recipient_ip, duration):
    async with connect(
        host=server_ip,
        port=12345,
        configuration={"alpn_protocols": ["hq-29"]},
        create_protocol=QUICClientProtocol,
    ) as client:
        stream_id = client._quic.get_next_available_stream_id()
        print(f"Connected to server at {server_ip}, sending messages to {recipient_ip} for {duration} seconds.")

        start_time = time.time()
        while time.time() - start_time < duration:
            message = f"Hello to {recipient_ip} at {time.time()}"
            print(f"Sending: {message}")
            client._quic.send_stream_data(stream_id, f"{recipient_ip}:{message}".encode())
            await asyncio.sleep(0.1)  # Send a message every 1 second

        print("Finished sending messages.")

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python3 quic_client.py <server_ip> <recipient_ip> <duration>")
        sys.exit(1)

    server_ip = sys.argv[1]
    recipient_ip = sys.argv[2]
    duration = int(sys.argv[3])
    asyncio.run(main(server_ip, recipient_ip, duration))

