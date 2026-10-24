import dotenv

vals: dict[str, str | None] = dotenv.dotenv_values("d16.env")
print(vals["PLAIN"])
print(vals["QUOTED"])
print("" if vals["EMPTY"] is None else vals["EMPTY"])
