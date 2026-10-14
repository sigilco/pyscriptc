parts: list[str] = []
for i in range(300):
    parts.append(str(i * i))
joined = ",".join(parts)
print(len(joined), joined[:30], joined[-12:])
print(joined.count("9"), joined.upper().startswith("0,1,4"))
print("  padded  ".strip() + "|" + "ab".center(6, "*"))
