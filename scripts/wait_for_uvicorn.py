import time, urllib.request
import sys

print("Waiting for microservice to load on port 8435...")
t0 = time.time()
while time.time() - t0 < 120:
    try:
        urllib.request.urlopen('http://localhost:8435/')
        print("READY")
        sys.exit(0)
    except Exception as e:
        if '405' in str(e) or '404' in str(e):
            print("READY")
            sys.exit(0)
    time.sleep(2)
print("Timeout waiting for microservice.")
sys.exit(1)
