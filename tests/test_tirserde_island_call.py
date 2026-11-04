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
