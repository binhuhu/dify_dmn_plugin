import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  exportConfig,
  parseDefinition,
  diagnostics,
} from "../extension/config.js";
import { decisions, reference, copy } from "../model.js";
const bundle = JSON.parse(
  fs.readFileSync(
    new URL(
      "../../../specs/service-decision-dsl-v2/examples/architecture_bundle.json",
      import.meta.url,
    ),
  ),
);
const node = reference(
  decisions(bundle).find((n) => n.model_ref === "demo.meeting@1.0.0"),
);
test("content export matches pinned Python RFC8785 digest; omits runtime identity and inputs", async () => {
  const config = await exportConfig(bundle, node);
  const expected = JSON.parse(
    fs.readFileSync(
      new URL(
        "../../../specs/service-decision-dsl-v2/examples/definition_digest.json",
        import.meta.url,
      ),
    ),
  ).value;
  assert.equal(config.fixed_parameters.expected_definition_sha256, expected);
  assert.equal(Object.keys(config.fixed_parameters).length, 3);
  assert.equal(config.authority, "CONTENT_ONLY_NOT_AUTHORIZATION");
  assert.deepEqual(config.fixed_parameters.definition_bundle_json, bundle);
});
test("RC5 rejects grouped v2 and missing templates; developer mode retains grouped semantics", async () => {
  const next = copy(bundle),
    model = next.models["demo.meeting@1.0.0"];
  model.profile = "service-decision-table-v2";
  model.rules[0].when = [{ all: model.rules[0].when }];
  await assert.rejects(exportConfig(next, node, "rc5"), /RC5/);
  const config = await exportConfig(next, node, "dev");
  assert.deepEqual(config.fixed_parameters.definition_bundle_json, next);
  model.rules[0].output_template_ref = "absent";
  await assert.rejects(exportConfig(next, node, "dev"), /模板/);
});
test("import preserves extensions; rejects unsafe numbers and malformed JSON; unknown policy is explicit", () => {
  const next = copy(bundle);
  next.extra_demo = { unicode: "合成😀", zero: 0 };
  assert.deepEqual(parseDefinition(JSON.stringify(next)), next);
  assert.throws(() => parseDefinition("{"));
  assert.throws(
    () =>
      parseDefinition(JSON.stringify({ ...next, unsafe: 9007199254740992 })),
    /安全/,
  );
  next.models["demo.meeting@1.0.0"].hit_policy = "UNKNOWN_POLICY";
  assert.match(
    diagnostics(parseDefinition(JSON.stringify(next)), "dev").join("\n"),
    /未知 Hit Policy/,
  );
});
test("built extension requests sidePanel only and ships no remote code/evaluator or content script", () => {
  const manifest = JSON.parse(
    fs.readFileSync(
      new URL("../dist/chrome-sidepanel/manifest.json", import.meta.url),
    ),
  );
  assert.deepEqual(manifest.permissions, ["sidePanel"]);
  for (const name of [
    "host_permissions",
    "content_scripts",
    "externally_connectable",
  ])
    assert.equal(manifest[name], undefined);
  assert.match(
    manifest.content_security_policy.extension_pages,
    /connect-src 'none'/,
  );
  const js = fs.readFileSync(
    new URL("../dist/chrome-sidepanel/panel.js", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(js, /\beval\s*\(|new Function\s*\(/);
});
