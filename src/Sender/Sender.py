import socket as s
import os
sock = s.socket(s.AF_INET, s.SOCK_DGRAM)

ip = "192.38.81.6"
port = 6967

a = 0
while a<8:
    message = os.urandom(8)
    sock.sendto(message.encode(), (ip, port))
    a+=1






















sock.close()