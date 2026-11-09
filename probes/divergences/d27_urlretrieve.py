import urllib.request

urllib.request.urlretrieve("file:///nonexistent-path", "out.bin")
print("done")
