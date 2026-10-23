# Empty marker: jwtapp is a package (relative imports between siblings), not
# a program directory. Without it, a root main.py would make pyscriptc infer
# the `python DIR` program shape, where a plain sibling refuses a relative
# import the way CPython refuses it in a script (`no known parent package`).
