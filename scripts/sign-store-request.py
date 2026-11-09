"""Sign a route_table_store request as a named writer (0.5.24, D14).

The writer's own key is read from the environment variable DMN_WRITER_KEY (never an argument: it would land in shell
history and process lists). The request body is read from stdin as a JSON object; the signed body (with writer
{id, at, sig}) is printed. op, table and version are the ones the maintenance app will be run with (version: the
op=version parameter, or nothing). A signature is good for 10 minutes.

    DMN_WRITER_KEY=... python3 -I scripts/sign-store-request.py --writer synthetic-writer --op draft_save \\
        --table triage.dispatch.route < body.json > signed.json

0.5.26: --fingerprint prints only the key_fp of DMN_WRITER_KEY; compare it with op=writers before signing.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin"))
from dmn_client.two_state import key_fingerprint, sign  # noqa: E402


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--writer")
    p.add_argument("--op")
    p.add_argument("--table", default="dispatch.route")
    p.add_argument("--version", default="")
    p.add_argument(
        "--fingerprint",
        action="store_true",
        help="print only the key_fp of DMN_WRITER_KEY (compare with op=writers) and exit",
    )
    a = p.parse_args()
    key = os.environ.get("DMN_WRITER_KEY", "")
    if len(key) < 24:
        sys.exit("DMN_WRITER_KEY is not set or shorter than 24 characters")
    if a.fingerprint:
        print(key_fingerprint(key))
        return
    if not a.writer or not a.op:
        p.error("--writer and --op are required to sign")
    body = json.load(sys.stdin)
    if not isinstance(body, dict):
        sys.exit("the request body must be a JSON object")
    body.pop("writer", None)
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body["writer"] = {
        "id": a.writer,
        "at": at,
        "sig": sign(key, a.op, a.table, a.version, body, a.writer, at),
    }
    json.dump(body, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
