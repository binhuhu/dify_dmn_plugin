"""Trial share, table format: a solution row of a v3 route table may carry one trial {app_id, share, note?}.
A table without trials validates, digests, stores and compiles as before (baselines taken on df817f3)."""

import copy
import hashlib

import pytest
from route_trial_fixtures import ALPHA, ALPHA_ROW, DELTA, TRANSFER_ROW, base, row_index, with_trial
from test_route_table import FakeStorage
from test_route_table_v3 import stub
from test_route_table_v3 import v3 as big
from toy_route_fixtures import BETA, GAMMA
from toy_route_fixtures import v3 as toy

from dmn_client.route_table import (
    FORMAT_V2,
    FORMAT_V3,
    FORMAT_V4,
    MAX_ROUTES,
    SCHEMA_V2,
    TRIAL_MAX_SHARE,
    TRIAL_MIN_SHARE,
    RouteTableError,
    StorageTables,
    canon,
    content_sha256,
    materialize,
    validate_doc,
)


def sha(value):
    return hashlib.sha256(canon(value).encode()).hexdigest()


# Computed on df817f3 (the commit this work starts from) by the function below, before any trial code existed.
BASELINE = {
    "toy.clean": "2b54e8d3d084683f23b9d004079149d673512608eba29dbd5e91ea4a7a644e19",
    "toy.content": "0df4119cc84659d3869efebde90c274c82ba5653e7cf86da53f8238fe3e075db",
    "toy.model.routing-table/v2": "2f60431102218b8bbacb070bc269c0c4a4acaac1d3632802b284e9cd0d6cf1bb",
    "toy.source.routing-table/v2": "c20fbfd5d7d2171c7f9d18eb2e5deebf80c3707c58a4aaf53498665eb2aa2b6b",
    "toy.model.routing-table/v3": "d116e5c577a1ef1a698f54d760da054e9cac3e7d567b50ed18db8df4a8b45b4d",
    "toy.source.routing-table/v3": "79eceb777e4026a532ad07def2894292b0d6ee28c75599032c172253df76370e",
    "toy.model.routing-table/v4": "4fb70b2a1aa17c2134bc4f36f390814725627ea26bcbe18e9e409cdbaf2cd87a",
    "toy.source.routing-table/v4": "043153c784d70189b078be413e4766f9e8f5ba2a77f091a5f640b1c00b3b44c1",
    "toy.stored": "2883d8b23eed0d836eeb2287fddf969d6d1c03bb1c6fb23ac544f097e0f5342f",
    "big.clean": "bc71f9001a697d7e4b890225d24671e732355f32deaf348819265a4d6e181308",
    "big.content": "4794e9ddeea1a40157fea2a92d4014dae74438cfa1ecac3c09129bef015a963e",
    "big.model.routing-table/v2": "8d6b3e5ade99eb0c889f6002c9af5b9e21d4331a68c896116b34a2e1744c90f4",
    "big.source.routing-table/v2": "6b100fd90b24f4a3dfb644583175de75b7d5a4f095d3c0c9bc7f34b6a9405eb0",
    "big.model.routing-table/v3": "411b90766697e4ddbae35bbe6c63a19467978f694f82141101d2db0d1c8de094",
    "big.source.routing-table/v3": "42384428a4d2a1fc170250037cd5fb3ea3fca9c4d6b6a1b2795a0c3e59a62102",
    "big.model.routing-table/v4": "c9581a357de2ee6e7560c2f236a6de6e3e752a5e3c58231f43d51265d4ad038c",
    "big.source.routing-table/v4": "77782d2f7290868654e5c7a861fbb62782fdd741d002a6d8a556eac8b37fedf5",
    "big.stored": "e1f57c07a710f948896c289642033f66cd6adac436f2d34d95af789300ad8e46",
}


