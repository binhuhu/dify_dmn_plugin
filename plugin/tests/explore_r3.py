"""Random interleaving explorer (greenlets): every storage call of every process is a scheduling point. After all
processes end, oracles check the residual-limit claim: no 200 lost silently, no half state, never unreadable and
undoable-neither, no standard-name key left under OLD."""

import copy
import json
import random
import sys

from greenlet import greenlet
from test_review_attacks import next_body
from test_v0520_invariants import Faulty, N, body_of, copied, legacy_faulty
from test_v0520_store_name import CLOCK, OLD, bump, five, legacy

from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError, StorageTables


class Sched:
    def __init__(self, data, rnd):
        self.data, self.rnd, self.main = data, rnd, greenlet.getcurrent()

    def run(self, procs):
        """procs: list of (name, fn(storage)). Returns {name: result or ('err', code)}."""
        out, live = {}, {}
        for name, fn in procs:
            st = Stepper(self.data, self)

            def body(fn=fn, st=st, name=name):
                try:
                    out[name] = ("ok", fn(st))
                except RouteTableError as exc:
                    out[name] = ("err", exc.code)
                except Exception as exc:  # noqa: BLE001
                    out[name] = ("exc", repr(exc)[:80])

            live[name] = greenlet(body)
        while live:
            name = self.rnd.choice(sorted(live))
            live[name].switch()
            if live[name].dead:
                del live[name]
        return out


class Stepper(Faulty):
    def __init__(self, data, sched):
        super().__init__(data)
        self.sched = sched

    def _yield(self):
        self.sched.main.switch()

    def exist(self, key):
        self._yield()
        return super().exist(key)

    def get(self, key):
        self._yield()
        return super().get(key)

    def set(self, key, val):
        self._yield()
        super().set(key, val)

    def delete(self, key):
        self._yield()
        super().delete(key)


def T(st):
    return StorageTables(st, now=CLOCK)


def doc_of(data, name):
    st, out = run_op(Faulty(data), "table", name)
    return st, out


def oracles(data, std, results, saves):
    """saves: {proc name: table name saved}. Returns a list of violations."""
    bad = []
    st_s, s_out = doc_of(data, std)
    st_o, o_out = doc_of(data, OLD[std])
    readable = st_s == 200 and st_o == 200
    if not readable:
        try:
            head = (
                json.loads(data["route-table:" + std])["version"]
                if ("route-table:" + std) in data
                else None
            )
        except Exception:  # noqa: BLE001
            head = None
        ok = False
        for v in {head, N, json.loads(data["route-table:" + OLD[std]])["version"]} - {None}:
            if run_op(Faulty(copy.deepcopy(data)), "undo", std, str(v))[0] == 200:
                ok = True
        if not ok:
            bad.append(("neither readable nor undoable", st_s, s_out.get("error")))
        return bad
    if (s_out["doc"] or {}).get("save_id") != (o_out["doc"] or {}).get("save_id"):
        bad.append(("names read different heads", s_out["version"], o_out["version"]))
    head = s_out["doc"] or {}
    hv = head.get("version")
    st_v, v_out = run_op(Faulty(data), "version", std, str(hv)) if hv else (404, {})
    if st_v == 200 and v_out["doc"].get("save_id") != head.get("save_id"):
        bad.append(
            ("head is not its own version key", hv, head.get("note"), v_out["doc"].get("note"))
        )
    for name, table in saves.items():
        r = results.get(name)
        if not r or r[0] != "ok":
            continue
        saved = r[1]
        k = saved["version"]
        if hv is None or hv < k:
            bad.append(("acked save above the head", name, k, hv))
            continue
        st_k, k_out = run_op(Faulty(data), "version", std, str(k))
        if st_k != 200 or k_out["doc"].get("save_id") != saved["save_id"]:
            if not (k == N and "supersedes" in saved):  # a copy replaced by a twin's identical copy
                bad.append(
                    ("acked version not in the chain", name, k, k_out.get("doc", {}).get("note"))
                )
    rec = data.get("route-table:%s:first" % std)
    state = json.loads(rec)["state"] if rec else "OLD"
    if state == "OLD":
        left = run_op(Faulty(data), "check", std)[1].get("standard_keys")
        if left:
            bad.append(("standard keys left under OLD", left))
    return bad


