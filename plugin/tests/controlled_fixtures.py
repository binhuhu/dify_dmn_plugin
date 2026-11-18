"""A toy controlled entry for tests (2026-11-18). The key below is a toy: generated for these tests, used nowhere else,
and never to be listed in controlled_tables.json (test_controlled_tables checks that). Tests that write a controlled
table register it with `toy_entry` and sign with `signed_body`."""

import hashlib
import itertools
from datetime import datetime, timedelta, timezone

from dmn_client import controlled

TOY_ID = "toy-entry"
TOY_E = 65537
TOY_N = int(
    "ba2b64cab508daf943524c00aa08bbe985dfd4375cb812df97bb84c62e7fea4c5896e5c1357f17631f749647750f7bb7"
    "c317d3dcbced59e1ba97455b701d48d4eab6e5a6e5a749ba43a14ca13e1bce2000f2963e3af85b7637c0fcee9bb6a384"
    "033ca09fb0430ebdde0ebd2bd22aaf2abfe6dfb22986bbc62e6db78abfddba906cdedf300d5eccc46b22e754bf0f35d4"
    "0ec2b01a770b00e674b0da2504d855aeb20fc4a11fd9129afd91c29bb89a3d55de0312c48b6f9d416a40ff31f81af828"
    "3a609e84d32d5a730284485bf724b0ee19410d5407b243f3ac4cc978c80841e4500736b1efc8b5be032e789d63ce4d57"
    "31bc8a713a971cb0d0a5e5073001c0ba87c56aa091d651c2a67531e528e4b83b8aac184b77e74b300fb594f70b991f3e"
    "fa31317c8d8e7163c534c030950a8de464bf681495a09230b8fe27320cff92700e384d4204cc5a13e2103e737a0f3bd8"
    "99973dab4d599cbde47f9edc77345a5e31daae03c67b93bf912d793e67057396f17e446bad30e41da21aa2fc6f8dc945",
    16,
)
TOY_D = int(
    "c0da0ad88c48816db7f61c928e3beb4d069d78876673ade413ee45fb2102e92c9a935655f09b986d2f91e5205e2737e5"
    "d8776a6f93abdc1c20351667ee45526e72e61c1293d12e682c297f3ed91e91f19d3459bc5aa2193ce697486c74bef1b6"
    "e3137ee0c2aff47ec2f549b431f67d9684a1c96b69e664709bcfe9a8c05b69d40ab3a5481b26efbfd901432f60418565"
    "5d6a5865a743ad7463152b7a85abaf61549b4e4eb56077618ef8863c2c8865594f5952566a8afe155c2dbb2e2c46e3cb"
    "7617e684f5667c312d114291125a25a5746e5c2ade583805a0d0d56ff7514a915120318cca4c2ddf95653421ce72cf94"
    "6970dba35379ac155d3617861711d1a42bcc584b8f857a0681249cdd212d8fdce948a7ce910996a1c9eb36be02768659"
    "a4bcc08de22b0b4ddfc34d8184a7e2b57eab104ac47b01b47dcc6f6679e534624d9caea037b44642f9075297aa54b443"
    "ef0faa41f7f68d7da8a7385d36a3a4c4e6f54f0328085fbafc9fd0af8939e8a9770c3f019dbcbb9286497315b7247f9",
    16,
)
# what `openssl dgst -sha256 -sign` gives for VECTOR_MESSAGE with the toy key (the way 81's entry signs)
VECTOR_MESSAGE = (
    "dmn-store-entry-sig/1\nsave\ntarget.scene.topic\n\n"
    + "0" * 64
    + "\ntoy-entry\n2030-01-02T12:00:00Z"
).encode("utf-8")
OPENSSL_SIG = (
    "b00e9094dae65ef23205af25408710f2ace3d8df0d644715c742510b1fc63b7c18d77aec029ad66b3a7f1ea086fdbaa7"
    "ba9a99ccd075f04d09229291549605997ca96126a9301729c728bd0c867d798b77bfabe97cdc2d0fe4f0bb4203a53ef8"
    "9961a6d40de8838219ef882dec3f0968b5abf469eb1bc0a2cd0c92e2a785ca255d678379787975d355b325410db04a78"
    "2ba2df72ef8dd7c3c874345942df80d8fbb7368d1ed87b8f9d4991b264c4609250e1de5d2a82adc53f4a647acdb7d22a"
    "03eef0a2064394d9789766fc52f95cb50e0779db4d230b41cd90456ad15bd44238def6d2f4d9f3ed1b6ca5ced028a3f7"
    "3e6bdc96b6ffd13041af315c3958d697ed6c5238c3ca262e75be471689675323002e4fa9fb81df08adf24cc570afffa8"
    "b12f311298fdf0cd7b9eafcb14f4f771163fc3cedf629cb0d4fdb627e75fae9e49cc559e29aac20f37771ff7c7316b0b"
    "1700f817786cc068ba17ffaf6cad3adc29be73af99af9afcfba3684c49852539be5a52525b82ccab35f3f46c47400034"
)

NOW = datetime(2030, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
STAMP = "%Y-%m-%dT%H:%M:%SZ"
_clock = itertools.count()


def toy_entry(monkeypatch, entries=None):
    """Register the toy entry (or the given {id: (n, e)}) as the release's entries, for one test."""
    global _clock
    _clock = itertools.count()
    monkeypatch.setattr(
        controlled, "ENTRIES", {TOY_ID: (TOY_N, TOY_E)} if entries is None else entries
    )


def rs256_sign(msg, n=TOY_N, d=TOY_D):
    k = (n.bit_length() + 7) // 8
    t = controlled.SHA256_INFO + hashlib.sha256(msg).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return format(pow(int.from_bytes(em, "big"), d, n), "0%dx" % (2 * k))


def entry_for(op, table, version, body, at=None, entry_id=TOY_ID, d=TOY_D, n=TOY_N):
    """The entry block a controlled entry sends with this request. Each call without `at` takes a time a second before
    the last (within ten minutes of NOW), so two requests with the same body are two signed requests."""
    if at is None:
        at = (NOW - timedelta(seconds=next(_clock))).strftime(STAMP)
    msg = controlled.message(op, table, version, controlled.body_sha256(body), entry_id, at)
    return {"id": entry_id, "at": at, "sig": rs256_sign(msg, n, d)}


def signed_body(op, table, version, body, **kw):
    body = dict(body or {})
    body["entry"] = entry_for(op, table, version, body, **kw)
    return body
