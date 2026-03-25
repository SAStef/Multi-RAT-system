#!/usr/bin/env python3
import asyncio
from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import StreamDataReceived

HOST = "127.0.0.1"
PORTS = [6769, 6967]
CERT_FILE = "cert.pem"
KEY_FILE = "key.pem"

class EchoProtocol(QuicConnectionProtocol):
    def quic_event_received(self, event):
        if isinstance(event, StreamDataReceived):
            data = event.data.decode()
            print(f"Server received: {data}")
            self._quic.send_stream_data(event.stream_id, b"ACK: " + event.data)
            self.transmit()

async def run_server(port):
    config = QuicConfiguration(is_client=False)
    config.load_cert_chain(CERT_FILE, KEY_FILE)
    config.alpn_protocols = ["test"]
    await serve(host=HOST, port=port, configuration=config, create_protocol=EchoProtocol)
    print(f"Server listening on {HOST}:{port}")

async def main():
    await asyncio.gather(*(run_server(p) for p in PORTS))
    print(f"Redundant servers running on ports {PORTS}. Press Ctrl+C to stop.")
    await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
