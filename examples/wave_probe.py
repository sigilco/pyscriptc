"""Codec-wave checks. `probe` returns 0 when the lowered calls match CPython."""

import base64
import hashlib
import json
import re
import struct
import urllib
import urllib.parse as up


def probe() -> int:
    if re.findall(r"\d+", "a12b3")[0] != "12":
        return 1
    if re.findall(r"\d+", "a12b3")[1] != "3":
        return 2
    if re.sub(r"(a)(b)", r"\2\1", "ab") != "ba":
        return 3
    if re.sub(r"a*", "X", "ba") != "XbXX":
        return 4
    if re.sub("", "x", "ab") != "xaxbx":
        return 5
    if re.split(r"(\d)", "a1b2")[3] != "2":
        return 6
    if re.split(r"(\d)", "a1b2")[4] != "":
        return 7
    if re.escape("a.b*c_d") != r"a\.b\*c_d":
        return 8
    if re.compile("a+").findall("baa")[0] != "aa":
        return 9
    if re.compile("a.b").pattern != "a.b":
        return 10
    number: int = json.loads("-12")
    if number != -12:
        return 11
    table: dict[str, int] = json.loads('{"a": 1, "a": 2}')
    if table["a"] != 2:
        return 12
    flag: bool = json.loads("true")
    if not flag:
        return 13
    if json.dumps(table) != '{"a": 2}':
        return 14
    if json.dumps([1, 2]) != "[1, 2]":
        return 15
    if json.dumps(True) != "true":
        return 16
    if up.quote("a b/c") != "a%20b/c":
        return 17
    if urllib.parse.quote_plus("a b/c") != "a+b%2Fc":
        return 18
    if urllib.parse.unquote_plus("a+b%2Fc") != "a b/c":
        return 19
    if urllib.parse.urlencode({"a": "1", "b": "2"}) != "a=1&b=2":
        return 20
    parsed: dict[str, list[str]] = urllib.parse.parse_qs("a=1&b=&c=2;a=3")
    if "b" in parsed:
        return 21
    if parsed["c"][0] != "2;a=3":
        return 22
    again: dict[str, list[str]] = urllib.parse.parse_qs("a=1&a=2")
    if again["a"][1] != "2":
        return 24
    if urllib.parse.urlsplit("http://h/p?q=1#f").path != "/p":
        return 25
    if urllib.parse.urlsplit("http://h/p?q=1#f").scheme != "http":
        return 26
    if urllib.parse.urlsplit("http://h/p?q=1#f").query != "q=1":
        return 27
    if base64.b64encode(b"hi") != b"aGk=":
        return 28
    if base64.b64decode(base64.b64encode(b"hi")) != b"hi":
        return 29
    if base64.urlsafe_b64encode(b"\xfb\xff") != b"-_8=":
        return 30
    if base64.urlsafe_b64decode(b"-_8=") != b"\xfb\xff":
        return 31
    if hashlib.sha256(b"abc").hexdigest() != "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad":
        return 32
    if hashlib.md5(b"abc").hexdigest() != "900150983cd24fb0d6963f7d28e17f72":
        return 33
    packed: bytes = struct.pack("<HH", 1, 2)
    if struct.unpack("<HH", packed)[0] != 1:
        return 34
    if struct.unpack("<HH", packed)[1] != 2:
        return 35
    if struct.unpack("<3s", struct.pack("<3s", b"ab"))[0] != b"ab\x00":
        return 36
    if struct.unpack(">H", struct.pack(">H", 1))[0] != 1:
        return 37
    if struct.unpack("<d", struct.pack("<d", 1.5))[0] != 1.5:
        return 38
    return 0
