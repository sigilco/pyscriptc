open("nl.txt", "w").write("a\r\nb")
print(repr(open("nl.txt", newline="").read()))
