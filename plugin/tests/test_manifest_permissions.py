"""Protect Endpoint installation declarations at the release boundary."""

import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-manifest-permissions.py"
spec = importlib.util.spec_from_file_location("manifest_gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.mark.parametrize(
    "permission",
    [
        "{}",
        "null",
        "{endpoint: {}}",
        "{endpoint: null}",
        "{endpoint: {enabled: false}}",
        '{endpoint: {enabled: "true"}}',
        "{endpoint: {enabled: 1}}",
    ],
)
def test_declared_endpoint_requires_boolean_permission(permission):
    with pytest.raises(gate.ManifestError, match="require.*enabled=true"):
        gate.check(
            "plugins: {endpoints: [endpoints/viewer.yaml]}\nresource:\n  permission: " + permission
        )


def test_no_endpoint_needs_no_endpoint_permission():
    assert gate.check("plugins: {tools: [provider/tool.yaml]}") == "no Endpoints declared"
    assert gate.check("plugins: {endpoints: []}") == "no Endpoints declared"


def test_current_builtin_and_external_manifests_pass():
    for path in (ROOT / "builtin/manifest.yaml", ROOT / "plugin/manifest.yaml"):
        gate.check(gate.read_manifest(path))


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "plugins: null",
        "plugins: {endpoints: null}",
        "plugins: {endpoints: viewer.yaml}",
        "plugins: {endpoints: ['']}",
        "plugins: {endpoints: [1]}",
        "plugins: {}\nplugins: {}",
        "plugins: [",
    ],
)
def test_malformed_declarations_fail_closed(raw):
    with pytest.raises(gate.ManifestError):
        gate.check(raw)


def test_original_rc3_manifest_fails_and_only_permission_fix_passes():
    # Git objects are the frozen release inputs, not a reconstruction of RC3.
    rc3 = subprocess.check_output(
        ["git", "show", "v0.4.0-rc3:builtin/manifest.yaml"], cwd=ROOT, text=True
    )
    rc4 = subprocess.check_output(
        ["git", "show", "v0.4.0-rc4:builtin/manifest.yaml"], cwd=ROOT, text=True
    )
    with pytest.raises(gate.ManifestError, match="require.*enabled=true"):
        gate.check(rc3)
    assert gate.check(rc4) == "Endpoint permission enabled"


def test_package_cli_rejects_rc3_and_accepts_rc4(tmp_path):
    packages = []
    for version in ("rc3", "rc4"):
        package = tmp_path / f"{version}.difypkg"
        raw = subprocess.check_output(
            ["git", "show", f"v0.4.0-{version}:builtin/manifest.yaml"], cwd=ROOT
        )
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("manifest.yaml", raw)
        packages.append(package)
    for path, expected in zip(packages, (1, 0), strict=True):
        result = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True)
        assert result.returncode == expected
    assert (
        subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, packages)], capture_output=True
        ).returncode
        == 1
    )


def test_package_requires_root_manifest(tmp_path):
    package = tmp_path / "empty.difypkg"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("nested/manifest.yaml", "plugins: {}")
    with pytest.raises(gate.ManifestError, match="exactly one root"):
        gate.read_manifest(package)
