import socket as s
sock = s.socket(s.AF_INET, s.SOCK_DGRAM)

ip = "192.38.81.6"
port = 6967
message = "Hello World"
sock.sendto(message.encode(), (ip, port))






















sock.close()