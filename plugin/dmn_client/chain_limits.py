"""由 vectors/主链路v1/gen_chain_limits.py 从 主链路限额表.v1.json 生成，不手改。"""
import json as _json

_T = _json.loads(r"""{"cells":{"PREFETCH_FRESHNESS":"service.failure.valid_for_s","PREFETCH_READ_TIMEOUT":"service.failure.timeout_ms"},"limits":{"CANDIDATE_LABEL_CHARS":32,"DIFY_CODE_STRING_OUTPUT_CHARS":80000,"ENTITY_FACTS_MAX":64,"ERROR_TEXT_LIMIT":4000,"EXIT_WORDING_CHARS":120,"FILTER_WINDOW_DAYS_MAX":3650,"FRAME_TEXT_LIMIT":60000,"INTAKE_READ_RETRY_BACKOFF_MS":[1000,3000],"INTAKE_READ_RETRY_MAX":3,"ISSUES_PER_ROUND_MAX":20,"LOCATE_POLL_MS":300,"LOCATE_READ_TRIES":4,"LOCATE_WAIT_MS":20000,"PACKET_CHUNKS_MAX":12,"PLATFORM_RUN_RECORD_INPUT_CHARS":4000,"PREFETCH_RUN_BUDGET_MS":45000,"PREFETCH_STREAM_WAIT_MS":15000,"READBACK_LINE_BYTES":262144,"RECORDS_RESEND_BACKOFF_MS":[2000,30000],"RECORDS_RESEND_MAX":8,"ROWS_PER_SECTION_MAX":300,"ROW_KEY_CHARS":128,"RUN_OVERDUE_MS":180000,"RUN_RECEIPT_FRAMES_MAX":41,"SCENE_CATALOG_READ_CHARS":300000,"SUPPLEMENT_ALTERNATIVES_MAX":8,"SUPPLEMENT_GROUPS_MAX":16,"SUPPLEMENT_LABEL_CHARS":128,"SUPPLEMENT_OBJECTS_MAX":8,"TOPIC_OPTIONS_MAX":64,"TRIAGE_BLOCK_CHARS":32768,"V5_REQUESTS_PACK_CHARS":4000000},"schema":"main-chain-limits/v1"}""")
SCHEMA = _T["schema"]
LIMITS = _T["limits"]
CELLS = _T["cells"]


def limit(k):
    if k not in LIMITS:
        raise KeyError("LIMIT_UNKNOWN:" + k)
    return LIMITS[k]


def capability_cell(row, k):
    """超时、新鲜期只从能力表那一行「怎么供」一格取；取不到返回 None，读方照实记，不拿默认数顶。"""
    if k not in CELLS:
        raise KeyError("LIMIT_CELL_UNKNOWN:" + k)
    cur = row
    for p in CELLS[k].split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur if isinstance(cur, (int, float)) and not isinstance(cur, bool) else None
