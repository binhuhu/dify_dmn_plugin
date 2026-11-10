(() => {
  'use strict';
  // 按场景列出现网定位的全部场景，每行选一个去向（方案应用 / 转安全 / 无方案）；方案应用先在「方案登记」里登记。
  // 只经本端点的 /api/* 读写插件存储，不执行规则、不连别的地址。DOM API + textContent 出字，不拼 HTML。
  // 0.5.24（D14）：没有共用编辑口令。每个请求用你自己的口令签名（HMAC-SHA256，口令不发出去），请求头 X-Route-Writer
  // 带名字、时间、签名；「路由表维护」应用核签名。交付链五张表先存草稿、再带闸报告和发布批次或变更编号发布。口令只放本标签页的 sessionStorage。


  // 同一场景两种写法一起导入就拒；存后插件若换了写法，提示里说换了几处。
  const KEY_STORE = 'route-writer-key', WRITER_STORE = 'route-writer-id';
  const SCHEMA = 'dmn-json.route-table/v2';
  const SCHEMA_V3 = 'dmn-json.route-table/v3';
  const STATUS = [['CURRENT', '可调用'], ['CANDIDATE', '候选，不调'], ['GATED', '闸挡着，不调'], ['SHADOW', '影子，不调'], ['RETIRED', '退役，不调']];
  const GATE_STATUS = ['GATED', 'SHADOW'];
  const DEFAULTS = { status: 'CURRENT', gate_code: null, request_contract: null, topics: [], intents: [], invocation_scope: null, priority: null, package_id: null, target_handoff: null };
  const FIELD_LABEL = { name: '名称', inputs: '开始变量', status: '状态', gate_code: '闸', request_contract: '请求合同', topics: '话题', intents: '诉求' };
  const STATES = [['ALL', '全部'], ['SOLUTION_APP', '方案应用'], ['TRANSFER', '转安全'], ['NONE', '无方案']];
  const TYPE_LABEL = { COMPLAINT: '投诉', CONSULT: '咨询', FEEDBACK: '反馈' };
  const CONFLICT = ['ROUTE_TABLE_STALE', 'ROUTE_TABLE_RACE'];
  const SCENE_ID = /^(M_)?P_\d{2}_\d{2}_\d{2}$/;
  const SPELLING_LABEL = { LEGACY: '旧写法 P_', CANONICAL: '新写法 M_P_' };
  // 一张草稿（场景目录键 + 去向键）的写法：全 M_ 为 true，全旧写法为 false，混或空为 null。
  const spellingOf = (d) => { const ids = [...Object.keys(d.scenes), ...[...d.routes.keys()].map(k => k.split('|')[0])]; return !ids.length ? null : ids.every(x => x.startsWith('M_')) ? true : ids.every(x => !x.startsWith('M_')) ? false : null; };
  const spellAs = (sid, canonical) => canonical === null || !SCENE_ID.test(sid) ? sid : canonical ? 'M_' + sid.replace(/^M_/, '') : sid.replace(/^M_/, '');
  const app = document.getElementById('app');
  const base = (() => { const p = location.pathname; return p.endsWith('/') ? p : p.endsWith('/index.html') ? p.slice(0, -'index.html'.length) : p + '/'; })();
  const state = { key: readKey(), writer: readWriter(), serverDraft: null, releaseRef: '', table: 'dispatch.route', loaded: false, doc: null, baseDoc: null, version: 0, draft: null, basedOn: 0, history: [],
    busy: false, msg: null, confirming: false, conflict: false, errKey: null, errApp: null,
    view: { issue_type: 'COMPLAINT', phase: 'STAGE_1', domain: '', kind: 'ALL', q: '' }, exported: '', newApp: { app_id: '', name: '', inputs: 'call_id request_json' },
    gateText: '' };

  function readKey() { try { return sessionStorage.getItem(KEY_STORE) || ''; } catch (_) { return ''; } }
  function readWriter() { try { return sessionStorage.getItem(WRITER_STORE) || ''; } catch (_) { return ''; } }
  function keepKey(k, w) { try { sessionStorage.setItem(KEY_STORE, k); sessionStorage.setItem(WRITER_STORE, w); } catch (_) { /* 私密窗口等：只在本页内存里 */ } }
  function el(tag, props, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'text') n.textContent = String(v); else if (k === 'value') n.value = v; else n.setAttribute(k === 'cls' ? 'class' : k, v === true ? '' : String(v));
    }
    for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) n.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid);
    return n;
  }
  const opt = (value, text, selected) => { const o = el('option', { value, text }); if (selected) o.selected = true; return o; };
  const words = s => String(s || '').split(/[\s,，、]+/).map(x => x.trim()).filter(Boolean);
  const key3 = (s, t, p) => s + '|' + t + '|' + p;
  const appLink = id => el('a', { href: location.origin + '/v2/app/' + encodeURIComponent(id) + '?mode=workflow', target: '_blank', rel: 'noopener noreferrer', text: '打开 ↗' });
  // 按键排序后比较：存储和接口回来的键序不同。
  const canon = v => Array.isArray(v) ? '[' + v.map(canon).join(',') + ']' : v && typeof v === 'object'
    ? '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + canon(v[k])).join(',') + '}' : JSON.stringify(v ?? null);

  // 签名：插件按 RFC 8785 规范化请求体（去掉 writer）再取 sha256；本页发的都是对象、字符串、整数，排序键 + JSON.stringify 就是 RFC 8785。
  const GET_OP = { table: 'table', history: 'history', version: 'version', draft: 'draft' };
  const POST_OP = { table: 'save', draft: 'draft_save', discard: 'draft_discard', publish: 'publish' };
  const enc = new TextEncoder();
  const hex = b => [...new Uint8Array(b)].map(x => x.toString(16).padStart(2, '0')).join('');
  const jcs = v => Array.isArray(v) ? '[' + v.map(x => x === undefined ? 'null' : jcs(x)).join(',') + ']' : v && typeof v === 'object'
    ? '{' + Object.keys(v).filter(k => v[k] !== undefined).sort().map(k => JSON.stringify(k) + ':' + jcs(v[k])).join(',') + '}' : JSON.stringify(v);
  async function writerHeader(op, version, body) {
    const at = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
    const digest = hex(await crypto.subtle.digest('SHA-256', enc.encode(jcs(body || {}))));
    const msg = ['dmn-store-sig/1', op, state.table, version, digest, state.writer, at].join('\n');
    const k = await crypto.subtle.importKey('raw', enc.encode(state.key), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
    return state.writer + ';' + at + ';' + hex(await crypto.subtle.sign('HMAC', k, enc.encode(msg)));
  }
  async function api(path, body) {
    const [name, query] = path.split('?'), pinned = new URLSearchParams(query || '').get('v');
    const op = (body ? POST_OP : GET_OP)[name] || '-';
    const r = await fetch(base + 'api/' + path, { method: body ? 'POST' : 'GET', cache: 'no-store', credentials: 'omit', redirect: 'error',
      headers: { 'X-Route-Writer': await writerHeader(op, name === 'version' && pinned != null ? pinned : '', body), ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined });
    let v; try { v = await r.json(); } catch (_) { throw Error('服务没返回 JSON（' + r.status + '）'); }
    if (!r.ok || v.error) { const e = Error(v.error?.message || '请求失败（' + r.status + '）'); e.code = v.error?.code; e.path = v.error?.path || ''; throw e; }
    return v;
  }
  const q = name => name + '?table=' + encodeURIComponent(state.table);


  const routeDoc = doc => doc && (doc.schema === SCHEMA || doc.schema === SCHEMA_V3) ? doc : null;
  const isV3 = d => d.schema === SCHEMA_V3;
  // 0.5.32 试验方案：表里写在行上（row.trial），草稿里跟着去向走（target.trial），这样改动对比、冲突重放都带着它；toDoc 再放回行上。
  const MIN_TRIAL_SHARE = 0, MAX_TRIAL_SHARE = 100;
  const withTrial = (t, trial) => { if (t && trial !== undefined) t.trial = structuredClone(trial); return t; };
  const rowOf = (r, t) => { const { trial, ...target } = structuredClone(t); const row = { ...r, target }; if (trial === undefined) delete row.trial; else row.trial = trial; return row; };
  function toDraft(doc) {
    const routes = new Map();
    for (const r of doc?.routes || []) routes.set(key3(r.scene_id, r.issue_type, r.phase), withTrial(structuredClone(r.target), r.trial));
    return { schema: doc?.schema || SCHEMA, issue_types: doc?.issue_types || ['COMPLAINT', 'CONSULT', 'FEEDBACK'], phases: doc?.phases || ['STAGE_1', 'STAGE_2', 'STAGE_3'],
      scenes: structuredClone(doc?.scenes || {}), solutions: structuredClone(doc?.solutions || {}), routes, note: '', base: structuredClone(doc || null) };
  }

  function upgrade(d) {
    d.schema = SCHEMA_V3;
    for (const [id, s] of Object.entries(d.solutions)) d.solutions[id] = { name: s.name, inputs: s.inputs, ...structuredClone(DEFAULTS), ...s };
  }
  // 以前只写七个键，存表会丢掉 resource_table。
  function toDoc(d) {
    const base = structuredClone(d.base || {}), routes = [], kept = new Set();
    for (const r of base.routes || []) {
      const k = r && key3(r.scene_id, r.issue_type, r.phase);
      if (!r || kept.has(k) || !d.routes.has(k)) continue; // 删掉的去向（选「无方案」）不再写
      kept.add(k);
      const t = d.routes.get(k);
      routes.push(canon(t) === canon(withTrial(structuredClone(r.target), r.trial)) ? r : rowOf(r, t));
    }
    for (const [k, target] of d.routes) {
      if (kept.has(k)) continue;
      const [scene_id, issue_type, phase] = k.split('|');
      routes.push(rowOf({ scene_id, issue_type, phase }, target));
    }
    const doc = Object.assign(base, { schema: d.schema, table: state.table, routes, note: d.note.trim() });
    for (const f of ['issue_types', 'phases', 'scenes', 'solutions']) if (canon(base[f]) !== canon(d[f])) doc[f] = structuredClone(d[f]);
    return doc;
  }
  const kindOf = t => t ? t.kind : 'NONE';
  const notCurrent = s => s && s.status && s.status !== 'CURRENT' ? ' · ' + s.status : '';
  const label = (t, d) => !t ? '无方案' : t.kind === 'TRANSFER' ? '转安全' : t.kind === 'RESOURCE' ? '资源目标 · ' + t.resource_id
    : (d.solutions[t.app_id]?.name || '未登记的应用') + ' · ' + String(t.app_id || '').slice(0, 8) + notCurrent(d.solutions[t.app_id]) + trialLabel(t, d);
  const trialLabel = (t, d) => !t?.trial ? '' : ' ＋ 试验 ' + (d.solutions[t.trial.app_id]?.name || (t.trial.app_id ? '未登记的应用' : '未选应用')) + ' · ' + String(t.trial.app_id || '').slice(0, 8) + notCurrent(d.solutions[t.trial.app_id]) + ' ' + t.trial.share + '%';
  const appsOf = t => t?.kind === 'SOLUTION_APP' ? [t.app_id, ...(t.trial?.app_id ? [t.trial.app_id] : [])] : [];

  // 草稿和某一版比：去向、方案登记、场景目录各变了什么。
  function changesAgainst(doc, d) {
    const before = toDraft(routeDoc(doc)), routes = [], solutions = [];
    for (const k of new Set([...before.routes.keys(), ...d.routes.keys()])) {
      const a = before.routes.get(k), b = d.routes.get(k);
      if (canon(a) !== canon(b)) routes.push([k, a, b]);
    }
    for (const id of new Set([...Object.keys(before.solutions), ...Object.keys(d.solutions)])) {
      const a = before.solutions[id], b = d.solutions[id];
      if (canon(a) !== canon(b)) solutions.push([id, a, b]);
    }
    return { routes, solutions, scenes: canon(before.scenes) !== canon(d.scenes), schema: before.schema !== d.schema, before };
  }
  const changes = d => changesAgainst(state.doc, d);

  function routeProblems(d) {
    const out = [];
    for (const [k, t] of d.routes) {
      const sid = k.split('|')[0];
      if (!d.scenes[sid]) out.push([k, '场景 ' + sid + ' 不在场景目录里']);
      else if (t.kind === 'SOLUTION_APP' && !d.solutions[t.app_id]) out.push([k, sid + ' 指向未登记的应用 ' + t.app_id.slice(0, 8)]);
      else if (t.trial !== undefined) { // 同插件的拒存规则（route_table._trial）
        const x = t.trial, app = x && x.app_id, s = app && d.solutions[app];
        if (!isV3(d)) out.push([k, sid + ' 挂试验要用 v3 路由表']);
        else if (t.kind !== 'SOLUTION_APP') out.push([k, sid + ' 的去向不是方案应用，不能挂试验']);
        else if (!x || typeof x !== 'object' || Array.isArray(x)) out.push([k, sid + ' 的试验写法不对（一行最多一个试验方案）']);
        else if (!app) out.push([k, sid + ' 的试验方案还没选应用']);
        else if (!s) out.push([k, sid + ' 的试验方案指向未登记的应用 ' + String(app).slice(0, 8)]);
        else if (app === t.app_id) out.push([k, sid + ' 的试验方案不能和原去向是同一个应用']);
        else if (s.status && s.status !== 'CURRENT') out.push([k, sid + ' 的试验方案 ' + app.slice(0, 8) + ' 是 ' + s.status + '：试验方案要是 CURRENT']);
        else if (d.solutions[t.app_id].status && d.solutions[t.app_id].status !== 'CURRENT') out.push([k, sid + ' 的原去向 ' + t.app_id.slice(0, 8) + ' 是 ' + d.solutions[t.app_id].status + '：原去向挂起的行不能挂试验']);
        else if (!Number.isInteger(x.share) || x.share < MIN_TRIAL_SHARE || x.share > MAX_TRIAL_SHARE) out.push([k, sid + ' 的试验比例要是 ' + MIN_TRIAL_SHARE + '–' + MAX_TRIAL_SHARE + ' 的整数']);
      }
    }
    return out;
  }
  function solutionProblems(d) {
    if (!isV3(d)) return [];
    const out = [];
    for (const [id, s] of Object.entries(d.solutions)) {
      if (s.status === 'GATED' && !s.gate_code) out.push(['app:' + id, '方案 ' + id.slice(0, 8) + '：GATED 要写闸编码']);
      if (s.status === 'CURRENT' && !s.request_contract) out.push(['app:' + id, '方案 ' + id.slice(0, 8) + '：CURRENT 要写请求合同']);
    }
    return out;
  }
  const problems = d => [...routeProblems(d), ...solutionProblems(d)];
  function gateReport() {
    const text = state.gateText.trim();
    if (!text) return { ok: true, value: null };
    try { const v = JSON.parse(text); if (v && typeof v === 'object' && !Array.isArray(v)) return { ok: true, value: v }; } catch (_) { /* below */ }
    return { ok: false };
  }

  async function load() {
    state.busy = true; state.msg = null; state.confirming = false; state.conflict = false; state.errKey = state.errApp = null; paint();
    try {
      let [t, h, dr] = await Promise.all([api(q('table')), api(q('history')), api(q('draft')).catch(e => e.code === 'STORED_TABLE_DRAFT_NOT_SUPPORTED' ? { draft: null, none: true } : Promise.reject(e))]);
      state.serverDraft = dr.draft || null; state.twoState = !dr.none;
      // 0.5.20 (存表名即标准名): an old name read after the first store answers with the standard name; edit that one
      if (t.read_as && t.table) { state.table = t.table; h = await api(q('history')); }
      state.doc = t.doc; state.baseDoc = routeDoc(t.doc); state.version = t.version; state.history = h.versions; state.basedOn = t.version; state.loaded = true;
      state.draft = toDraft(routeDoc(t.doc));
      if (t.doc && !routeDoc(t.doc)) state.msg = { cls: 'warn', text: '插件里的 ' + state.table + ' 是旧格式（' + t.doc.schema + '），本页只编辑 v2 / v3：用「导入 JSON」放进第一版后保存。' };
      else state.msg = t.doc ? null : { cls: 'warn', text: '插件里还没有路由表 ' + state.table + '：用「导入 JSON」放进第一版（场景目录 + 方案登记 + 路由）。' };
    } catch (e) { state.msg = { cls: 'bad', text: e.message }; if (/^STORED_TABLE_WRITER/.test(e.code || '')) state.loaded = false; }
    state.busy = false; paint();
  }
  function askSave() {
    const d = state.draft; if (!d) return;
    state.errKey = state.errApp = null;
    if (!d.note.trim()) { state.msg = { cls: 'bad', text: '写一句改动说明再保存。' }; paint(); return; }
    const bad = problems(d);
    if (bad.length) {
      const k = bad[0][0];
      if (k.startsWith('app:')) state.errApp = k.slice(4); else focusRow(k);
      state.msg = { cls: 'bad', text: '先改掉这些再保存：' + bad.slice(0, 5).map(x => x[1]).join('；') + (bad.length > 5 ? '…（共 ' + bad.length + ' 处）' : '') }; paint(); return;
    }
    if (!state.twoState && !gateReport().ok) { state.msg = { cls: 'bad', text: '存前闸报告要是一个 JSON 对象（或者清空）。' }; paint(); return; }
    state.confirming = true; state.msg = null; paint();
  }
  async function save() {
    const d = state.draft; if (!d) return;
    const sent = toDoc(d), n = changes(d).routes.length, gate = gateReport().value;
    state.busy = true; state.confirming = false; state.msg = null; paint();
    try {
      if (state.twoState) {
        // 交付链五张表：只存草稿，不生效
        const r = await api(q('draft'), { ...sent, expected_version: state.version, expected_draft: state.serverDraft?.draft_id || '' });
        state.serverDraft = r.draft; state.conflict = false;
        state.msg = { cls: 'ok', text: '已存草稿（基于第 ' + r.draft.base_version + ' 版，改了 ' + n + ' 条去向，内容摘要 ' + r.draft.content_sha256.slice(0, 12) + '），还没生效。按这个摘要跑存前闸，把报告贴到下面、填写发布批次或变更编号，再发布。' };
        state.busy = false; paint(); return;
      }
      const r = await api(q('table'), { ...sent, expected_version: state.version, ...(gate ? { gate_report: gate } : {}) });
      state.doc = r.doc; state.baseDoc = r.doc; state.version = r.version; state.draft = toDraft(r.doc); state.basedOn = r.version; state.conflict = false; state.gateText = '';
      state.history = (await api(q('history'))).versions;
      state.msg = { cls: 'ok', text: '已保存第 ' + r.version + ' 版（改了 ' + n + ' 条去向，共 ' + r.doc.routes.length + ' 条路由）。现网流程下一次运行就读这一版。'
        + (r.respelled_count ? ' 插件按本表写法换了 ' + r.respelled_count + ' 处场景编号。' : '')
        + (r.scene_spelling_was ? ' 整张表的场景编号从' + SPELLING_LABEL[r.scene_spelling_was] + '换成' + SPELLING_LABEL[r.scene_spelling] + '。' : '') };
    } catch (e) {
      // 草稿原样保留；冲突时给「载入最新版并保留我的改动」，校验错误定位到出错的那一行。
      state.conflict = CONFLICT.includes(e.code);
      state.msg = { cls: 'bad', text: e.message + (state.conflict ? '' : e.path ? '（' + e.path + '）' : '') };
      const m = /^routes\[(\d+)\]/.exec(e.path || ''), s = /^solutions\.([0-9a-f-]{36})/.exec(e.path || '');
      if (m && sent.routes[Number(m[1])]) { const r = sent.routes[Number(m[1])]; focusRow(key3(r.scene_id, r.issue_type, r.phase)); }
      else if (s) state.errApp = s[1];
    }
    state.busy = false; paint();
  }
  // 0.5.24：发布草稿。要存前闸报告（核的是草稿的内容摘要）和发布批次或变更编号。
  async function publish() {
    const dr = state.serverDraft, gate = gateReport();
    if (!dr) return;
    if (!gate.ok || !gate.value) { state.msg = { cls: 'bad', text: '发布要贴存前闸报告（JSON 对象，content_sha256 等于草稿的内容摘要，gate_status 写 PASS 或 FAIL；FAIL 须附 approval_ref）。' }; paint(); return; }
    if (!state.releaseRef.trim()) { state.msg = { cls: 'bad', text: '填写发布批次或变更编号再发布。' }; paint(); return; }
    state.busy = true; state.msg = null; paint();
    try {
      const r = await api(q('publish'), { draft_id: dr.draft_id, expected_version: dr.base_version, gate_report: gate.value, release_ref: state.releaseRef.trim() });
      state.doc = r.doc; state.baseDoc = r.doc; state.version = r.version; state.draft = toDraft(r.doc); state.basedOn = r.version; state.serverDraft = null; state.gateText = ''; state.releaseRef = '';
      state.history = (await api(q('history'))).versions;
      const inv = r.published?.invariants_after;
      state.msg = { cls: 'ok', text: '已发布第 ' + r.version + ' 版，现网流程下一次运行就读这一版。发布人 ' + r.published.publisher.id + '，发布批次或变更编号 ' + r.published.release_ref + '。'
        + (inv ? ' 能不能调：' + inv.broken.length + ' 行调不到、' + inv.parked.length + ' 行挂起、' + inv.current_not_routed.length + ' 个在用方案没有路由行。' : '') };
    } catch (e) { state.msg = { cls: 'bad', text: e.message + (e.path ? '（' + e.path + '）' : '') }; }
    state.busy = false; paint();
  }
  async function discard() {
    const dr = state.serverDraft; if (!dr) return;
    state.busy = true; paint();
    try { await api(q('discard'), { draft_id: dr.draft_id }); state.serverDraft = null; state.msg = { cls: 'ok', text: '草稿已删。' }; }
    catch (e) { state.msg = { cls: 'bad', text: e.message }; }
    state.busy = false; paint();
  }
  // 冲突后：取最新版，把「我相对起点的改动」重放上去；双方都改了的同一处以我的为准，并列出来。
  async function rebase() {
    const d = state.draft; if (!d) return;
    state.busy = true; paint();
    try {
      const t = await api(q('table')), head = routeDoc(t.doc);
      const next = toDraft(head), clash = [];

      const to = spellingOf(next), key = k => { const [sid, t, p] = k.split('|'); return key3(spellAs(sid, to), t, p); };
      const base = state.baseDoc && { ...state.baseDoc, scenes: Object.fromEntries(Object.entries(state.baseDoc.scenes || {}).map(([sid, v]) => [spellAs(sid, to), v])),
        routes: (state.baseDoc.routes || []).map(r => ({ ...r, scene_id: spellAs(r.scene_id, to) })) };
      const mine = changesAgainst(state.baseDoc, d), theirs = changesAgainst(base, toDraft(head));
      const theirRoute = new Map(theirs.routes.map(([k, , b]) => [key(k), b]));
      for (const [k0, , b] of mine.routes) {
        const k = key(k0);
        if (theirRoute.has(k) && canon(theirRoute.get(k)) !== canon(b)) clash.push(k.split('|')[0]);
        if (b) next.routes.set(k, b); else next.routes.delete(k);
      }
      for (const [id, , b] of mine.solutions) { if (b) next.solutions[id] = b; else delete next.solutions[id]; }
      if (mine.scenes) next.scenes = Object.fromEntries(Object.entries(d.scenes).map(([sid, v]) => [spellAs(sid, to), v]));
      if (mine.schema) next.schema = d.schema;
      next.note = d.note;
      state.doc = t.doc; state.baseDoc = head; state.version = t.version; state.basedOn = t.version; state.draft = next; state.conflict = false;
      state.history = (await api(q('history'))).versions;
      state.msg = { cls: clash.length ? 'warn' : 'ok', text: '已载入第 ' + t.version + ' 版，并把你的 ' + mine.routes.length + ' 条去向改动重放上去，还没保存。'
        + (clash.length ? ' 这些场景别人也改了，现在按你的：' + [...new Set(clash)].slice(0, 8).join('，') : '') };
    } catch (e) { state.msg = { cls: 'bad', text: e.message }; }
    state.busy = false; paint();
  }
  async function loadVersion(v) {
    try {
      const r = await api(q('version') + '&v=' + v);
      if (!routeDoc(r.doc)) throw Error('第 ' + v + ' 版是旧格式，本页不能载入');
      if (!isV3(r.doc) && isV3(state.doc || {})) throw Error('第 ' + v + ' 版是 v2，现存的是 v3：v2 把每个方案都当 CURRENT，插件不收 v3 存回 v2，这里不载入。要回到那版的路由：导出那版、导入后升为 v3、逐个写状态再存。');
      state.draft = toDraft(r.doc); state.basedOn = v; state.baseDoc = routeDoc(state.doc); state.confirming = false;
      state.msg = { cls: 'warn', text: '已载入第 ' + v + ' 版到编辑区；写明回滚说明后按本表流程保存：受保护表须先存草稿再发布，其他表确认保存后生效。' };
    } catch (e) { state.msg = { cls: 'bad', text: e.message }; }
    paint();
  }
  function focusRow(k) {
    const [sid, t, p] = k.split('|');
    Object.assign(state.view, { issue_type: t, phase: p, domain: '', kind: 'ALL', q: sid });
    state.errKey = k;
  }

  function parse(text) { try { return JSON.parse(text); } catch (_) { state.msg = { cls: 'bad', text: '导入的内容不是 JSON。' }; paint(); return null; } }
  function importTable(text) {
    const v = parse(text); if (!v) return;
    if (!routeDoc(v) || !v.scenes || !v.solutions || !Array.isArray(v.routes)) { state.msg = { cls: 'bad', text: '要导入的是 ' + SCHEMA + ' 或 ' + SCHEMA_V3 + '：要有 scenes、solutions、routes。' }; paint(); return; }
    const note = state.draft?.note || v.note || '';
    state.draft = { ...toDraft(v), note }; state.confirming = false;
    state.msg = { cls: 'warn', text: '已导入 ' + Object.keys(v.scenes).length + ' 个场景、' + Object.keys(v.solutions).length + ' 个方案、' + v.routes.length + ' 条路由到编辑区，还没保存。' }; paint();
  }
  function importScenes(text) {
    const v = parse(text); if (!v || !state.draft) return;
    const rows = Array.isArray(v) ? v : v.scenes && !Array.isArray(v.scenes) ? Object.entries(v.scenes).map(([scene_id, s]) => ({ scene_id, ...s }))
      : Array.isArray(v.scenes) ? v.scenes : Object.entries(v).map(([scene_id, s]) => ({ scene_id, ...s }));
    const scenes = {}, seen = {};
    const canonical = spellingOf(state.draft);
    let respelled = 0;
    for (const r of rows) {
      if (!(r && SCENE_ID.test(r.scene_id || '') && r.name && r.domain)) continue;
      const raw = String(r.scene_id), bare = raw.replace(/^M_/, '');
      if (seen[bare] !== undefined && seen[bare] !== raw) { state.msg = { cls: 'bad', text: '目录里 ' + seen[bare] + ' 和 ' + raw + ' 是同一个场景的两种写法，只能留一个。' }; paint(); return; }
      seen[bare] = raw;
      const sid = spellAs(raw, canonical);
      respelled += sid !== raw;
      scenes[sid] = { name: String(r.name), domain: String(r.domain) };
    }
    if (!Object.keys(scenes).length) { state.msg = { cls: 'bad', text: '没认出场景：每项要有 scene_id、name、domain。' }; paint(); return; }
    state.draft.scenes = scenes; state.confirming = false;
    const orphan = routeProblems(state.draft);
    state.msg = { cls: orphan.length ? 'bad' : 'warn', text: '场景目录换成 ' + Object.keys(scenes).length + ' 个场景，还没保存。' + (respelled ? '（按本表写法换了 ' + respelled + ' 个场景编号）' : '') + (orphan.length ? ' 有 ' + orphan.length + ' 条路由指向目录里没有的场景，保存前先改掉：' + orphan.slice(0, 6).map(x => x[0].split('|')[0]).join('，') : '') };
    paint();
  }
  function addSolution() {
    const d = state.draft, a = state.newApp, id = a.app_id.trim().toLowerCase();
    if (!/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(id)) { state.msg = { cls: 'bad', text: '应用编号要是 AgentHub 应用 id（36 位，形如 00000000-0000-4000-8000-000000000001）。' }; paint(); return; }
    if (!a.name.trim() || !words(a.inputs).length) { state.msg = { cls: 'bad', text: '登记方案要写名称和开始变量。' }; paint(); return; }

    d.solutions[id] = isV3(d) ? { name: a.name.trim(), inputs: words(a.inputs), ...structuredClone(DEFAULTS), status: 'CANDIDATE' } : { name: a.name.trim(), inputs: words(a.inputs) };
    state.newApp = { app_id: '', name: '', inputs: 'call_id request_json' }; state.confirming = false;
    state.msg = { cls: 'warn', text: '已登记 ' + id.slice(0, 8) + '，还没保存；在场景路由里给场景选上它。' }; paint();
  }

  function sceneRows(d) {
    const v = state.view, f = v.q.trim().toLowerCase();
    return Object.entries(d.scenes).filter(([sid, s]) => {
      const t = d.routes.get(key3(sid, v.issue_type, v.phase));
      if (v.domain && s.domain !== v.domain) return false;
      if (v.kind !== 'ALL' && kindOf(t) !== v.kind) return false;
      return !f || (sid + ' ' + s.name + ' ' + (t?.app_id || '') + ' ' + (t?.trial?.app_id || '') + ' ' + (t ? label(t, d) : '')).toLowerCase().includes(f);
    });
  }
  function targetCell(sid, t, d) {
    const k = key3(sid, state.view.issue_type, state.view.phase);
    const value = !t ? 'NONE' : t.kind === 'SOLUTION_APP' ? 'APP:' + t.app_id : t.kind;
    const opts = [opt('NONE', '无方案', value === 'NONE'), opt('TRANSFER', '转安全', value === 'TRANSFER'),
      ...Object.entries(d.solutions).map(([id, s]) => opt('APP:' + id, s.name + ' · ' + id.slice(0, 8) + notCurrent(s), value === 'APP:' + id))];
    if (t?.kind === 'SOLUTION_APP' && !d.solutions[t.app_id]) opts.push(opt(value, '未登记的应用 · ' + t.app_id.slice(0, 8), true));

    if (t?.kind === 'RESOURCE') opts.push(opt('RESOURCE:' + t.resource_id, label(t, d), true));
    const box = [el('select', { 'data-k': k, 'data-f': 'target', 'aria-label': sid + ' 的去向' }, opts)];
    if (t?.kind === 'SOLUTION_APP') box.push(el('span', { cls: 're-app' }, el('code', { text: t.app_id }), ' ', appLink(t.app_id)));
    if (t?.trial !== undefined) {
      const x = t.trial && typeof t.trial === 'object' && !Array.isArray(t.trial) ? t.trial : {};
      const ok = Number.isInteger(x.share) && x.share >= MIN_TRIAL_SHARE && x.share <= MAX_TRIAL_SHARE;
      box.push(el('div', { cls: 're-trial' }, '试验方案 ',
        el('select', { 'data-k': k, 'data-f': 'trial-app', 'aria-label': sid + ' 的试验方案' },
          [opt('', '选应用…', !x.app_id), ...Object.entries(d.solutions).filter(([id]) => id !== t.app_id || id === x.app_id).map(([id, s]) => opt(id, s.name + ' · ' + id.slice(0, 8) + notCurrent(s), x.app_id === id)),
            ...(x.app_id && !d.solutions[x.app_id] ? [opt(x.app_id, '未登记的应用 · ' + String(x.app_id).slice(0, 8), true)] : [])]),
        el('input', { type: 'number', min: String(MIN_TRIAL_SHARE), max: String(MAX_TRIAL_SHARE), step: '1', size: '3', value: x.share == null ? '' : String(x.share), 'data-k': k, 'data-f': 'trial-share', 'aria-label': sid + ' 的试验比例（百分比）' }), ' % ',
        el('button', { type: 'button', cls: 'link', 'data-act': 'trial-del', 'data-k': k, text: '去掉试验' })));
      box.push(el('span', { cls: 're-app' + (ok ? '' : ' re-bad'), text: ok ? '原去向 ' + (100 - x.share) + '% · 试验方案 ' + x.share + '% · 没带分流键的进线都走原去向' : '比例写 ' + MIN_TRIAL_SHARE + '–' + MAX_TRIAL_SHARE + ' 的整数' }));
      if (x.note) box.push(el('span', { cls: 're-app', text: '试验说明：' + x.note }));
    } else if (t?.kind === 'SOLUTION_APP' && isV3(d)) box.push(el('button', { type: 'button', cls: 'link', 'data-act': 'trial-add', 'data-k': k, text: '加试验方案' }));
    return el('div', { cls: 're-target' }, box);
  }
  function changeList(c, d) {
    const items = [
      ...c.routes.map(([k, a, b]) => { const [sid, t, p] = k.split('|'); return el('li', {}, el('code', { text: sid }), ' ' + (d.scenes[sid]?.name || c.before.scenes[sid]?.name || '') + ' · ' + (TYPE_LABEL[t] || t) + ' · ' + p + '：' + label(a, c.before) + ' → ' + label(b, d)); }),
      ...c.solutions.map(([id, a, b]) => el('li', {}, '方案登记 ', el('code', { text: id.slice(0, 8) }), '：' + (!a ? '新登记「' + b.name + '」' + (b.status ? '，状态 ' + b.status : '') : !b ? '删除「' + a.name + '」' : fieldChanges(a, b)))),
      c.scenes ? el('li', { text: '场景目录换成 ' + Object.keys(d.scenes).length + ' 个场景' }) : null,
      c.schema ? el('li', { text: '表格式 ' + (c.before.schema === SCHEMA ? 'v2' : c.before.schema) + ' 升为 v3（方案登记带状态、闸、请求合同、话题、诉求）' }) : null];
    return el('ul', { cls: 're-list' }, items.slice(0, 300));
  }
  const shown = v => v == null || (Array.isArray(v) && !v.length) ? '（无）' : Array.isArray(v) ? v.join(' ') : String(v);
  function fieldChanges(a, b) {
    const out = Object.keys(FIELD_LABEL).filter(k => canon(a[k]) !== canon(b[k])).map(k => FIELD_LABEL[k] + ' ' + shown(a[k]) + ' → ' + shown(b[k]));
    return out.length ? out.join('；') : '其余登记字段有改动';
  }
  function view() {
    const out = [el('h1', { text: '受理路由表' }), el('p', { cls: 're-lead', text: '每个场景选一个去向：方案应用、转安全，或无方案。方案应用先在「方案登记」里登记；知识库路由不在这里，留在侧栏配置。受保护的交付链表先存草稿，发布后后续运行才读新版本；其他表确认保存后生效。每个生效版本都保留历史，可载入旧版并按同样流程保存或发布完成回滚。' })];
    out.push(el('div', { cls: 're-card' }, el('div', { cls: 're-row' },
      el('label', { cls: 're-field' }, el('span', { text: '写手' }), el('input', { id: 're-writer', value: state.writer, autocomplete: 'off', placeholder: '你的名字（写手登记里的）' })),
      el('label', { cls: 're-field' }, el('span', { text: '我的口令' }), el('input', { type: 'password', id: 're-key', value: state.key, autocomplete: 'off' })),
      el('label', { cls: 're-field' }, el('span', { text: '路由表' }), el('input', { id: 're-table', value: state.table })),
      el('button', { type: 'button', cls: 'primary', 'data-act': 'load', disabled: state.busy, text: state.loaded ? '重新读取（丢掉编辑区）' : '读取' }))));
    if (state.msg) out.push(el('div', { cls: 're-status ' + state.msg.cls, role: state.msg.cls === 'bad' ? 'alert' : 'status' }, el('span', { text: state.msg.text }),
      state.conflict ? el('button', { type: 'button', cls: 'primary', 'data-act': 'rebase', disabled: state.busy, text: '载入最新版并保留我的改动' }) : null));
    const d = state.draft; if (!state.loaded || !d) return out;
    const doc = routeDoc(state.doc), v = state.view, diff = changes(d);
    out.push(el('p', { cls: 're-muted', text: doc ? state.table + ' · 现在是第 ' + state.version + ' 版 · 保存于 ' + doc.saved_at + ' · 内容摘要 ' + String(doc.content_sha256).slice(0, 12)
      + ' · ' + (isV3(doc) ? 'v3' : 'v2') + (state.basedOn !== state.version ? ' · 编辑区是第 ' + state.basedOn + ' 版' : '') : state.table + ' · 还没有 v2 / v3 版本' }));

    // 场景路由
    const counts = { SOLUTION_APP: 0, TRANSFER: 0, RESOURCE: 0, NONE: 0 };
    for (const sid of Object.keys(d.scenes)) counts[kindOf(d.routes.get(key3(sid, v.issue_type, v.phase)))]++;
    const domains = [...new Set(Object.values(d.scenes).map(s => s.domain))].sort();
    const rows = sceneRows(d), changedKeys = new Set(diff.routes.map(c => c[0]));
    out.push(el('section', { cls: 're-card' },
      el('h2', { text: '场景路由 · ' + (TYPE_LABEL[v.issue_type] || v.issue_type) + ' · ' + v.phase }),
      el('p', { cls: 're-muted', text: Object.keys(d.scenes).length + ' 个场景：方案应用 ' + counts.SOLUTION_APP + ' · 转安全 ' + counts.TRANSFER + (counts.RESOURCE ? ' · 资源目标 ' + counts.RESOURCE : '') + ' · 无方案 ' + counts.NONE }),
      el('div', { cls: 're-row' },
        el('label', { cls: 're-field' }, el('span', { text: '问题类型' }), el('select', { id: 're-type' }, d.issue_types.map(t => opt(t, (TYPE_LABEL[t] || t) + ' ' + t, t === v.issue_type)))),
        el('label', { cls: 're-field' }, el('span', { text: '处理阶段' }), el('select', { id: 're-phase' }, d.phases.map(p => opt(p, p, p === v.phase)))),
        el('label', { cls: 're-field' }, el('span', { text: '域' }), el('select', { id: 're-domain' }, [opt('', '全部域', !v.domain), ...domains.map(x => opt(x, x, x === v.domain))])),
        el('label', { cls: 're-field re-grow' }, el('span', { text: '搜索' }), el('input', { id: 're-q', type: 'search', value: v.q, placeholder: '场景名、编号或应用 id' }))),
      el('div', { cls: 're-chips', role: 'group', 'aria-label': '按去向筛选' }, STATES.map(([k, t]) => el('button', { type: 'button', 'data-act': 'kind', 'data-v': k, 'aria-pressed': String(v.kind === k), text: t }))),
      el('div', { cls: 're-scroll re-tall' }, el('table', {}, el('thead', {}, el('tr', {}, ['场景编号', '场景名称', '域', '去向'].map(t => el('th', { text: t })))),
        el('tbody', {}, rows.map(([sid, s]) => {
          const k = key3(sid, v.issue_type, v.phase);
          return el('tr', { cls: k === state.errKey ? 're-error' : changedKeys.has(k) ? 're-changed' : null, 'data-row': k },
            el('td', {}, el('code', { text: sid })), el('td', { text: s.name }), el('td', { text: s.domain }), el('td', {}, targetCell(sid, d.routes.get(k), d)));
        })))),
      el('p', { cls: 're-muted', text: '显示 ' + rows.length + ' / ' + Object.keys(d.scenes).length + ' 个场景' })));


    const used = {}; for (const t of d.routes.values()) for (const id of appsOf(t)) used[id] = (used[id] || 0) + 1; // 原去向、试验方案都算用到
    const v3 = isV3(d);
    const head = v3 ? ['应用编号', '名称', '开始变量', '状态', '闸（GATED 必填）', '请求合同', '话题', '诉求', '路由', ''] : ['应用编号', '名称', '开始变量', '用到的路由', ''];
    const info = s => [['调用范围', s.invocation_scope], ['优先级', s.priority], ['方案包', s.package_id], ['交接', s.target_handoff]].filter(([, x]) => x != null).map(([k, x]) => k + ' ' + x).join(' · ') || '其余登记字段没写';
    const text = (id, f, value, labelText, extra) => el('input', { 'data-app': id, 'data-f': f, value: value == null ? '' : Array.isArray(value) ? value.join(' ') : value, 'aria-label': labelText, ...(extra || {}) });
    out.push(el('section', { cls: 're-card' }, el('h2', { text: '方案登记（' + Object.keys(d.solutions).length + ' 个应用 · ' + (v3 ? 'v3' : 'v2') + '）' }),
      el('p', { cls: 're-muted', text: '只有登记过的应用能被路由；「调用方案」工具只调状态 CURRENT 的应用（GATED 拒绝并报闸编码）。开始变量要和应用开始节点一致（统一入口是 call_id request_json）。CURRENT 要写请求合同（话题、诉求可空：路由按场景，不按话题 / 诉求收窄）；GATED 要写挡住它的闸。新方案、状态和闸的改动都随本表按相同流程保存或发布。' }),
      v3 ? null : el('p', { cls: 're-warn' }, '本表是 v2：方案登记只有名称和开始变量，调用时每个方案都当 CURRENT。 ',
        el('button', { type: 'button', 'data-act': 'upgrade', text: '升为 v3（逐个写状态、闸、请求合同、话题、诉求）' })),
      el('div', { cls: 're-scroll' }, el('table', {}, el('thead', {}, el('tr', {}, head.map(t => el('th', { text: t })))),
        el('tbody', {}, Object.entries(d.solutions).map(([id, s]) => el('tr', { cls: id === state.errApp ? 're-error' : null },
          el('td', {}, el('code', { text: id }), ' ', appLink(id), v3 ? el('div', { cls: 're-app', 'data-app-info': id, text: info(s) }) : null),
          el('td', {}, text(id, 'name', s.name, '方案名称')),
          el('td', {}, text(id, 'inputs', s.inputs, '开始变量')),
          v3 ? [
            el('td', {}, el('select', { 'data-app': id, 'data-f': 'status', 'aria-label': '登记状态' }, STATUS.map(([k, t]) => opt(k, k + ' · ' + t, s.status === k)))),
            el('td', {}, text(id, 'gate_code', s.gate_code, '闸编码', { placeholder: s.status === 'GATED' ? 'EXAMPLE_GATE' : null, disabled: !GATE_STATUS.includes(s.status) })),
            el('td', {}, text(id, 'request_contract', s.request_contract, '请求合同', { placeholder: '场景方案调用/v4' })),
            el('td', {}, text(id, 'topics', s.topics, '话题')),
            el('td', {}, text(id, 'intents', s.intents, '诉求', { placeholder: 'REPORT_PROBLEM' })),
          ] : null,
          el('td', { cls: 're-num', text: String(used[id] || 0) }),
          el('td', {}, used[id] ? el('span', { cls: 're-muted', text: '有路由' }) : el('button', { type: 'button', cls: 'link', 'data-act': 'unreg', 'data-v': id, text: '删除登记' }))))))),
      el('div', { cls: 're-row' },
        el('label', { cls: 're-field' }, el('span', { text: '应用编号' }), el('input', { id: 're-new-id', value: state.newApp.app_id, placeholder: '00000000-0000-4000-8000-000000000001', size: '38' })),
        el('label', { cls: 're-field' }, el('span', { text: '名称' }), el('input', { id: 're-new-name', value: state.newApp.name, placeholder: '示例方案' })),
        el('label', { cls: 're-field' }, el('span', { text: '开始变量' }), el('input', { id: 're-new-inputs', value: state.newApp.inputs })),
        el('button', { type: 'button', 'data-act': 'reg', text: '登记' }))));

    // 保存：先列改动，确认后才写
    const total = diff.routes.length + diff.solutions.length + (diff.scenes ? 1 : 0) + (diff.schema ? 1 : 0);
    if (state.twoState) out.push(draftCard());
    out.push(el('section', { cls: 're-card' + (state.confirming ? ' re-confirm' : '') }, el('h2', { text: state.confirming ? (state.twoState ? '确认存为草稿' : '确认保存为第 ' + (state.version + 1) + ' 版') : (state.twoState ? '存草稿' : '保存') }),
      total ? el('details', { open: state.confirming }, el('summary', { text: '和第 ' + state.version + ' 版相比：去向 ' + diff.routes.length + ' 条 · 方案登记 ' + diff.solutions.length + ' 项' + (diff.scenes ? ' · 场景目录' : '') + (diff.schema ? ' · 升为 v3' : '') }), changeList(diff, d))
        : el('p', { cls: 're-muted', text: '和现存版本相比没有改动。' }),
      state.confirming ? el('p', { cls: 're-warn', text: state.twoState ? '存成草稿，不生效：发布之前现网流程照读第 ' + state.version + ' 版。' + (state.serverDraft ? '会替换现在的草稿。' : '') : '确认后立即生效：读这张表的现网流程，下一次运行就按新表走。' }) : null,
      state.confirming ? null : el('label', { cls: 're-field' }, el('span', { text: '改动说明（必填）' }), el('textarea', { id: 're-note', rows: '2', text: d.note })),
      state.confirming ? el('p', {}, '改动说明：' + d.note) : null,
      state.twoState ? null : state.confirming ? (state.gateText.trim() ? el('p', { cls: 're-muted', text: '随这次保存发出存前闸报告（插件核内容摘要并记下）。' }) : null)
        : el('details', { open: !!state.gateText.trim() }, el('summary', { text: '存前闸报告（可选）' }),
          el('textarea', { id: 're-gate', rows: '3', 'aria-label': '存前闸报告 JSON', placeholder: '{"status": "PASS", "content_sha256": "…", …}', text: state.gateText })),
      el('div', { cls: 're-row' }, state.confirming
        ? [el('button', { type: 'button', cls: 'primary', 'data-act': 'confirm', disabled: state.busy, text: state.twoState ? '确认存草稿' : '确认保存' }), el('button', { type: 'button', 'data-act': 'cancel', text: '返回修改' })]
        : el('button', { type: 'button', cls: 'primary', 'data-act': 'save', disabled: state.busy || !total, text: state.busy ? '正在保存…' : state.twoState ? '检查改动并存草稿' : '检查改动并保存' }))));

    out.push(el('details', { cls: 're-card' }, el('summary', { text: '场景目录（' + Object.keys(d.scenes).length + ' 个场景）' }),
      el('p', { cls: 're-muted', text: '场景目录取自现网定位。定位发布新目录后在这里导入：粘贴 {场景编号: {name, domain}}，或 [{scene_id, name, domain}]（定位 CATALOG 的行就可以）。' }),
      el('textarea', { id: 're-scenes', rows: '4', 'aria-label': '场景目录 JSON' }),
      el('div', { cls: 're-row' }, el('button', { type: 'button', 'data-act': 'scenes', text: '替换场景目录' }))));
    out.push(el('details', { cls: 're-card' }, el('summary', { text: '历史版本（' + state.history.length + '）' }),
      el('ul', { cls: 're-list' }, state.history.map(h => el('li', {}, '第 ' + h.version + ' 版 · ' + h.saved_at + ' · ' + h.routes + ' 条' + (h.schema === SCHEMA_V3 ? ' · v3' : '') + ' · ' + (h.note || '（无说明）') + ' ',
        el('button', { type: 'button', cls: 'link', 'data-act': 'version', 'data-v': h.version, text: '载入编辑区' }))))));
    out.push(el('details', { cls: 're-card' }, el('summary', { text: '导入 / 导出 JSON' }),
      el('label', { cls: 're-field' }, el('span', { text: '粘贴整张路由表（' + SCHEMA + ' 或 ' + SCHEMA_V3 + '）' }), el('textarea', { id: 're-import', rows: '5' })),
      el('div', { cls: 're-row' }, el('button', { type: 'button', 'data-act': 'import', text: '导入到编辑区' }),
        el('button', { type: 'button', 'data-act': 'export-draft', text: '导出编辑区（未保存的也在）' }), el('button', { type: 'button', 'data-act': 'export', text: '导出已保存版本' })),
      state.exported ? el('textarea', { rows: '8', readonly: true, 'aria-label': '导出的 JSON', text: state.exported }) : null));
    return out;
  }
  // 0.5.24：草稿与发布
  function draftCard() {
    const dr = state.serverDraft;
    if (!dr) return el('section', { cls: 're-card' }, el('h2', { text: '草稿与发布' }), el('p', { cls: 're-muted', text: '没有未发布的草稿。改完先存草稿，再带存前闸报告和发布批次或变更编号发布；发布前现网照读生效版。' }));
    const stale = dr.base_version !== state.version;
    return el('section', { cls: 're-card re-confirm' }, el('h2', { text: '未发布的草稿' }),
      el('p', { text: '草稿 ' + dr.draft_id.slice(0, 12) + ' · ' + (dr.writer?.id || '?') + ' 存于 ' + dr.saved_at + ' · 基于第 ' + dr.base_version + ' 版 · 内容摘要 ' + dr.content_sha256 + ' · 说明：' + (dr.doc?.note ?? dr.body?.note ?? '') }),
      stale ? el('p', { cls: 're-warn', text: '生效版已是第 ' + state.version + ' 版，这份草稿发不了：在新版上重做草稿。' }) : null,
      el('label', { cls: 're-field' }, el('span', { text: '存前闸报告（必填，核的是上面的内容摘要）' }), el('textarea', { id: 're-gate', rows: '3', 'aria-label': '存前闸报告 JSON', placeholder: '{"content_sha256": "…", "gate_status": "PASS", …}', text: state.gateText })),
      el('label', { cls: 're-field' }, el('span', { text: '发布批次或变更编号（必填）' }), el('input', { id: 're-release', value: state.releaseRef, placeholder: '发布批次或变更编号' })),
      el('div', { cls: 're-row' }, el('button', { type: 'button', cls: 'primary', 'data-act': 'publish', disabled: state.busy || stale, text: '发布为第 ' + (dr.would_be_version ?? dr.base_version + 1) + ' 版' }),
        el('button', { type: 'button', 'data-act': 'discard', disabled: state.busy, text: '删草稿' })));
  }
  function paint() {
    const a = document.activeElement;
    const focus = a && app.contains(a) ? (a.id ? '#' + a.id : a.dataset.k ? '[data-k="' + a.dataset.k + '"][data-f="' + a.dataset.f + '"]' : a.dataset.app ? '[data-app="' + a.dataset.app + '"][data-f="' + a.dataset.f + '"]' : null) : null;
    app.replaceChildren(...view());
    if (focus) app.querySelector(focus)?.focus();
    if (state.errKey) app.querySelector('tr.re-error')?.scrollIntoView?.({ block: 'center' });
  }

  app.addEventListener('click', e => {
    const b = e.target.closest('[data-act]'); if (!b) return;
    const act = b.dataset.act, d = state.draft;
    if (act === 'load') { state.key = app.querySelector('#re-key').value; state.writer = app.querySelector('#re-writer').value.trim(); state.table = app.querySelector('#re-table').value.trim(); keepKey(state.key, state.writer); void load(); }
    else if (act === 'publish') void publish();
    else if (act === 'discard') void discard();
    else if (act === 'save') askSave();
    else if (act === 'confirm') void save();
    else if (act === 'cancel') { state.confirming = false; paint(); }
    else if (act === 'rebase') void rebase();
    else if (act === 'kind') { state.view.kind = b.dataset.v; paint(); }
    else if (act === 'reg' && d) addSolution();
    else if (act === 'upgrade' && d) { upgrade(d); state.confirming = false; state.msg = { cls: 'warn', text: '已升为 v3，还没保存：每个方案先当 CURRENT；CURRENT 要写请求合同，不调的改成 CANDIDATE / GATED（写闸）等。' }; paint(); }
    else if (act === 'trial-add' && d) { const t = d.routes.get(b.dataset.k); if (t?.kind === 'SOLUTION_APP' && t.trial === undefined) { t.trial = { app_id: '', share: 10 }; state.confirming = false; paint(); } }
    else if (act === 'trial-del' && d) { const t = d.routes.get(b.dataset.k); if (t) { delete t.trial; if (state.errKey === b.dataset.k) state.errKey = null; state.confirming = false; paint(); } }
    else if (act === 'unreg' && d) { delete d.solutions[b.dataset.v]; state.confirming = false; paint(); }
    else if (act === 'version') void loadVersion(Number(b.dataset.v));
    else if (act === 'import') importTable(app.querySelector('#re-import').value);
    else if (act === 'scenes') importScenes(app.querySelector('#re-scenes').value);
    else if (act === 'export') { state.exported = state.doc ? JSON.stringify(state.doc, null, 2) : '（还没有保存过的版本）'; paint(); }
    else if (act === 'export-draft' && d) { state.exported = JSON.stringify(toDoc(d), null, 2); paint(); }
  });
  // 输入只改草稿；会改变行数或下拉内容的才重画。任何改动都退出「确认保存」。
  app.addEventListener('input', e => {
    const t = e.target, d = state.draft;
    if (t.id === 're-q') { state.view.q = t.value; paint(); return; }
    if (t.id === 're-note' && d) { d.note = t.value; return; }
    if (t.id === 're-new-id') { state.newApp.app_id = t.value; return; }
    if (t.id === 're-new-name') { state.newApp.name = t.value; return; }
    if (t.id === 're-new-inputs') { state.newApp.inputs = t.value; return; }
    if (t.id === 're-gate') { state.gateText = t.value; return; }
    if (t.id === 're-release') { state.releaseRef = t.value; return; }
    if (d && t.dataset.app && d.solutions[t.dataset.app]) {
      const s = d.solutions[t.dataset.app], f = t.dataset.f;
      if (f === 'name') s.name = t.value;
      else if (f === 'inputs') s.inputs = words(t.value);
      else if (f === 'gate_code' || f === 'request_contract') s[f] = t.value.trim() || null;
      else if (f === 'topics' || f === 'intents') s[f] = words(t.value);
      state.confirming = false;
    }
  });
  app.addEventListener('change', e => {
    const t = e.target, d = state.draft, v = state.view;
    if (t.id === 're-type') { v.issue_type = t.value; paint(); return; }
    if (t.id === 're-phase') { v.phase = t.value; paint(); return; }
    if (t.id === 're-domain') { v.domain = t.value; paint(); return; }
    if (d && t.dataset.app && t.dataset.f === 'status' && d.solutions[t.dataset.app]) {
      const s = d.solutions[t.dataset.app];
      s.status = t.value;
      if (!GATE_STATUS.includes(s.status)) s.gate_code = null; // 闸只写在 GATED（必填）/ SHADOW（可选）上，插件同规则
      if (state.errApp === t.dataset.app) state.errApp = null;
      state.confirming = false; paint(); return;
    }
    // 一项登记的文字改完（失焦 / 回车）：重画，改动计数和「检查改动并保存」跟上
    if (d && t.dataset.app && d.solutions[t.dataset.app]) { paint(); return; }
    if (d && t.dataset.k && (t.dataset.f === 'trial-app' || t.dataset.f === 'trial-share')) {
      const row = d.routes.get(t.dataset.k);
      if (row && row.trial !== undefined) {
        if (!row.trial || typeof row.trial !== 'object' || Array.isArray(row.trial)) row.trial = { app_id: '', share: 10 };
        if (t.dataset.f === 'trial-app') row.trial.app_id = t.value; else row.trial.share = /^\d+$/.test(t.value.trim()) ? Number(t.value.trim()) : NaN;
      }
      if (state.errKey === t.dataset.k) state.errKey = null;
      state.confirming = false; paint(); return;
    }
    if (!d || t.dataset.f !== 'target' || !t.dataset.k) return;
    const k = t.dataset.k, val = t.value, was = d.routes.get(k);
    if (val === 'NONE') d.routes.delete(k);
    else if (val === 'TRANSFER') d.routes.set(k, { kind: 'TRANSFER', to: 'SAFETY' });
    // 换原去向时试验留着（换成试验方案自己时去掉：两边不能是同一个应用）；改成转安全、无方案时试验跟着去掉
    else if (val.startsWith('APP:')) d.routes.set(k, withTrial({ kind: 'SOLUTION_APP', app_id: val.slice(4) }, was?.kind === 'SOLUTION_APP' && was.trial?.app_id !== val.slice(4) ? was.trial : undefined));
    else if (val.startsWith('RESOURCE:')) d.routes.set(k, { kind: 'RESOURCE', resource_id: val.slice('RESOURCE:'.length) });
    if (state.errKey === k) state.errKey = null;
    state.confirming = false;
    paint();
  });
  paint();
  if (state.key && state.writer) void load();
})();
