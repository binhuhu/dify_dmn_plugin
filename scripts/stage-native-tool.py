"""Stage the native-component draft's Tool-only plugin. No package or deployment."""

import argparse
import importlib.util
from pathlib import Path
import shutil

import yaml


def stage(destination):
    source = Path(__file__).with_name("stage-builtin.py")
    spec = importlib.util.spec_from_file_location("stage_builtin", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = module.stage(destination)
    manifest_path = output / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest["plugins"].pop("endpoints", None)
    manifest["resource"]["permission"].pop("endpoint", None)
    manifest["description"] = {
        "en_US": "Draft DMN Tools for a native Dify source component. No Endpoint.",
        "zh_Hans": "Dify 原生前端组件配套 DMN Tool 草稿；无 Endpoint。",
    }
    manifest_path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
    )
    for name in ("endpoints", "editor_static", "viewer_static"):
        shutil.rmtree(output / name)
    (output / "README.md").write_text(
        "# Native Dify DMN Tool draft\n\n"
        "Tool-only development staging. No Endpoint or embedded webpage.\n"
        "The React component is integrated separately into Dify frontend source.\n"
        "Host binding is required. Not an installed or production-accepted build.\n"
    )
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination")
    print(stage(parser.parse_args().destination))
