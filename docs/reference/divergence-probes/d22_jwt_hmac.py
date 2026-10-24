import jwt

tok: str = jwt.encode({"user": "kim"}, "secret", algorithm="HS256")
print(len(tok) > 0)
claims: dict[str, str] = jwt.decode(tok, "secret", ["HS256"])
print(claims["user"])
