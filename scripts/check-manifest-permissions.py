#!/usr/bin/env python3
"""Offline Endpoint permission gate for manifest YAML or an unopened difypkg."""

import argparse
from pathlib import Path
import sys
import zipfile

import yaml


class ManifestError(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of checking a silently overwritten value."""


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ManifestError("manifest mapping keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def check(raw):
    try:
        manifest = yaml.load(raw, Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise ManifestError("invalid manifest YAML") from exc
    if not isinstance(manifest, dict):
        raise ManifestError("manifest must be a mapping")
    plugins = manifest.get("plugins", {})
    if not isinstance(plugins, dict):
        raise ManifestError("plugins must be a mapping")
    endpoints = plugins.get("endpoints", [])
    if not isinstance(endpoints, list) or any(
        not isinstance(item, str) or not item.strip() for item in endpoints
    ):
        raise ManifestError("plugins.endpoints must be a list of nonempty paths")
    if not endpoints:
        return "no Endpoints declared"
    permission = manifest
    for key in ("resource", "permission", "endpoint"):
        if not isinstance(permission, dict):
            raise ManifestError(
                "declared Endpoints require resource.permission.endpoint.enabled=true"
            )
        permission = permission.get(key)
    if not isinstance(permission, dict) or permission.get("enabled") is not True:
        raise ManifestError(
            "declared Endpoints require resource.permission.endpoint.enabled=true"
        )
    return "Endpoint permission enabled"


def read_manifest(path):
    if path.suffix == ".difypkg":
        with zipfile.ZipFile(path) as archive:
            if archive.namelist().count("manifest.yaml") != 1:
                raise ManifestError(
                    "package must contain exactly one root manifest.yaml"
                )
            if archive.getinfo("manifest.yaml").file_size > 1024 * 1024:
                raise ManifestError("manifest exceeds 1 MiB")
            return archive.read("manifest.yaml").decode("utf-8")
    if path.stat().st_size > 1024 * 1024:
        raise ManifestError("manifest exceeds 1 MiB")
    return path.read_text(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", type=Path, nargs="+")
    args = parser.parse_args()
    failed = False
    for path in args.paths:
        try:
            message = check(read_manifest(path))
        except (ManifestError, OSError, UnicodeError, zipfile.BadZipFile) as exc:
            print(f"FAIL {path}: {exc}", file=sys.stderr)
            failed = True
        else:
            print(f"PASS {path}: {message}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
