"""测试用的三个部署应用号（0.5.44-rc3 起现网号从插件配置读，不进仓）：都是编的，conftest 每条用例前按插件配置的写法装上。"""

MAINTENANCE_APP = "d0000001-0000-4000-8000-000000000001"  # 「路由表维护」应用（编的）
APP_TARGET = "d0000002-0000-4000-8000-000000000002"  # 定位 TARGET（编的）
APP_TRIAGE = "d0000003-0000-4000-8000-000000000003"  # 分诊 TRIAGE（编的）
CREDENTIALS = {
    "maintenance_app_id": MAINTENANCE_APP,
    "target_app_id": APP_TARGET,
    "triage_app_id": APP_TRIAGE,
}


def in_config(fn):
    """Run fn with these invented ids configured. A worker thread or a fresh process does not inherit the context the
    test configured (contextvars), as each tool invocation of the plugin configures its own (deployment.configured)."""

    def run(*args, **kwargs):
        from dmn_client import deployment

        token = deployment.apply(CREDENTIALS)
        try:
            return fn(*args, **kwargs)
        finally:
            deployment.reset(token)

    return run
