import fs from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const exec = promisify(execFile);
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import Ajv from "ajv/dist/2020.js";
import standaloneCode from "ajv/dist/standalone/index.js";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.join(root, "extension");
const out = path.join(root, "dist/chrome-sidepanel");
const schema = JSON.parse(
  await fs.readFile(
    path.join(
      root,
      "../../plugin/dmn_client/dsl_schemas/architecture.schema.json",
    ),
    "utf8",
  ),
);
const ajv = new Ajv({ strict: false, allErrors: true, code: { source: true } });
await fs.mkdir(path.join(source, "generated"), { recursive: true });
await fs.writeFile(
  path.join(source, "generated/validate.cjs"),
  standaloneCode(ajv, ajv.compile(schema)),
);
const resultSchema = JSON.parse(
  await fs.readFile(
    path.join(
      root,
      "../../specs/service-decision-dsl-v2/schemas/node-result.schema.json",
    ),
    "utf8",
  ),
);
await fs.writeFile(
  path.join(source, "generated/result.cjs"),
  standaloneCode(ajv, ajv.compile(resultSchema)),
);
await fs.rm(out, { recursive: true, force: true });
await fs.mkdir(out, { recursive: true });
await build({
  entryPoints: [path.join(source, "panel.js")],
  bundle: true,
  outfile: path.join(out, "panel.js"),
  platform: "browser",
  format: "iife",
  target: "chrome116",
  define: { "process.env.NODE_ENV": '"production"' },
  minify: true,
  legalComments: "eof",
});
for (const name of [
  "manifest.json",
  "worker.js",
  "sidepanel.html",
  "panel.css",
])
  await fs.copyFile(path.join(source, name), path.join(out, name));
console.log(out);
const { exportConfig } = await import("../extension/config.js");
const bundle = JSON.parse(
  await fs.readFile(
    path.join(
      root,
      "../../specs/service-decision-dsl-v2/examples/architecture_bundle.json",
    ),
    "utf8",
  ),
);
const invocation = JSON.parse(
  await fs.readFile(
    path.join(
      root,
      "../../specs/service-decision-dsl-v2/examples/invocation_decision.json",
    ),
    "utf8",
  ),
);
const config = await exportConfig(bundle, invocation.node_ref);
await fs.mkdir(path.join(out, "demo"), { recursive: true });
for (const [name, value] of Object.entries({
  "definition.json": bundle,
  "tool-config.json": config,
  "synthetic-invocation.json": {
    ...config.fixed_parameters,
    inputs_json: invocation.inputs,
    execution_context_json: invocation.execution_context,
  },
}))
  await fs.writeFile(
    path.join(out, "demo", name),
    JSON.stringify(value, null, 2) + "\n",
  );
await fs.copyFile(
  path.join(root, "test/fixtures/redacted-dify.json"),
  path.join(out, "demo/redacted-dify.json"),
);
await fs.mkdir(path.join(out, "licenses"), { recursive: true });
for (const [name, filename] of [
  ["react", "LICENSE"],
  ["react-dom", "LICENSE"],
  ["scheduler", "LICENSE"],
  ["ajv", "LICENSE"],
  ["json-canonicalize", "LICENSE.md"],
]) {
  await fs.copyFile(
    path.join(root, "node_modules", name, filename),
    path.join(out, "licenses", name + ".txt"),
  );
}

await fs.copyFile(path.join(root, "README.md"), path.join(out, "README.md"));

await fs.copyFile(
  path.join(source, "INSTALL.md"),
  path.join(out, "INSTALL.md"),
);
await fs.writeFile(
  path.join(out, "source.json"),
  JSON.stringify(
    {
      repository: "binhuhu/dify_dmn_plugin",
      commit: (
        await exec("git", ["rev-parse", "HEAD"], { cwd: root })
      ).stdout.trim(),
      dirty: Boolean(
        (
          await exec("git", ["status", "--porcelain"], { cwd: root })
        ).stdout.trim(),
      ),
      release: false,
      cloud_acceptance: "NOT_RUN",
    },
    null,
    2,
  ) + "\n",
);
