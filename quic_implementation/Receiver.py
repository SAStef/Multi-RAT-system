import socket as s

def init_receiving():
    
    
    try:
        Port1 = 2299
        port2 = 2300
    
        sock1 = s.connect()
        
        while True:
            print("hello");
            
            
    except:
        print("error")
        
if __name__ == "__main__":
    init_receiving()