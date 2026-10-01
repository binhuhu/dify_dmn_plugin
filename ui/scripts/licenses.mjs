import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
const require = createRequire(import.meta.url),
  seen = new Set(),
  notices = [];
function visit(name) {
  if (seen.has(name) || name.startsWith("@types/")) return;
  seen.add(name);
  let dir = dirname(require.resolve(name));
  while (true) {
    try {
      if (JSON.parse(readFileSync(join(dir, "package.json"))).name === name)
        break;
    } catch {}
    const parent = dirname(dir);
    if (parent === dir) throw new Error("Missing package metadata: " + name);
    dir = parent;
  }
  const pkg = JSON.parse(readFileSync(join(dir, "package.json")));
  const files = readdirSync(dir).filter((x) =>
    /^(licen[cs]e|copying)(\.|$)/i.test(x),
  );
  if (!files.length) throw new Error("Missing license: " + name);
  notices.push(
    `${name}@${pkg.version} (${pkg.license})\n` +
      files.map((f) => readFileSync(join(dir, f), "utf8")).join("\n"),
  );
  for (const dep of Object.keys(pkg.dependencies || {})) visit(dep);
}
for (const name of Object.keys(
  JSON.parse(readFileSync(new URL("../package.json", import.meta.url)))
    .dependencies,
))
  visit(name);
writeFileSync(
  new URL(
    "../../plugin/workbench_static/THIRD_PARTY_LICENSES.txt",
    import.meta.url,
  ),
  notices.join("\n\n----------------\n\n"),
);
