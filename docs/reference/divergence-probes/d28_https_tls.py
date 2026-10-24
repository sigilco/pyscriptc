from urllib.request import urlopen

try:
    urlopen("https://127.0.0.1:9/x")
except OSError:
    print("OSError")
