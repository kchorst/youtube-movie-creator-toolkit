import socket

try:
    # Try to create a socket and connect to a well-known host (Google's IP)
    socket.create_connection(("8.8.8.8", 53), timeout=5) # DNS server, common to be open
    print("Python can successfully connect to the internet.")
except socket.timeout:
    print("Python connection timed out. Network or firewall issue suspected.")
except Exception as e:
    print(f"Python encountered an error connecting to the internet: {e}")
