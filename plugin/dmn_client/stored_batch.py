"""一次求值多个输入 (DMN 2.0.107 §七, §八): the evaluate_stored_table_batch tool's list, limits and answer.

The evaluation itself is stored_tables.evaluate_stored_many, the one function the single evaluate_stored_table tool runs
too; this module only reads the list, holds the two safety lines and writes the answer. Item i of item_result_jsons is,
character for character, the result_json the single tool gives for (table, format, version, content_sha256,
inputs_list_json[i]) against the same stored version.

Answer (results / result_json): {schema_version, status, table, format, count, statuses, error}; status speaks for the
list only - SUCCEEDED (every item has its own status), NOT_REQUESTED (an empty or absent list), FAILED (the list itself
refused: not a list, too many items, too large). The item receipts are only in item_result_jsons (not in results, so the
payload is sent once).

11-26 review (three defects of 37ae4cc): a list text nested too deep for the JSON reader is INPUT_INVALID, not a crash;
the list given as a list or as its JSON text gets the same answer when an item holds text UTF-8 cannot carry (a lone
surrogate) - that item's own refusal, as the single tool gives it, not the whole list's; the receipt line stops the
evaluation as soon as the receipts so far cross it, instead of first evaluating and writing out every item.
"""

import json

from dmn_client.route_table import RouteTableError, _need
from dmn_client.stored_tables import SOURCE_STORAGE, _echo, delivery_name, evaluate_stored_many

BATCH_SCHEMA = "dmn-json.stored-table-batch-result/v1"
# DMN 2.0.107 §八: the only place these numbers are written in the plugin
# more than the scene-topic table's 211 scenes: a reader never has to split
ITEMS_MAX = 256
# the item receipts together, UTF-8; inside one plugin message (5 MiB)
ITEMS_BYTES_MAX = 4 * 1024 * 1024
# the list as given, UTF-8
INPUT_BYTES_MAX = 1024 * 1024


def item_json(envelope):
    """One item's receipt as text: what the single tool's result_json is (tools/evaluate_stored_table.py)."""
    return json.dumps(envelope, ensure_ascii=False)


def _bytes(text):
    """UTF-8 length of a text. A lone surrogate (text UTF-8 cannot carry) counts as the three bytes it would take:
    measuring never refuses - whether such an item is acceptable is the single tool's answer for that item."""
    return len(text.encode("utf-8", "surrogatepass"))


def _size(value):
    try:
        return _bytes(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError):
        raise RouteTableError("INPUT_INVALID", "inputs_list_json 不是合法 JSON", "inputs_list_json")


def read_list(raw):
    """inputs_list_json -> the items, each as the single tool hands inputs_json on (absent or empty = {}).
    None / "" -> [] (not requested)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return []
    _need(
        _size(raw) <= INPUT_BYTES_MAX,
        "STORED_TABLE_BATCH_TOO_LARGE",
        "inputs_list_json 超过 %d 字节（DMN 2.0 §八）" % INPUT_BYTES_MAX,
        "inputs_list_json",
    )
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        # RecursionError: nested deeper than the JSON reader goes, well inside 1 MiB
        except (ValueError, RecursionError):
            raise RouteTableError(
                "INPUT_INVALID", "inputs_list_json 不是合法 JSON", "inputs_list_json"
            )
    _need(
        type(raw) is list,
        "INPUT_INVALID",
        "inputs_list_json 必须是列表（每项是一份 inputs_json）",
        "inputs_list_json",
    )
    _need(
        len(raw) <= ITEMS_MAX,
        "STORED_TABLE_BATCH_TOO_MANY",
        "一次至多 %d 项，这次 %d 项（DMN 2.0 §八）" % (ITEMS_MAX, len(raw)),
        "inputs_list_json",
    )
    # the single tool: tool_parameters.get("inputs_json") or {}
    return [item or {} for item in raw]


class _ReceiptLine:
    """The 4 MiB line on the receipts together, held while the items are evaluated: each evaluated item's receipt is
    written once, and the call stops at the first item that takes the running total over the line (the final total can
    only be larger, so the answer is the one the whole list would get)."""

    def __init__(self):
        self.texts, self.total = {}, 0

    def __call__(self, i, envelope):
        text = self.texts[i] = item_json(envelope)
        self.total += _bytes(text)
        _need(
            self.total <= ITEMS_BYTES_MAX,
            "STORED_TABLE_BATCH_TOO_LARGE",
            "求值到第 %d 项，回执至少已有 %d 字节，超过 %d（DMN 2.0 §八）"
            % (i + 1, self.total, ITEMS_BYTES_MAX),
            "item_result_jsons",
        )


def evaluate_batch(
    table,
    fmt,
    raw_list,
    load,
    version=None,
    content_sha256=None,
    load_version=None,
    find_content=None,
    kind=SOURCE_STORAGE,
):
    """-> (answer, item_result_jsons). Never raises a RouteTableError: a refused list is in the answer."""
    answer = {
        "schema_version": BATCH_SCHEMA,
        "status": "FAILED",
        "table": _echo(delivery_name(table)) if isinstance(table, str) else None,
        "format": _echo(fmt) if fmt != "" else fmt,
        "count": 0,
        "statuses": [],
        "error": None,
    }
    try:
        items = read_list(raw_list)
        if not items:
            answer["status"] = "NOT_REQUESTED"
            return answer, []
        line = _ReceiptLine()
        envelopes = evaluate_stored_many(
            table,
            fmt,
            items,
            load,
            version=version,
            content_sha256=content_sha256,
            load_version=load_version,
            find_content=find_content,
            kind=kind,
            each=line,
        )
        # the items refused before step 4 (input, table level) are written here; the evaluated ones already were
        texts = [
            line.texts[i] if i in line.texts else item_json(e) for i, e in enumerate(envelopes)
        ]
        total = sum(_bytes(t) for t in texts)
        _need(
            total <= ITEMS_BYTES_MAX,
            "STORED_TABLE_BATCH_TOO_LARGE",
            "各项回执合计 %d 字节，超过 %d（DMN 2.0 §八）" % (total, ITEMS_BYTES_MAX),
            "item_result_jsons",
        )
    except RouteTableError as exc:
        answer["error"] = {"code": exc.code, "message": exc.message, "path": exc.path}
        return answer, []
    answer.update(
        status="SUCCEEDED",
        count=len(texts),
        statuses=[e["status"] for e in envelopes],
    )
    return answer, texts
