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


# 插件拒码登记（docs/refusal-codes.v1.json，协调 11-25）：全量用例跑时抛出的每个拒码都要在登记里。只算插件源码里写着的码（用例自己的桩
# 抛的码不算）；登记文件不在（如变异脚本拷出的副本里没有 docs/）就不核。静态的那一半见 tests/test_refusal_codes_register.py。
_RAISED_CODES = set()


def pytest_configure(config):
    from dmn_client import route_table

    init = route_table.RouteTableError.__init__

    def recording(self, code, *args, **kwargs):
        if isinstance(code, str):
            _RAISED_CODES.add(code)
        return init(self, code, *args, **kwargs)

    route_table.RouteTableError.__init__ = recording


def pytest_sessionfinish(session, exitstatus):
    import importlib.util
    import os

    script = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "refusal_codes.py")
    if not os.path.exists(script):
        return
    spec = importlib.util.spec_from_file_location("refusal_codes", script)
    rc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rc)
    if not os.path.exists(rc.REGISTER):
        return
    text = rc.source_text()
    missing = sorted(
        c for c in _RAISED_CODES if '"%s"' % c in text and c not in set(rc.listed_codes())
    )
    if missing:
        print(
            "\n用例里抛出了插件拒码登记里没有的码（重出登记：scripts/refusal_codes.py gen）："
            + "、".join(missing)
        )
        session.exitstatus = 1
