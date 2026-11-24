"""0.5.46: a capability table's info may be typed datetime (DMN 2.0.96 §十: a moment, the interface's own
YYYY-MM-DD HH:MM:SS string in Beijing time, to the second). Only the type is taken here; the vocabulary lists it. A candidate
filter's window naming a datetime info is checked from 0.5.47 (after the capability table marks its moments)."""

import copy

import pytest
from test_capability_modes_and_service import fact, moded, table

from dmn_client import capability_catalog as CC
from dmn_client import stored_tables as st
from dmn_client.route_table import RouteTableError


def doc(type_):
    return table(
        moded(
            "toy.orders.detail",
            ["M"],
            "ORDER",
            "ORDER",
            "DETAIL",
            inputs={"orderKey": {"required": True}},
            facts=[fact("TOY_ORDER.made_at", "data.made_at", "ORDER", type_)],
        )
    )


def test_datetime_is_a_fact_type():
    assert "datetime" in CC.FACT_TYPES
    assert CC.FACT_TYPES[:6] == (
        "string",
        "boolean",
        "integer",
        "number",
        "object",
        "array",
    )  # 旧的六种原样、次序不变


def test_a_datetime_info_validates():
    st.validate(copy.deepcopy(doc("datetime")))


@pytest.mark.parametrize("type_", ["date", "time", "timestamp", "date and time", "DATETIME"])
def test_other_spellings_are_refused(type_):
    with pytest.raises(RouteTableError) as e:
        st.validate(copy.deepcopy(doc(type_)))
    assert e.value.path.endswith(".type"), e.value.path
