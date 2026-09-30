export function table({
  policy = "UNIQUE",
  aggregation = "",
  entries = ["> 10"],
  outputs = ['"yes"'],
  type = "string",
  extra = "",
  defaultOutput = "",
  input = "x",
  inputType = "number",
} = {}) {
  const rules = entries
    .map(
      (entry, i) =>
        `<rule id="r${i + 1}"><inputEntry id="ie${i}"><text>${entry.replaceAll("&", "&amp;").replaceAll("<", "&lt;")}</text></inputEntry><outputEntry id="oe${i}"><text>${outputs[i].replaceAll("&", "&amp;").replaceAll("<", "&lt;")}</text></outputEntry></rule>`,
    )
    .join("");
  return `<?xml version="1.0" encoding="UTF-8"?><definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="defs" name="Example" namespace="urn:test"><decision id="decision" name="Result"><decisionTable id="table" ${policy ? `hitPolicy="${policy}"` : ""} ${aggregation ? `aggregation="${aggregation}"` : ""}><input id="input"><inputExpression id="expr" typeRef="${inputType}"><text>${input}</text></inputExpression></input><output id="output" name="value" typeRef="${type}">${extra}${defaultOutput ? `<defaultOutputEntry id="default"><text>${defaultOutput}</text></defaultOutputEntry>` : ""}</output>${rules}</decisionTable></decision></definitions>`;
}
export function literal(text) {
  return `<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="defs" name="Example" namespace="urn:test"><decision id="decision" name="Result"><literalExpression id="expr"><text>${text.replaceAll("&", "&amp;").replaceAll("<", "&lt;")}</text></literalExpression></decision></definitions>`;
}