def fingerprint():
    out = {}
    for name, d in (("toy", toy()), ("big", big())):
        clean = validate_doc(d)
        out[name + ".clean"] = sha(clean)
        out[name + ".content"] = content_sha256(clean)
        st = StorageTables(FakeStorage())
        st.save(d, 0)
        for fmt in (FORMAT_V2, FORMAT_V3, FORMAT_V4):
            bundle, sources = materialize(stub(fmt), st.load)
            out[name + ".model." + fmt] = sha(bundle["models"]["dispatch.route@4.0.0"])
            out[name + ".source." + fmt] = sha(
                [{k: v for k, v in s.items() if k != "saved_at"} for s in sources]
            )
        stored = st.load("dispatch.route")
        out[name + ".stored"] = sha(
            {k: v for k, v in stored.items() if k not in ("saved_at", "save_id")}
        )
    return out


def test_a_table_without_trials_is_what_it_was_byte_for_byte():
    assert fingerprint() == BASELINE


def refused(doc, code, path=None):
    with pytest.raises(RouteTableError) as e:
        validate_doc(doc)
    assert e.value.code == code, (e.value.code, e.value.message)
    if path is not None:
        assert e.value.path == path, e.value.path
    return e.value


def test_a_trial_is_stored_on_its_row_and_nowhere_else():
    d = with_trial(share=10, note="  试新版分拣  ")
    clean = validate_doc(d)
    rows = {(r["scene_id"], r["issue_type"], r["phase"]): r for r in clean["routes"]}
    assert rows[ALPHA_ROW] == {
        "scene_id": ALPHA_ROW[0],
        "issue_type": "COMPLAINT",
        "phase": "STAGE_1",
        "target": {"kind": "SOLUTION_APP", "app_id": ALPHA},
        "trial": {"app_id": DELTA, "share": 10, "note": "试新版分拣"},
    }
    assert all("trial" not in r for k, r in rows.items() if k != ALPHA_ROW)
    assert validate_doc(clean) == clean, "the stored shape validates to itself"
    assert "note" not in validate_doc(with_trial())["routes"][row_index(clean)]["trial"]


def test_the_content_digest_follows_the_trial():
    plain = content_sha256(validate_doc(base()))
    ten = content_sha256(validate_doc(with_trial(share=10)))
    assert len({plain, ten, content_sha256(validate_doc(with_trial(share=11)))}) == 3
    assert content_sha256(validate_doc(with_trial(share=10))) == ten


@pytest.mark.parametrize("share", [TRIAL_MIN_SHARE, 1, 50, 51, 99, TRIAL_MAX_SHARE])
def test_the_share_bounds_are_inside(share):
    assert (TRIAL_MIN_SHARE, TRIAL_MAX_SHARE) == (0, 100)
    clean = validate_doc(with_trial(share=share))
    assert clean["routes"][row_index(clean)]["trial"]["share"] == share


@pytest.mark.parametrize("share", [-1, 101, 1000, "10", 10.0, 10.5, True, False, None, [10]])
def test_a_share_outside_zero_to_a_hundred_is_refused(share):
    d = with_trial(share=share)
    refused(d, "ROUTE_TABLE_TRIAL_SHARE", "routes[%d].trial.share" % row_index(d))


def test_a_trial_on_a_transfer_row_is_refused():
    d = with_trial(key=TRANSFER_ROW)
    err = refused(d, "ROUTE_TABLE_TRIAL", "routes[%d].trial" % row_index(d, TRANSFER_ROW))
    assert "方案应用" in err.message


def test_a_trial_on_a_resource_row_is_refused():
    d = base(resource_table="dispatch.resource")
    d["routes"][row_index(d, TRANSFER_ROW)]["target"] = {
        "kind": "RESOURCE",
        "resource_id": "workshop.queue",
    }
    validate_doc(d)
    d = with_trial(d, key=TRANSFER_ROW)
    refused(d, "ROUTE_TABLE_TRIAL", "routes[%d].trial" % row_index(d, TRANSFER_ROW))


def test_the_rows_own_target_as_its_trial_is_refused():
    d = with_trial(app=ALPHA)
    refused(d, "ROUTE_TABLE_TRIAL", "routes[%d].trial.app_id" % row_index(d))


