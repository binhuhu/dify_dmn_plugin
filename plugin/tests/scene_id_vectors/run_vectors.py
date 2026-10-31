#!/usr/bin/env python3
"""scene-id-grammar/v1 vector runner (Python, stdlib only).

Runs every case of an explicitly supplied fixture against the implementation given with --impl.

    python3 -I run_vectors.py --impl <file> --vectors <file> [--show <n>]

Exit 0 only when every runnable case passes, the stamp equals sha256(vectors), the pattern constants equal the
vectors' patterns and every named function exists. Cases that need the generated alias map (scene-id-alias.v1.json,
provided separately) are DEFERRED while that file does not exist: reported, never counted as passed.

For a public derivative fixture, pass --public-manifest <file>. This explicitly checks the derivative's
own SHA-256 and case/group counts while preserving the upstream implementation grammar identity.
"""
import copy
import datetime
import hashlib
import importlib.util
import json
import os
import platform
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def opt(argv, name, dflt):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return dflt


def canon(v):
    """JSON-strict comparison key: type-exact (true != 1), key order free."""
    return json.dumps(v, sort_keys=True, ensure_ascii=False, default=lambda o: '<%s>' % type(o).__name__)


class Deferred(Exception):
    pass


MISSING = object()


def main(argv):
    vector_arg = opt(argv, '--vectors', None)
    if not vector_arg or vector_arg.startswith('--'):
        print('CHECK_FAILED explicit --vectors path required')
        return 2
    vectors_path = os.path.abspath(vector_arg)
    impl_path = os.path.abspath(opt(argv, '--impl', os.path.join(HERE, 'scene_id.py')))
    show = int(opt(argv, '--show', '25'))
    with open(vectors_path, 'rb') as f:
        raw = f.read()
    sha = hashlib.sha256(raw).hexdigest()
    V = json.loads(raw.decode('utf-8'))
    vectors_dir = os.path.dirname(vectors_path)
    public_manifest = None
    if '--public-manifest' in argv:
        try:
            manifest_path = opt(argv, '--public-manifest', None)
            if not manifest_path or manifest_path.startswith('--'):
                raise ValueError('missing public manifest path')
            with open(manifest_path, encoding='utf-8') as f:
                public_manifest = json.load(f)
            if public_manifest.get('schema') != 'scene-id-public-fixture-manifest/v1':
                raise ValueError('public manifest schema mismatch')
            if V.get('schema') != 'scene-id-public-vectors/v1':
                raise ValueError('public fixture schema mismatch')
            if public_manifest.get('PUBLIC_FIXTURE_SHA256') != sha:
                raise ValueError('public fixture SHA-256 mismatch')
            if type(public_manifest.get('groups')) is not int or public_manifest['groups'] != len(V['groups']):
                raise ValueError('public fixture group count mismatch')
            if type(public_manifest.get('cases')) is not int or public_manifest['cases'] != sum(len(g['cases']) for g in V['groups']):
                raise ValueError('public fixture case count mismatch')
            upstream = public_manifest.get('upstream_grammar')
            if not isinstance(upstream, str) or re.fullmatch(re.escape(V['grammar']) + r'@[0-9a-f]{64}', upstream) is None:
                raise ValueError('upstream grammar identity invalid')
            external_cases = sum('requires' in c for g in V['groups'] for c in g['cases'])
            if type(public_manifest.get('deferred_external_cases')) is not int or public_manifest['deferred_external_cases'] != external_cases:
                raise ValueError('deferred external case count mismatch')
        except (OSError, ValueError, KeyError, TypeError) as e:
            print('CHECK_FAILED public manifest: %s' % e)
            return 2

    impl, load_error = None, None
    try:
        if not os.path.exists(impl_path):
            raise FileNotFoundError('no such file')
        spec = importlib.util.spec_from_file_location('scene_id_under_test', impl_path)
        impl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(impl)
    except Exception as e:  # noqa: BLE001 - report any load failure
        impl, load_error = None, e

    map_cache = {}

    def sub(a):
        if isinstance(a, dict) and list(a.keys()) == ['$missing']:
            return {}.get('absent')  # a field the record does not have: dict.get -> None
        if isinstance(a, dict) and list(a.keys()) == ['$fixture']:
            if a['$fixture'] not in V['fixtures']:
                raise RuntimeError('VECTORS_FIXTURE_MISSING ' + a['$fixture'])
            return copy.deepcopy(V['fixtures'][a['$fixture']])
        if isinstance(a, dict) and list(a.keys()) == ['$map']:
            f = os.path.join(vectors_dir, a['$map'])
            if not os.path.exists(f):
                raise Deferred('needs ' + a['$map'])
            if f not in map_cache:
                with open(f, encoding='utf-8') as fh:
                    map_cache[f] = json.load(fh)
            return copy.deepcopy(map_cache[f])
        return a

    def effect_counts(m):
        out = {}
        for r in m.get('aliases') or []:
            out.setdefault(r['epoch'], {})
            out[r['epoch']][r['effect']] = out[r['epoch']].get(r['effect'], 0) + 1
        return out

    intrinsic = {'$effect_counts': effect_counts}

    def fn_of(spec_name):
        if spec_name.startswith('$'):
            if spec_name not in intrinsic:
                raise RuntimeError('RUNNER_INTRINSIC_MISSING ' + spec_name)
            return intrinsic[spec_name]
        n = (V['names'].get(spec_name) or {}).get('py')
        if not n:
            raise RuntimeError('VECTORS_NAME_MISSING ' + spec_name)
        f = getattr(impl, n, None)
        if not callable(f):
            raise RuntimeError('FN_MISSING ' + n)
        return f

    table_ops = {
        'get': lambda t, op: t.get(sub(op['key']), MISSING),
        'has': lambda t, op: sub(op['key']) in t,
        'size': lambda t, op: len(t),
        'keys': lambda t, op: list(t.keys()),
        'source_key': lambda t, op: t.source_key(sub(op['key'])),
    }

    def table_input(inp, group):
        # case_shapes.native: objects JSON cannot carry, built natively (set, date, an already-built table).
        if 'native' in inp:
            if inp['native'] == 'set':
                return set(inp['items'])
            if inp['native'] == 'date':
                return datetime.date(1970, 1, 1)
            if inp['native'] == 'scene_table':
                return fn_of(group['fn'])(copy.deepcopy(inp['object']))
            raise RuntimeError('VECTORS_NATIVE ' + str(inp['native']))
        if 'object' in inp:
            return copy.deepcopy(inp['object'])
        if 'entries' in inp:
            return [tuple(e) if isinstance(e, list) and len(e) == 2 else copy.deepcopy(e) for e in inp['entries']]
        if 'raw' in inp:
            return sub(copy.deepcopy(inp['raw']))
        raise RuntimeError('VECTORS_TABLE_INPUT')

    error_cls = getattr(impl, 'SceneIdError', None) if impl else None

    def run_case(group, c):
        expect_throw = 'throws' in c
        try:
            if group['name'] == 'validator_patterns':
                p = c['args'][0]
                got = {'accept': [s for s in c['expect']['accept'] if re.fullmatch(p, s)],
                       'reject': [s for s in c['expect']['reject'] if not re.fullmatch(p, s)]}
            elif group['name'] == 'census':
                got = [[m.group(0), 'C' if m.group(1) else 'L'] for m in re.finditer(V['patterns']['embedded'], c['args'][0])]
            elif 'input' in c:
                t = fn_of(group['fn'])(table_input(c['input'], group))
                if expect_throw:
                    return 'fail', 'expected throw %s, got a table' % c['throws']
                for op in c['ops']:
                    r = table_ops[op['op']](t, op)
                    if op.get('missing'):
                        if r is not MISSING or (sub(op['key']) in t):
                            return 'fail', 'op %s(%r) expected missing, got %s' % (op['op'], op['key'], canon(r))
                    elif r is MISSING or canon(r) != canon(op['expect']):
                        return 'fail', 'op %s(%r) expected %s, got %s' % (op['op'], op.get('key', ''), canon(op['expect']), 'missing' if r is MISSING else canon(r))
                return 'pass', None
            else:
                chain = c.get('pipe') or [group['fn']]
                args = [sub(a) for a in c['args']]
                got = fn_of(chain[0])(*args)
                for f in chain[1:]:
                    got = fn_of(f)(got)
        except Deferred as e:
            return 'deferred', str(e)
        except Exception as e:  # noqa: BLE001
            code = getattr(e, 'code', None)
            if expect_throw and code == c['throws']:
                if error_cls is not None and not isinstance(e, error_cls):
                    return 'fail', 'raised error is not a SceneIdError'
                detail = getattr(e, 'detail', None) or {}
                for k, v in (c.get('detail') or {}).items():
                    if canon(detail.get(k)) != canon(v):
                        return 'fail', 'detail.%s expected %s, got %s' % (k, canon(v), canon(detail.get(k)))
                return 'pass', None
            prefix = ('expected throw %s, got ' % c['throws']) if expect_throw else 'raised '
            return 'fail', prefix + ((code + ' ') if code else '') + '%s: %s' % (type(e).__name__, e)
        if expect_throw:
            return 'fail', 'expected throw %s, got %s' % (c['throws'], canon(got))
        if canon(got) == canon(c['expect']):
            return 'pass', None
        return 'fail', 'expected %s, got %s' % (canon(c['expect']), canon(got))

    checks = []
    expect_stamp = '%s@%s' % (V['grammar'], sha)
    if public_manifest is not None:
        expect_stamp = public_manifest['upstream_grammar']
    if impl:
        checks.append(('stamp', getattr(impl, 'SCENE_ID_GRAMMAR', None) == expect_stamp,
                       'SCENE_ID_GRAMMAR = %r; vectors want %r' % (getattr(impl, 'SCENE_ID_GRAMMAR', None), expect_stamp)))
        for k, cname in V['pattern_constants'].items():
            checks.append(('pattern ' + cname, getattr(impl, cname, None) == V['patterns'][k], '%s = %r' % (cname, getattr(impl, cname, None))))
        for spec_name, n in V['names'].items():
            checks.append(('name ' + spec_name, callable(getattr(impl, n['py'], None)), 'def ' + n['py']))
        for cname in V['constants']:
            checks.append(('constant ' + cname, hasattr(impl, cname), cname))

    n_pass = n_fail = n_def = 0
    failures, deferred_why, per_group = [], set(), []
    for group in V['groups']:
        g = {'name': group['name'], 'pass': 0, 'fail': 0, 'deferred': 0}
        for c in group['cases']:
            if impl:
                status, why = run_case(group, c)
            elif c.get('requires') and not os.path.exists(os.path.join(vectors_dir, c['requires'])):
                status, why = 'deferred', 'needs ' + c['requires']
            else:
                status, why = 'fail', 'IMPL_LOAD_FAILED'
            g[status] += 1
            if status == 'pass':
                n_pass += 1
            elif status == 'deferred':
                n_def += 1
                deferred_why.add(why)
            else:
                n_fail += 1
                failures.append((c['id'], why, c.get('note')))
        per_group.append(g)

    print('scene-id-grammar vectors · Python · %s %s' % (platform.python_implementation(), platform.python_version()))
    print('vectors %s\n  sha256 %s' % (vectors_path, sha))
    print('impl    %s' % impl_path)
    if load_error is not None:
        print('IMPL_LOAD_FAILED %s: %s' % (type(load_error).__name__, load_error))
    for g in per_group:
        print('  %-20s pass %4d  fail %4d  deferred %3d' % (g['name'], g['pass'], g['fail'], g['deferred']))
    bad = [c for c in checks if not c[1]]
    for c in bad:
        print('CHECK_FAILED %s: %s' % (c[0], c[2]))
    for cid, why, note in failures[:show]:
        print('FAIL %s: %s%s' % (cid, why, ('  [%s]' % note) if note else ''))
    if len(failures) > show:
        print('… %d more failures (--show N)' % (len(failures) - show))
    for d in sorted(deferred_why):
        print('DEFERRED %s (external alias map unavailable)' % d)
    if public_manifest is not None:
        print('EXTERNAL_FIXTURE_CASES %d (actual deferred count reported below)' % public_manifest['deferred_external_cases'])
    runnable = n_pass + n_fail
    ok = load_error is None and n_fail == 0 and not bad
    pct = (100.0 * n_pass / runnable) if runnable else 0.0
    print('RESULT %s · runnable %d · passed %d (%.1f%%) · failed %d · deferred %d · checks %d/%d%s' % (
        'PASS' if ok else 'FAIL', runnable, n_pass, pct, n_fail, n_def, len(checks) - len(bad), len(checks),
        '' if impl else ' (no implementation)'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
