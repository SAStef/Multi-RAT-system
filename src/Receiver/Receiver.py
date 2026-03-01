import socket as s

PORT = 6967
sock = s.socket(s.AF_INET, s.SOCK_DGRAM)
sock.bind(("0.0.0.0", PORT))

print(f"Listening on UDP {PORT}...")

while True:
    data, addr = sock.recvfrom(2048)
    tag = data[0:1]          
    payload = data[1:]
    print(addr, tag, payload.hex())