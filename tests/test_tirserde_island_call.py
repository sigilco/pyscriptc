"""An island call keeps its island_* keys through the typed round trip."""

from pyscriptc.tirserde import dict_to_node, node_to_dict


def test_island_call_round_trips_losslessly() -> None:
    call = {
        "kind": "call",
        "callee": {"kind": "module", "module": "island", "name": "f"},
        "args": [],
        "type": {"kind": "int"},
        "symbol": "ps_island_fn_invoke",
        "island_fn": {"name": "f"},
        "island_npos": 0,
        "island_nkw": 0,
        "span": {"file": "a.py", "line": 1, "col": 0, "endLine": 1, "endCol": 1},
    }
    stmt = {"kind": "expr", "expr": call, "span": {"file": "a.py", "line": 1, "col": 0, "endLine": 1, "endCol": 1}}
    assert node_to_dict(dict_to_node(stmt)) == stmt


def test_print_keywords_round_trip() -> None:
    span = {"file": "p.py", "line": 1, "col": 0, "endLine": 1, "endCol": 1}
    sep = {"kind": "const", "value": "-", "type": {"kind": "str"}, "span": span}
    call_d = {
        "kind": "call",
        "callee": {"kind": "builtin", "name": "print"},
        "args": [],
        "type": {"kind": "none"},
        "span": span,
        "sep": sep,
        "file": "stderr",
    }
    out = node_to_dict(dict_to_node(call_d))
    assert out["sep"] == sep
    assert out["file"] == "stderr"
