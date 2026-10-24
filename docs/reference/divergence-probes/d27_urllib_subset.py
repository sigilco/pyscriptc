from urllib.request import Request, urlopen

req: Request = Request("http://127.0.0.1:9/never", headers={"X-Key": "v"})
try:
    urlopen(req)
except OSError:
    print("OSError")
