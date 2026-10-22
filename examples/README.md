# Examples

Programs a person can compile are documented in [Example programs](https://pyscriptc.pages.dev/examples/programs/), with the command and the value each one prints.

| File | Run |
| --- | --- |
| `invoice.py` | `--call charge --args '[10, 2, 50]'` prints `600` |
| `access_log.py` | `--call admit` on a JSON object of strings prints `0` or `1` |
| `request_id.py` | `--call request_id --args '["id=88421 status=500"]'` prints `88421` |
| `release_note.py` | `--call stamp` writes an ISO date and prints it |
| `blob_digest.py` | `--call fingerprint --args '[1, 2]'` prints a SHA-256 hex digest |
| `url_gate.py` | `--call allow` prints `1` or `0` |
| `route_label.py` | `--call label --args '["/api/health", 201]'` prints `health 201` |
| `hostname.py` | `--call hostname` prints the machine name and links libpython |
| `wordcount/` | a program directory: `pyscriptc compile examples/wordcount --run` prints `three 3` |

The other files here are the compiler corpus: `gates_*.py`, `wave_probe.py`, `rest_probe.py`, `sum_squares.py`, and the `fence_*.py` programs that are supposed to be refused.
