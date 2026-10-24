from urllib.request import urlopen

try:
    urlopen("http://127.0.0.1:9/never")
except OSError:
    print("OSError")
