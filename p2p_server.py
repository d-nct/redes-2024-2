import asyncio
from aioquic.asyncio import serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.events import StreamDataReceived, HandshakeCompleted

clients = {}

class QUICServerProtocol(QuicConnectionProtocol):
    def quic_event_received(self, event):
        if isinstance(event, HandshakeCompleted):
            client_ip = self._quic._peer_address[0]
            print(f"Client {client_ip} connected.")
            clients[client_ip] = self

        elif isinstance(event, StreamDataReceived):
            client_ip = self._quic._peer_address[0]
            message = event.data.decode()

            print(f"Received from {client_ip}: {message}")

            # Parse message for forwarding (format: <recipient_ip>:<message>)
            recipient_ip, actual_message = message.split(":", 1)
            if recipient_ip in clients:
                print(f"Forwarding message to {recipient_ip}")
                clients[recipient_ip]._quic.send_stream_data(
                    event.stream_id, f"Message from {client_ip}: {actual_message}".encode()
                )
            else:
                print(f"Recipient {recipient_ip} not found.")
                self._quic.send_stream_data(event.stream_id, b"Recipient not connected.\n")

async def main():
    await serve(
        host="0.0.0.0",
        port=12345,
        configuration={
            "alpn_protocols": ["hq-29"],
            "certfile": "cert.pem",  # Replace with your certificate
            "keyfile": "key.pem",   # Replace with your private key
        },
        create_protocol=QUICServerProtocol,
    )

if __name__ == "__main__":
    asyncio.run(main())

