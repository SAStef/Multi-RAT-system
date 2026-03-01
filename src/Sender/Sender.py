import socket as s  
import os
sock = s.socket(s.AF_INET, s.SOCK_DGRAM)

ip = "10.209.154.26"
port = 6967

a = 0
while a<8:
    Payload = (os.urandom(8))
    sock.sendto(b"A" + Payload, (ip, port))
    sock.sendto(b"B" + Payload, (ip, port))
    a+=1




sock.close()