def test_an_unregistered_trial_app_is_refused():
    d = with_trial(app="00000000-0000-4000-8000-0000000000ff")
    refused(d, "ROUTE_TABLE_UNREGISTERED_APP", "routes[%d].trial.app_id" % row_index(d))


@pytest.mark.parametrize("app", ["not-an-app", "", None, 5, " " + DELTA])
def test_a_trial_app_id_that_is_not_an_app_id_is_refused(app):
    d = with_trial(app=app)
    refused(d, "ROUTE_TABLE_TRIAL", "routes[%d].trial.app_id" % row_index(d))


@pytest.mark.parametrize("app", [ALPHA, DELTA])
@pytest.mark.parametrize("existing", [False, True])
def test_a_registered_trial_app_cannot_hide_a_terminal_newline(app, existing):
    """The stored ID must be the exact ID invoked, including when it disguises the primary app."""
    malformed = app + "\n"
    d = with_trial(app=malformed)
    d["solutions"][malformed] = copy.deepcopy(d["solutions"][app])
    refused(d, "ROUTE_TABLE_TRIAL", "routes[%d].trial.app_id" % row_index(d))
    storage = FakeStorage()
    tables = StorageTables(storage)
    if existing:
        tables.save(base(), 0)
    before = dict(storage.data)
    with pytest.raises(RouteTableError) as exc:
        tables.save(d, int(existing))
    assert exc.value.code == "ROUTE_TABLE_TRIAL"
    assert storage.data == before, "an invalid trial must not create or replace a saved version"


@pytest.mark.parametrize("existing", [False, True])
def test_a_trial_cannot_fall_back_to_a_primary_app_with_a_terminal_newline(existing):
    d = with_trial()
    malformed = ALPHA + "\n"
    d["solutions"][malformed] = d["solutions"].pop(ALPHA)
    d["routes"][row_index(d)]["target"]["app_id"] = malformed
    refused(d, "ROUTE_TABLE_TRIAL", "routes[%d].trial" % row_index(d))
    storage = FakeStorage()
    tables = StorageTables(storage)
    if existing:
        tables.save(base(), 0)
    before = dict(storage.data)
    with pytest.raises(RouteTableError) as exc:
        tables.save(d, int(existing))
    assert exc.value.code == "ROUTE_TABLE_TRIAL"
    assert storage.data == before


@pytest.mark.parametrize("status", ["CANDIDATE", "GATED", "SHADOW", "RETIRED"])
def test_a_trial_app_that_is_not_current_is_refused(status):
    d = with_trial()
    d["solutions"][DELTA]["status"] = status
    if status == "GATED":
        d["solutions"][DELTA]["gate_code"] = "TOY_SORTER_DISABLED"
    err = refused(d, "ROUTE_TABLE_SOLUTION_NOT_CALLABLE", "routes[%d].trial.app_id" % row_index(d))
    assert status in err.message and DELTA[:8] in err.message


@pytest.mark.parametrize("status", ["CANDIDATE", "GATED", "SHADOW", "RETIRED"])
def test_a_trial_on_a_row_whose_own_target_is_not_current_is_refused(status):
    d = with_trial()
    d["solutions"][ALPHA]["status"] = status
    if status == "GATED":
        d["solutions"][ALPHA]["gate_code"] = "TOY_SORTER_DISABLED"
    else:
        d["solutions"][ALPHA].pop("gate_code", None)
    plain = copy.deepcopy(d)
    del plain["routes"][row_index(d)]["trial"]
    validate_doc(plain)  # the parked row itself is still stored, as before
    err = refused(d, "ROUTE_TABLE_TRIAL", "routes[%d].trial" % row_index(d))
    assert status in err.message and ALPHA[:8] in err.message


def test_a_trial_on_a_parked_row_is_refused_whichever_parked_row_it_is():
    """BETA's row (CANDIDATE) and GAMMA's (GATED) in the toy table: neither takes a trial."""
    for key in (("P_99_62_01", "COMPLAINT", "STAGE_1"), ("P_99_63_01", "COMPLAINT", "STAGE_1")):
        d = with_trial(key=key)
        refused(d, "ROUTE_TABLE_TRIAL", "routes[%d].trial" % row_index(d, key))


