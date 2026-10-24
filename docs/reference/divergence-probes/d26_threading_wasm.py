import threading

lock: threading.Lock = threading.Lock()
with lock:
    print("locked")
