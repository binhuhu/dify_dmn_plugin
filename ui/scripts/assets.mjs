import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
const root = new URL("../../plugin/workbench_static/", import.meta.url);
const result = {};
function visit(dir = "") {
  for (const entry of readdirSync(new URL(dir, root), {
    withFileTypes: true,
  })) {
    const file = dir + entry.name;
    if (entry.isDirectory()) visit(file + "/");
    else if (file !== "assets.json")
      result[file] = {
        sha256: createHash("sha256")
          .update(readFileSync(new URL(file, root)))
          .digest("hex"),
        content_type: file.endsWith(".js")
          ? "text/javascript; charset=utf-8"
          : file.endsWith(".css")
            ? "text/css; charset=utf-8"
            : file.endsWith(".txt")
              ? "text/plain; charset=utf-8"
              : "text/html; charset=utf-8",
      };
  }
}
visit();
writeFileSync(
  new URL("assets.json", root),
  JSON.stringify(result, null, 2) + "\n",
);