@pytest.mark.parametrize("app", [BETA, GAMMA])
def test_the_toy_candidate_and_gated_apps_cannot_be_trials(app):
    refused(with_trial(app=app), "ROUTE_TABLE_SOLUTION_NOT_CALLABLE")


def test_more_than_one_trial_on_a_row_is_refused():
    d = with_trial()
    i = row_index(d)
    d["routes"][i]["trial"] = [
        {"app_id": DELTA, "share": 10},
        {"app_id": "00000000-0000-4000-8000-000000000006", "share": 10},
    ]
    err = refused(d, "ROUTE_TABLE_TRIAL", "routes[%d].trial" % i)
    assert "最多挂一个" in err.message and "2 个" in err.message


@pytest.mark.parametrize(
    "trial",
    [
        None,
        [],
        [{"app_id": DELTA, "share": 10}],
        "x",
        {},
        {"app_id": DELTA},
        {"share": 10},
        {"app_id": DELTA, "share": 10, "weight": 1},
    ],
)
def test_a_trial_of_another_shape_is_refused(trial):
    d = base()
    i = row_index(d)
    d["routes"][i]["trial"] = trial
    refused(d, "ROUTE_TABLE_TRIAL", "routes[%d].trial" % i)


@pytest.mark.parametrize("key", ["trials", "experiment"])
def test_another_extra_row_key_is_still_refused(key):
    d = base()
    d["routes"][row_index(d)][key] = {"app_id": DELTA, "share": 10}
    refused(d, "ROUTE_TABLE_INVALID", "routes[%d]" % row_index(d))


@pytest.mark.parametrize("note", ["", "   ", "x" * 201, 5, None])
def test_a_trial_note_is_one_short_line_when_written(note):
    d = with_trial(note=note)
    refused(d, "ROUTE_TABLE_INVALID", "routes[%d].trial.note" % row_index(d))


def test_a_v2_table_takes_no_trial():
    d = with_trial()
    d["schema"] = SCHEMA_V2
    d["solutions"] = {
        a: {"name": s["name"], "inputs": s["inputs"]} for a, s in d["solutions"].items()
    }
    plain = copy.deepcopy(d)
    del plain["routes"][row_index(d)]["trial"]
    validate_doc(plain)
    refused(d, "ROUTE_TABLE_TRIAL", "routes[%d].trial" % row_index(d))


def test_a_refused_trial_writes_nothing():
    st = StorageTables(FakeStorage())
    st.save(base(), 0)
    before = dict(st.storage.data)
    with pytest.raises(RouteTableError) as e:
        st.save(with_trial(share=101, note="x"), 1)
    assert e.value.code == "ROUTE_TABLE_TRIAL_SHARE" and st.storage.data == before
    assert st.save(with_trial(share=50, note="x"), 1)["version"] == 2
    assert st.load("dispatch.route")["routes"][row_index(base())]["trial"] == {
        "app_id": DELTA,
        "share": 50,
        "note": "x",
    }


def test_each_trial_share_is_a_rule_of_its_own_under_the_rule_limit():
    """One target, 150 (issue type × share) groups: 3 rules without the trials, 150 with them."""
    d = base()
    plain, rows = copy.deepcopy(d), []
    for n in range(50):
        scene = "P_98_%02d_01" % (n + 1)
        d["scenes"][scene] = plain["scenes"][scene] = {
            "name": "Toy %d" % n,
            "domain": "Tile workshop",
        }
        for issue_type in d["issue_types"]:
            rows.append(
                {
                    "scene_id": scene,
                    "issue_type": issue_type,
                    "phase": "STAGE_2",
                    "target": {"kind": "SOLUTION_APP", "app_id": ALPHA},
                }
            )
    plain["routes"] += copy.deepcopy(rows)
    validate_doc(plain)
    for n, r in enumerate(rows):
        r["trial"] = {"app_id": DELTA, "share": n // 3 + 1}
    d["routes"] += rows
    assert len(rows) > MAX_ROUTES
    refused(d, "ROUTE_TABLE_TOO_LARGE", "routes")
