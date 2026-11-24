# Load Dify before HTTPX so its gevent socket patch matches production startup.
import dify_plugin  # noqa: F401
import pytest
from deployment_fixtures import CREDENTIALS


@pytest.fixture(autouse=True)
def deployment_configured():
    """0.5.44-rc3: the three deployment app ids come from the provider credentials; every test runs with the invented
    ones of deployment_fixtures unless it sets others (deployment.apply)."""
    from dmn_client import deployment

    token = deployment.apply(CREDENTIALS)
    yield
    deployment.reset(token)


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


@pytest.fixture
def live_owners():
    """Files that read the live table snapshots (outside git, tests/real_fixtures.py): the owners of target. and triage.
    are the live apps those tables carry; the maintenance app stays the invented one."""
    import real_fixtures

    from dmn_client import deployment

    token = real_fixtures.configure_live_owners()
    yield
    deployment.reset(token)