def scenario(kind, std, rnd):
    if kind in ("save-save", "save-undo", "save-save-undo", "undo-undo", "copyretry-save"):
        s = copied(std)
    else:
        s = legacy_faulty()
    data = s.data
    b1 = dict(next_body(s, std), note="P3")
    b2 = dict(bump(next_body(s, std)), note="P4")
    cb = body_of(s, std)
    old_b = legacy(bump(bump(five()[std])))
    procs, saves = [], {}
    if kind == "save-save":
        procs = [
            ("P3", lambda st: T(st).save(copy.deepcopy(b1), N, new_id=lambda: "3" * 32)),
            ("P4", lambda st: T(st).save(copy.deepcopy(b2), N, new_id=lambda: "4" * 32)),
        ]
        saves = {"P3": std, "P4": std}
    elif kind == "save-undo":
        procs = [
            ("P3", lambda st: T(st).save(copy.deepcopy(b1), N, new_id=lambda: "3" * 32)),
            ("U", lambda st: T(st).undo(std, N, new_id=lambda: "u" * 32)),
        ]
        saves = {"P3": std}
    elif kind == "save-save-undo":
        procs = [
            ("P3", lambda st: T(st).save(copy.deepcopy(b1), N, new_id=lambda: "3" * 32)),
            ("P4", lambda st: T(st).save(copy.deepcopy(b2), N, new_id=lambda: "4" * 32)),
            ("U", lambda st: T(st).undo(std, N, new_id=lambda: "u" * 32)),
        ]
        saves = {"P3": std, "P4": std}
    elif kind == "undo-undo":
        procs = [
            ("U1", lambda st: T(st).undo(std, N, new_id=lambda: "1" * 32)),
            ("U2", lambda st: T(st).undo(std, N, new_id=lambda: "2" * 32)),
        ]
    elif kind == "copy-copy":
        procs = [
            ("A", lambda st: T(st).save(copy.deepcopy(cb), N, new_id=lambda: "a" * 32)),
            ("B", lambda st: T(st).save(copy.deepcopy(cb), N, new_id=lambda: "b" * 32)),
        ]
        saves = {"A": std, "B": std}
    elif kind == "copy-old":
        procs = [
            ("A", lambda st: T(st).save(copy.deepcopy(cb), N, new_id=lambda: "a" * 32)),
            ("O1", lambda st: T(st).save(copy.deepcopy(old_b), N, new_id=lambda: "o" * 32)),
        ]
        saves = {"A": std, "O1": OLD[std]}
    elif kind == "copy-undo":
        procs = [
            ("A", lambda st: T(st).save(copy.deepcopy(cb), N, new_id=lambda: "a" * 32)),
            ("U", lambda st: T(st).undo(std, N, new_id=lambda: "u" * 32)),
        ]
        saves = {"A": std}
    elif kind == "copy-copy-save":

        def chain(st):
            T(st).save(copy.deepcopy(cb), N, new_id=lambda: "a" * 32)
            return T(st).save(copy.deepcopy(b1), N, new_id=lambda: "3" * 32)

        procs = [
            ("A+P3", chain),
            ("B", lambda st: T(st).save(copy.deepcopy(cb), N, new_id=lambda: "b" * 32)),
        ]
        saves = {"A+P3": std, "B": std}
    results = Sched(data, rnd).run(procs)
    # an old-name save answered 200 is a save of the table too (it is read through both names while OLD)
    return data, results, saves


def main(trials, seed, kinds, stds):
    found = {}
    for kind in kinds:
        for std in stds:
            for t in range(trials):
                rnd = random.Random(hash((seed, kind, std, t)))
                data, results, saves = scenario(kind, std, rnd)
                bad = oracles(data, std, results, saves)
                if bad:
                    key = (kind, bad[0][0])
                    if key not in found:
                        found[key] = (std, t, results, bad)
    return found


if __name__ == "__main__":
    kinds = (
        sys.argv[3].split(",")
        if len(sys.argv) > 3
        else [
            "save-save",
            "save-undo",
            "save-save-undo",
            "undo-undo",
            "copy-copy",
            "copy-old",
            "copy-undo",
            "copy-copy-save",
        ]
    )
    stds = sys.argv[4].split(",") if len(sys.argv) > 4 else ["triage.dispatch.resource"]
    found = main(int(sys.argv[1]), int(sys.argv[2]), kinds, stds)
    for (kind, what), (std, t, results, bad) in sorted(found.items()):
        print(kind, "|", what, "|", std, "trial", t)
        print(
            "   results:",
            {
                k: (
                    v[0],
                    v[1]
                    if v[0] != "ok"
                    else v[1].get("version")
                    if isinstance(v[1], dict)
                    else str(v[1])[:40],
                )
                for k, v in results.items()
            },
        )
        print("   ", bad[:3])
    print("done", len(found))
