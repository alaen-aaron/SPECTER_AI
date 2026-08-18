import socket
try:
    s = socket.create_connection(("172.18.0.1", 3001), 5)
    print("REACHABLE")
    s.close()
except Exception as e:
    print("FAIL", e)