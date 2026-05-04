"""Smoke tests for the concurrent-append lock and the project_id requirement.

Uses threads (not multiprocessing.Pool — Windows fork-spawn re-imports the
script and recurses).
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(HERE))

from sink.events import EventLog, _append_lock, read_log
from sink.manifest import Manifest


def section(name): print(f"\n=== {name} ===")


# ---------------- project_id is required ----------------

section("missing project_id raises a helpful error")
with tempfile.TemporaryDirectory() as td:
    bad = Path(td) / "coordination.yaml"
    bad.write_text(
        "project: test\n"
        "version: 1\n"
        "transport: {type: shared-fs, path: /tmp}\n"
        "agents:\n  host: {role: x}\n"
    )
    try:
        Manifest.load(bad)
    except ValueError as e:
        msg = str(e)
        assert "project_id" in msg and "coordination.yaml" in msg
        print(f"OK: rejected with helpful message:")
        print(f"     {msg.splitlines()[0]}")
    else:
        print("FAIL: should have raised")
        sys.exit(1)


section("present project_id loads cleanly")
with tempfile.TemporaryDirectory() as td:
    good = Path(td) / "coordination.yaml"
    good.write_text(
        "project: test\n"
        "project_id: 11111111-2222-3333-4444-555555555555\n"
        "version: 2\n"
        "transport: {type: shared-fs, path: /tmp}\n"
        "agents:\n  host: {role: x}\n"
    )
    m = Manifest.load(good)
    assert m.project_id == "11111111-2222-3333-4444-555555555555"
    print(f"OK: project_id stable: {m.project_id}")


# ---------------- concurrent appends serialize ----------------

section("4 threads x 25 concurrent appends produce monotonic, unique seqs")
with tempfile.TemporaryDirectory() as td:
    log_path = Path(td) / "host.jsonl"
    n_per = 25
    n_threads = 4

    def hammer(label):
        log = EventLog(log_path, "host")
        for i in range(n_per):
            log.append("log", payload={"label": label, "i": i})

    threads = [threading.Thread(target=hammer, args=(f"T{i}",)) for i in range(n_threads)]
    for t in threads: t.start()
    for t in threads: t.join()

    events = read_log(log_path)
    seqs = [e.seq for e in events]
    expected = list(range(n_per * n_threads))
    assert len(events) == n_per * n_threads, f"expected {n_per * n_threads} events, got {len(events)}"
    assert sorted(seqs) == expected, f"seqs not contiguous; gaps/dupes: {[s for s in expected if s not in seqs]}"
    assert len(set(seqs)) == len(seqs), f"duplicate seqs: {len(seqs) - len(set(seqs))} dupes"
    print(f"OK: {len(events)} events, seqs 0..{max(seqs)} all unique")


section("file lock holds exclusive mutex (5 short critical sections serialize)")
with tempfile.TemporaryDirectory() as td:
    target = Path(td) / "thing.txt"
    target.write_text("")
    order = []
    def worker(label):
        with _append_lock(target):
            order.append(("enter", label))
            time.sleep(0.05)
            order.append(("exit", label))
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()
    interleaved = False
    for i in range(0, len(order), 2):
        if order[i][0] != "enter" or order[i+1][0] != "exit" or order[i][1] != order[i+1][1]:
            interleaved = True
            break
    assert not interleaved, f"lock did NOT serialize: {order}"
    print(f"OK: 5 critical sections serialized cleanly")


print("\nAll checks passed.")
