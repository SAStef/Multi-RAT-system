#!/usr/bin/env python3
import asyncio
import random
import ssl
from aioquic.asyncio import connect
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import StreamDataReceived
from aioquic.asyncio.protocol import QuicConnectionProtocol

SERVER = "127.0.0.1"
PORTS = [6769, 6967]

class ClientProtocol(QuicConnectionProtocol):
    def quic_event_received(self, event):
        if isinstance(event, StreamDataReceived):
            print(f"Client ACK: {event.data.decode()}")

async def send_messages(port, count):
    config = QuicConfiguration(is_client=True, verify_mode=ssl.CERT_NONE)
    config.alpn_protocols = ["test"]
    print(f"Connecting to {SERVER}:{port}...")
    async with connect(
        host=SERVER,
        port=port,
        configuration=config,
        create_protocol=ClientProtocol,
    ) as client:
        print(f"Connected on port {port}")
        stream_id = client._quic.get_next_available_stream_id()
        for i in range(count):
            msg = f"Msg {i+1}: {random.randint(100,999)}"
            client._quic.send_stream_data(stream_id, msg.encode(), end_stream=False)
            client.transmit()
            print(f"Sent on {port}: {msg}")
            await asyncio.sleep(0.5)
        await asyncio.sleep(1)

async def main():
    count = int(input("Messages per connection? [5]: ") or 5)
    tasks = [asyncio.create_task(send_messages(p, count)) for p in PORTS]
    await asyncio.gather(*tasks)
    print("All done.")

if __name__ == "__main__":
    asyncio.run(main())