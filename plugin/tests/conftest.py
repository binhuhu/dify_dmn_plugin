# Load Dify before HTTPX so its gevent socket patch matches production startup.
import dify_plugin  # noqa: F401
import pytest


@pytest.fixture
def v1_heads_before_0541(monkeypatch):
    """0.5.41: a solution-layer name with no head takes only the new writing (v2). The v1 tests written before 0.5.41
    save v1 tables on fresh names to test v1's own checks; with this fixture each such name counts as one that already
    had a v1 head stored by 0.5.40 (the head gate's fresh_v1), and everything else about the save is unchanged."""
    from dmn_client import decision_v2

    gate = decision_v2.head_gate
    monkeypatch.setattr(
        decision_v2, "head_gate", lambda cur, clean, **kw: gate(cur, clean, fresh_v1=True)
    )
