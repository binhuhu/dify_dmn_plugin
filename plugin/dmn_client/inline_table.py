"""The evaluate_json_table tool: a table given in the node itself, evaluated locally.

Two kinds of table are taken, told apart by their own keys:

- json-table-v1 (`format`): the first, small format — unchanged, see dmn_client.json_table.
- the stored decision structure ("DMN 2.0"): a decision-table/v1 or decision-set/v1 document exactly as the plugin
  stores it (`schema`), or one decision model by itself (`profile`). It is validated and evaluated by the same code as
  a stored table (dmn_client.stored_tables), takes the same inputs ({parameters: {name: {quality, value, source_refs}},
  missing?, undeclared?, models?}) and answers with the same envelope (dmn-json.stored-table-result/v1), so a workflow
  can move a table between the node and the plugin's storage without touching what reads the result. source.kind is
  INLINE: nothing was read from storage, and there is no version history behind it.

0.5.42 (from the 11-17 DMN XML line): a table in a node is for trials — no version history, no editor, no ledger, and
changing it means publishing the workflow again. Its receipt carries no version and no save time whatever the document
claims, and is refused where a stored table's receipt is read (INLINE_RECEIPT_REFUSED: stored_tables.inline_refused).
"""

import hashlib
import json

import rfc8785

from dmn_client import json_table, stored_tables
from dmn_client.client import EngineError, json_object
from dmn_client.route_table import RouteTableError

MAX_TABLE_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024
INLINE_FORMATS = {
    stored_tables.SCHEMA_DECISION: stored_tables.FORMAT_DECISION,
    stored_tables.SCHEMA_DECISION_SET: stored_tables.FORMAT_DECISION_SET,
    stored_tables.SCHEMA_DECISION_V2: stored_tables.FORMAT_DECISION_V2,  # 0.5.41: the new writing
}


def _peek(raw):
    """The table as an object when it can be read as one, without judging it (each path validates its own)."""
    if type(raw) is dict:
        return raw
    if type(raw) is str and len(raw) <= 4 * MAX_TABLE_BYTES:
        try:
            value = json.loads(raw)
        except (ValueError, RecursionError):
            return None
        return value if type(value) is dict else None
    return None


def evaluate_inline(parameters):
    seen = _peek(parameters.get("table_json"))
    if seen is None or not ("schema" in seen or "profile" in seen):
        return json_table.evaluate_table(parameters)
    out = {
        "schema_version": stored_tables.RESULT_SCHEMA,
        "status": "FAILED",
        "table": None,
        "format": None,
        "source": None,
        "result": None,
        "error": None,
    }
    try:
        try:
            doc = json_object(parameters.get("table_json"), "table_json", MAX_TABLE_BYTES)
        except EngineError as exc:
            raise RouteTableError("INPUT_INVALID", exc.message, "table_json") from exc
        inputs = parameters.get("inputs_json")
        if inputs is None or (isinstance(inputs, str) and not inputs.strip()):
            inputs = {}
        if "profile" in doc:
            out = _model(doc, inputs, out)
        else:
            schema = doc.get("schema")
            # 0.5.42: a schema written as a list or an object is no format (it was a TypeError)
            fmt = INLINE_FORMATS.get(schema) if isinstance(schema, str) else None
            if fmt is None:
                raise RouteTableError(
                    "STORED_TABLE_FORMAT_UNKNOWN",
                    "节点里直接求值只收决策表（decision-table/v1、decision-table/v2）和决策集（decision-set/v1），读到 %s"
                    % str(doc.get("schema"))[:64],
                    "table_json.schema",
                )
            if not (type(doc.get("table")) is str and doc["table"]):
                raise RouteTableError(
                    "ROUTE_TABLE_INVALID", "文档要带表名 table", "table_json.table"
                )
            out = stored_tables.evaluate_stored(
                doc["table"], fmt, inputs, lambda _table: doc, kind=stored_tables.SOURCE_INLINE
            )
        pin = parameters.get("expected_sha256")
        source = out.get("source") or {}
        digest = source.get("content_sha256") or source.get("model_sha256")
        if pin not in (None, "") and digest is not None and pin != digest:
            raise RouteTableError(
                "MODEL_DIGEST_MISMATCH",
                "表的内容摘要是 %s…，不是 expected_sha256 钉的 %s…" % (digest[:12], str(pin)[:12]),
                "expected_sha256",
            )
        if len(json.dumps(out, ensure_ascii=False).encode("utf-8")) > MAX_RESULT_BYTES:
            raise RouteTableError("RESULT_TOO_LARGE", "结果超过 2 MiB", "")
    except RouteTableError as exc:
        out.update(
            status="BLOCKED" if exc.code in stored_tables.BLOCKING else "FAILED",
            result=None,
            error={"code": exc.code, "message": exc.message, "path": exc.path},
        )
    return out


def _model(model, inputs, out):
    """One decision model by itself: no table name, no owner — only its own digest identifies it."""
    out["format"] = stored_tables.FORMAT_DECISION
    clean = json.loads(stored_tables.canon(stored_tables._model(model, "table_json")))
    marked = sorted(n for n, c in clean["parameters"].items() if stored_tables.IN_SET in c)
    if marked:
        raise RouteTableError(
            "DECISION_TABLE_INVALID",
            "in_set 只用于决策集；单个模型的参数不写",
            "table_json.parameters.%s" % marked[0],
        )
    out["source"] = {
        "kind": stored_tables.SOURCE_INLINE,
        "table": None,
        "version": None,
        "saved_at": None,
        "content_sha256": None,
        "model_sha256": hashlib.sha256(rfc8785.dumps(clean)).hexdigest(),
    }
    inputs = stored_tables._input_object(inputs, "inputs_json", strict=False)
    out["result"] = stored_tables.evaluate_decision_table(
        {"model_ref": "inline@" + clean["version"], "model": clean},
        inputs,
        stored_tables.model_sources(clean),
    )
    out["status"] = "SUCCEEDED"
    return out
