"""部署的三个应用号从插件配置读（0.5.44-rc3；Brian 11-23「先改配置出 rc3」）。

0.5.43 以前这三个号写死在代码里（two_state.MAINTENANCE_APP、stored_tables.APP_TARGET / APP_TRIAGE），随包公开。
现在它们是工具提供方的凭据（builtin/provider.yaml 的 credentials_for_provider，在 Dify「工具 → 内置 JSON 决策表 → 授权」里填），
每次工具调用由 configured() 从 self.runtime.credentials 读进来，同一个意思只在这里一种写法：

    maintenance_app_id  「路由表维护」应用：只认它写主链路策略表、写手登记、登记值（two_state）
    target_app_id       定位 TARGET 应用：target. 下决策表的 owner.app_id（stored_tables）
    triage_app_id       分诊 TRIAGE 应用：triage. 下决策表与资源去向表的 owner.app_id（stored_tables）

没配或写得不对（不是小写 36 位应用号）就当没有：写入闸关门，按原来的码拒（非维护应用写 403 STORED_TABLE_WRITERS_APP_REJECTED、
owner 对不上 400 STORED_TABLE_NAME），不留默认号。配上的号与 0.5.43 写死的相同时，存、读、求值的回答逐字节不变。
"""

import contextvars
import functools
import re

MAINTENANCE = "maintenance_app_id"
TARGET = "target_app_id"
TRIAGE = "triage_app_id"
KEYS = (MAINTENANCE, TARGET, TRIAGE)
APP_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
UNSET = "（插件配置里没填）"

_CONFIG = contextvars.ContextVar("dmn_json_deployment", default={})


def read(credentials):
    """The three app ids out of the provider credentials; one that is missing or not a lower-case app id is None."""
    got = credentials if isinstance(credentials, dict) else {}
    return {
        k: got[k] if isinstance(got.get(k), str) and APP_ID_RE.fullmatch(got[k]) else None
        for k in KEYS
    }


def apply(credentials):
    """Set the app ids for this invocation; returns the token for reset()."""
    return _CONFIG.set(read(credentials))


def reset(token):
    try:
        _CONFIG.reset(token)
    except ValueError:  # resumed in another context: the next invocation sets its own anyway
        pass


def configured(invoke):
    """Wrap a tool's _invoke: the provider credentials are read in before the first step and dropped after the last."""

    @functools.wraps(invoke)
    def run(self, *args, **kwargs):
        token = apply(getattr(getattr(self, "runtime", None), "credentials", None))
        try:
            yield from invoke(self, *args, **kwargs)
        finally:
            reset(token)

    return run


def app(key):
    return _CONFIG.get().get(key)


def maintenance_app():
    return app(MAINTENANCE)


def target_app():
    return app(TARGET)


def triage_app():
    return app(TRIAGE)


def same(app_id, key):
    """Whether app_id is the configured app for key; never when that app is not configured."""
    want = app(key)
    return want is not None and isinstance(app_id, str) and app_id == want


def shown(app_id):
    return app_id if app_id is not None else UNSET
