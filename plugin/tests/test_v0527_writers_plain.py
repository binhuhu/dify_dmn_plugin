"""0.5.27 declares writers as a plain form setting. These unit checks do not validate host caching, migration or
revocation; target-host acceptance is separate."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _param(name):
    doc = yaml.safe_load((ROOT / "plugin/tools/route_table_store.yaml").read_text(encoding="utf-8"))
    return next(p for p in doc["parameters"] if p["name"] == name)


def test_writers_is_a_plain_form_string():
    p = _param("writers")
    assert (p["type"], p["form"], p.get("required")) == ("string", "form", False)


def test_no_route_table_store_parameter_is_a_secret_input():
    doc = yaml.safe_load((ROOT / "plugin/tools/route_table_store.yaml").read_text(encoding="utf-8"))
    assert [p["name"] for p in doc["parameters"] if p["type"] == "secret-input"] == []
