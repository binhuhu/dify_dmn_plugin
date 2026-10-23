(() => {
  'use strict';
  // 路由表编辑页（v2，Brian 10-23「agenthub的插件应该要有对于场景和方案的配置能力」）：
  // 按场景列出现网定位的全部场景，每行选一个去向（方案应用 / 转安全 / 无方案）；方案应用先在「方案登记」里登记。
  // 知识库路由不进这张表，留在侧栏配置（Brian 10-23 D8）。
  // 保存前先列改动、确认后才写（评审 T2-05）；冲突时保留草稿，可载入最新版并把自己的改动重放上去。
  // 只经本端点的 /api/* 读写插件存储（请求头带编辑口令），不执行规则、不连别的地址。DOM API + textContent 出字，不拼 HTML；
  // 口令只放本标签页的 sessionStorage。
  const KEY_STORE = 'route-edit-key';
  const SCHEMA = 'dmn-json.route-table/v2';
  const STATES = [['ALL', '全部'], ['SOLUTION_APP', '方案应用'], ['TRANSFER', '转安全'], ['NONE', '无方案']];
  const TYPE_LABEL = { COMPLAINT: '投诉', CONSULT: '咨询', FEEDBACK: '反馈' };
  const CONFLICT = ['ROUTE_TABLE_STALE', 'ROUTE_TABLE_RACE'];
  const app = document.getElementById('app');
  const base = (() => { const p = location.pathname; return p.endsWith('/') ? p : p.endsWith('/index.html') ? p.slice(0, -'index.html'.length) : p + '/'; })();
  const state = { key: readKey(), table: 'dispatch.route', loaded: false, doc: null, baseDoc: null, version: 0, draft: null, basedOn: 0, history: [],
    busy: false, msg: null, confirming: false, conflict: false, errKey: null, errApp: null,
    view: { issue_type: 'COMPLAINT', phase: 'STAGE_1', domain: '', kind: 'ALL', q: '' }, exported: '', newApp: { app_id: '', name: '', inputs: 'call_id request_json' } };

  function readKey() { try { return sessionStorage.getItem(KEY_STORE) || ''; } catch (_) { return ''; } }
  function keepKey(k) { try { sessionStorage.setItem(KEY_STORE, k); } catch (_) { /* 私密窗口等：只在本页内存里 */ } }
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

  async function api(path, body) {
    const r = await fetch(base + 'api/' + path, { method: body ? 'POST' : 'GET', cache: 'no-store', credentials: 'omit', redirect: 'error',
      headers: { 'X-Route-Edit-Key': state.key, ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
    let v; try { v = await r.json(); } catch (_) { throw Error('服务没返回 JSON（' + r.status + '）'); }
    if (!r.ok || v.error) { const e = Error(v.error?.message || '请求失败（' + r.status + '）'); e.code = v.error?.code; e.path = v.error?.path || ''; throw e; }
    return v;
  }
  const q = name => name + '?table=' + encodeURIComponent(state.table);

  // 存的文档 ↔ 页面草稿：路由按「场景|类型|阶段」放进 Map。
  const v2 = doc => doc && doc.schema === SCHEMA ? doc : null;
  function toDraft(doc) {
    const routes = new Map();
    for (const r of doc?.routes || []) routes.set(key3(r.scene_id, r.issue_type, r.phase), structuredClone(r.target));
    return { issue_types: doc?.issue_types || ['COMPLAINT', 'CONSULT', 'FEEDBACK'], phases: doc?.phases || ['STAGE_1', 'STAGE_2', 'STAGE_3'],
      scenes: structuredClone(doc?.scenes || {}), solutions: structuredClone(doc?.solutions || {}), routes, note: '' };
  }
  function toDoc(d) {
    const routes = [];
    for (const [k, target] of d.routes) { const [scene_id, issue_type, phase] = k.split('|'); routes.push({ scene_id, issue_type, phase, target }); }
    return { schema: SCHEMA, table: state.table, issue_types: d.issue_types, phases: d.phases, scenes: d.scenes, solutions: d.solutions, routes, note: d.note.trim() };
  }
  const kindOf = t => t ? t.kind : 'NONE';
  const label = (t, d) => !t ? '无方案' : t.kind === 'TRANSFER' ? '转安全' : (d.solutions[t.app_id]?.name || '未登记的应用') + ' · ' + t.app_id.slice(0, 8);

  // 草稿和某一版比：去向、方案登记、场景目录各变了什么。
  function changesAgainst(doc, d) {
    const before = toDraft(v2(doc)), routes = [], solutions = [];
    for (const k of new Set([...before.routes.keys(), ...d.routes.keys()])) {
      const a = before.routes.get(k), b = d.routes.get(k);
      if (canon(a) !== canon(b)) routes.push([k, a, b]);
    }
    for (const id of new Set([...Object.keys(before.solutions), ...Object.keys(d.solutions)])) {
      const a = before.solutions[id], b = d.solutions[id];
      if (canon(a) !== canon(b)) solutions.push([id, a, b]);
    }
    return { routes, solutions, scenes: canon(before.scenes) !== canon(d.scenes), before };
  }
  const changes = d => changesAgainst(state.doc, d);
  // 保存前在页面上先挡住服务端一定会拒的：路由指向目录外的场景、未登记的应用。
  function problems(d) {
    const out = [];
    for (const [k, t] of d.routes) {
      const sid = k.split('|')[0];
      if (!d.scenes[sid]) out.push([k, '场景 ' + sid + ' 不在场景目录里']);
      else if (t.kind === 'SOLUTION_APP' && !d.solutions[t.app_id]) out.push([k, sid + ' 指向未登记的应用 ' + t.app_id.slice(0, 8)]);
    }
    return out;
  }

  async function load() {
    state.busy = true; state.msg = null; state.confirming = false; state.conflict = false; state.errKey = state.errApp = null; paint();
    try {
      const [t, h] = await Promise.all([api(q('table')), api(q('history'))]);
      state.doc = t.doc; state.baseDoc = v2(t.doc); state.version = t.version; state.history = h.versions; state.basedOn = t.version; state.loaded = true;
      state.draft = toDraft(v2(t.doc));
      if (t.doc && !v2(t.doc)) state.msg = { cls: 'warn', text: '插件里的 ' + state.table + ' 是旧格式（' + t.doc.schema + '），本页只编辑 v2：用「导入 JSON」放进 v2 第一版后保存。' };
      else state.msg = t.doc ? null : { cls: 'warn', text: '插件里还没有路由表 ' + state.table + '：用「导入 JSON」放进第一版（场景目录 + 方案登记 + 路由）。' };
    } catch (e) { state.msg = { cls: 'bad', text: e.message }; if (e.code === 'EDIT_KEY_REJECTED') state.loaded = false; }
    state.busy = false; paint();
  }
  function askSave() {
    const d = state.draft; if (!d) return;
    state.errKey = state.errApp = null;
    if (!d.note.trim()) { state.msg = { cls: 'bad', text: '写一句改动说明再保存。' }; paint(); return; }
    const bad = problems(d);
    if (bad.length) { state.errKey = bad[0][0]; focusRow(bad[0][0]); state.msg = { cls: 'bad', text: '先改掉这些再保存：' + bad.slice(0, 5).map(x => x[1]).join('；') + (bad.length > 5 ? '…（共 ' + bad.length + ' 处）' : '') }; paint(); return; }
    state.confirming = true; state.msg = null; paint();
  }
  async function save() {
    const d = state.draft; if (!d) return;
    const sent = toDoc(d), n = changes(d).routes.length;
    state.busy = true; state.confirming = false; state.msg = null; paint();
    try {
      const r = await api('table', { ...sent, expected_version: state.version });
      state.doc = r.doc; state.baseDoc = r.doc; state.version = r.version; state.draft = toDraft(r.doc); state.basedOn = r.version; state.conflict = false;
      state.history = (await api(q('history'))).versions;
      state.msg = { cls: 'ok', text: '已保存第 ' + r.version + ' 版（改了 ' + n + ' 条去向，共 ' + r.doc.routes.length + ' 条路由）。现网流程下一次运行就读这一版。' };
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
  // 冲突后：取最新版，把「我相对起点的改动」重放上去；双方都改了的同一处以我的为准，并列出来。
  async function rebase() {
    const d = state.draft; if (!d) return;
    state.busy = true; paint();
    try {
      const t = await api(q('table')), head = v2(t.doc);
      const mine = changesAgainst(state.baseDoc, d), theirs = changesAgainst(state.baseDoc, toDraft(head));
      const next = toDraft(head), clash = [];
      const theirRoute = new Map(theirs.routes.map(([k, , b]) => [k, b]));
      for (const [k, , b] of mine.routes) {
        if (theirRoute.has(k) && canon(theirRoute.get(k)) !== canon(b)) clash.push(k.split('|')[0]);
        if (b) next.routes.set(k, b); else next.routes.delete(k);
      }
      for (const [id, , b] of mine.solutions) { if (b) next.solutions[id] = b; else delete next.solutions[id]; }
      if (mine.scenes) next.scenes = d.scenes;
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
      if (!v2(r.doc)) throw Error('第 ' + v + ' 版是旧格式，本页不能载入');
      state.draft = toDraft(r.doc); state.basedOn = v; state.baseDoc = v2(state.doc); state.confirming = false;
      state.msg = { cls: 'warn', text: '已载入第 ' + v + ' 版到编辑区；写说明后保存，就成为第 ' + (state.version + 1) + ' 版（回滚）。' };
    } catch (e) { state.msg = { cls: 'bad', text: e.message }; }
    paint();
  }
  function focusRow(k) {
    const [sid, t, p] = k.split('|');
    Object.assign(state.view, { issue_type: t, phase: p, domain: '', kind: 'ALL', q: sid });
    state.errKey = k;
  }
  // 导入整张 v2 表（例如 80 生成的第一版），或只导入场景目录（{场景编号: {name, domain}} 或 [{scene_id, name, domain}]，例如现网定位 CATALOG）。
  function parse(text) { try { return JSON.parse(text); } catch (_) { state.msg = { cls: 'bad', text: '导入的内容不是 JSON。' }; paint(); return null; } }
  function importTable(text) {
    const v = parse(text); if (!v) return;
    if (v.schema !== SCHEMA || !v.scenes || !v.solutions || !Array.isArray(v.routes)) { state.msg = { cls: 'bad', text: '要导入的是 ' + SCHEMA + '：要有 scenes、solutions、routes。' }; paint(); return; }
    const note = state.draft?.note || v.note || '';
    state.draft = { ...toDraft(v), note }; state.confirming = false;
    state.msg = { cls: 'warn', text: '已导入 ' + Object.keys(v.scenes).length + ' 个场景、' + Object.keys(v.solutions).length + ' 个方案、' + v.routes.length + ' 条路由到编辑区，还没保存。' }; paint();
  }
  function importScenes(text) {
    const v = parse(text); if (!v || !state.draft) return;
    const rows = Array.isArray(v) ? v : v.scenes && !Array.isArray(v.scenes) ? Object.entries(v.scenes).map(([scene_id, s]) => ({ scene_id, ...s }))
      : Array.isArray(v.scenes) ? v.scenes : Object.entries(v).map(([scene_id, s]) => ({ scene_id, ...s }));
    const scenes = {};
    for (const r of rows) if (r && /^P_\d{2}_\d{2}_\d{2}$/.test(r.scene_id || '') && r.name && r.domain) scenes[r.scene_id] = { name: String(r.name), domain: String(r.domain) };
    if (!Object.keys(scenes).length) { state.msg = { cls: 'bad', text: '没认出场景：每项要有 scene_id、name、domain。' }; paint(); return; }
    state.draft.scenes = scenes; state.confirming = false;
    const orphan = problems(state.draft);
    state.msg = { cls: orphan.length ? 'bad' : 'warn', text: '场景目录换成 ' + Object.keys(scenes).length + ' 个场景，还没保存。' + (orphan.length ? ' 有 ' + orphan.length + ' 条路由指向目录里没有的场景，保存前先改掉：' + orphan.slice(0, 6).map(x => x[0].split('|')[0]).join('，') : '') };
    paint();
  }
  function addSolution() {
    const d = state.draft, a = state.newApp, id = a.app_id.trim().toLowerCase();
    if (!/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(id)) { state.msg = { cls: 'bad', text: '应用编号要是 AgentHub 应用 id（36 位，形如 ee1aebd3-41ff-…）。' }; paint(); return; }
    if (!a.name.trim() || !words(a.inputs).length) { state.msg = { cls: 'bad', text: '登记方案要写名称和开始变量。' }; paint(); return; }
    d.solutions[id] = { name: a.name.trim(), inputs: words(a.inputs) };
    state.newApp = { app_id: '', name: '', inputs: 'call_id request_json' }; state.confirming = false;
    state.msg = { cls: 'warn', text: '已登记 ' + id.slice(0, 8) + '，还没保存；在场景路由里给场景选上它。' }; paint();
  }

  function sceneRows(d) {
    const v = state.view, f = v.q.trim().toLowerCase();
    return Object.entries(d.scenes).filter(([sid, s]) => {
      const t = d.routes.get(key3(sid, v.issue_type, v.phase));
      if (v.domain && s.domain !== v.domain) return false;
      if (v.kind !== 'ALL' && kindOf(t) !== v.kind) return false;
      return !f || (sid + ' ' + s.name + ' ' + (t?.app_id || '') + ' ' + (t ? label(t, d) : '')).toLowerCase().includes(f);
    });
  }
  function targetCell(sid, t, d) {
    const k = key3(sid, state.view.issue_type, state.view.phase);
    const value = !t ? 'NONE' : t.kind === 'SOLUTION_APP' ? 'APP:' + t.app_id : t.kind;
    const opts = [opt('NONE', '无方案', value === 'NONE'), opt('TRANSFER', '转安全', value === 'TRANSFER'),
      ...Object.entries(d.solutions).map(([id, s]) => opt('APP:' + id, s.name + ' · ' + id.slice(0, 8), value === 'APP:' + id))];
    if (t?.kind === 'SOLUTION_APP' && !d.solutions[t.app_id]) opts.push(opt(value, '未登记的应用 · ' + t.app_id.slice(0, 8), true));
    const box = [el('select', { 'data-k': k, 'data-f': 'target', 'aria-label': sid + ' 的去向' }, opts)];
    if (t?.kind === 'SOLUTION_APP') box.push(el('span', { cls: 're-app' }, el('code', { text: t.app_id }), ' ', appLink(t.app_id)));
    return el('div', { cls: 're-target' }, box);
  }
  function changeList(c, d) {
    const items = [
      ...c.routes.map(([k, a, b]) => { const [sid, t, p] = k.split('|'); return el('li', {}, el('code', { text: sid }), ' ' + (d.scenes[sid]?.name || c.before.scenes[sid]?.name || '') + ' · ' + (TYPE_LABEL[t] || t) + ' · ' + p + '：' + label(a, c.before) + ' → ' + label(b, d)); }),
      ...c.solutions.map(([id, a, b]) => el('li', {}, '方案登记 ', el('code', { text: id.slice(0, 8) }), '：' + (!a ? '新登记「' + b.name + '」' : !b ? '删除「' + a.name + '」' : '改为「' + b.name + '」，开始变量 ' + b.inputs.join(' ')))),
      c.scenes ? el('li', { text: '场景目录换成 ' + Object.keys(d.scenes).length + ' 个场景' }) : null];
    return el('ul', { cls: 're-list' }, items.slice(0, 300));
  }
  function view() {
    const out = [el('h1', { text: '受理路由表' }), el('p', { cls: 're-lead', text: '每个场景选一个去向：方案应用、转安全，或无方案。方案应用先在「方案登记」里登记；知识库路由不在这里，留在侧栏配置。保存后现网流程下一次运行就读新表，不用重发受理路由或交付流程；每次保存都留一版，可以载入旧版再存回去。' })];
    out.push(el('div', { cls: 're-card' }, el('div', { cls: 're-row' },
      el('label', { cls: 're-field' }, el('span', { text: '编辑口令' }), el('input', { type: 'password', id: 're-key', value: state.key, autocomplete: 'off' })),
      el('label', { cls: 're-field' }, el('span', { text: '路由表' }), el('input', { id: 're-table', value: state.table })),
      el('button', { type: 'button', cls: 'primary', 'data-act': 'load', disabled: state.busy, text: state.loaded ? '重新读取（丢掉编辑区）' : '读取' }))));
    if (state.msg) out.push(el('div', { cls: 're-status ' + state.msg.cls, role: state.msg.cls === 'bad' ? 'alert' : 'status' }, el('span', { text: state.msg.text }),
      state.conflict ? el('button', { type: 'button', cls: 'primary', 'data-act': 'rebase', disabled: state.busy, text: '载入最新版并保留我的改动' }) : null));
    const d = state.draft; if (!state.loaded || !d) return out;
    const doc = v2(state.doc), v = state.view, diff = changes(d);
    out.push(el('p', { cls: 're-muted', text: doc ? state.table + ' · 现在是第 ' + state.version + ' 版 · 保存于 ' + doc.saved_at + ' · 内容摘要 ' + String(doc.content_sha256).slice(0, 12)
      + (state.basedOn !== state.version ? ' · 编辑区是第 ' + state.basedOn + ' 版' : '') : state.table + ' · 还没有 v2 版本' }));

    // 场景路由
    const counts = { SOLUTION_APP: 0, TRANSFER: 0, NONE: 0 };
    for (const sid of Object.keys(d.scenes)) counts[kindOf(d.routes.get(key3(sid, v.issue_type, v.phase)))]++;
    const domains = [...new Set(Object.values(d.scenes).map(s => s.domain))].sort();
    const rows = sceneRows(d), changedKeys = new Set(diff.routes.map(c => c[0]));
    out.push(el('section', { cls: 're-card' },
      el('h2', { text: '场景路由 · ' + (TYPE_LABEL[v.issue_type] || v.issue_type) + ' · ' + v.phase }),
      el('p', { cls: 're-muted', text: Object.keys(d.scenes).length + ' 个场景：方案应用 ' + counts.SOLUTION_APP + ' · 转安全 ' + counts.TRANSFER + ' · 无方案 ' + counts.NONE }),
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

    // 方案登记
    const used = {}; for (const t of d.routes.values()) if (t.kind === 'SOLUTION_APP') used[t.app_id] = (used[t.app_id] || 0) + 1;
    out.push(el('section', { cls: 're-card' }, el('h2', { text: '方案登记（' + Object.keys(d.solutions).length + ' 个应用）' }),
      el('p', { cls: 're-muted', text: '只有登记过的应用能被路由、能被「调用方案」工具调用；开始变量要和应用开始节点一致（统一入口是 call_id request_json）。一个场景只挂一个在用应用，副本和草稿不登记。' }),
      el('div', { cls: 're-scroll' }, el('table', {}, el('thead', {}, el('tr', {}, ['应用编号', '名称', '开始变量', '用到的路由', ''].map(t => el('th', { text: t })))),
        el('tbody', {}, Object.entries(d.solutions).map(([id, s]) => el('tr', { cls: id === state.errApp ? 're-error' : null },
          el('td', {}, el('code', { text: id }), ' ', appLink(id)),
          el('td', {}, el('input', { 'data-app': id, 'data-f': 'name', value: s.name, 'aria-label': '方案名称' })),
          el('td', {}, el('input', { 'data-app': id, 'data-f': 'inputs', value: s.inputs.join(' '), 'aria-label': '开始变量' })),
          el('td', { cls: 're-num', text: String(used[id] || 0) }),
          el('td', {}, used[id] ? el('span', { cls: 're-muted', text: '在用' }) : el('button', { type: 'button', cls: 'link', 'data-act': 'unreg', 'data-v': id, text: '删除登记' }))))))),
      el('div', { cls: 're-row' },
        el('label', { cls: 're-field' }, el('span', { text: '应用编号' }), el('input', { id: 're-new-id', value: state.newApp.app_id, placeholder: 'ee1aebd3-41ff-…', size: '38' })),
        el('label', { cls: 're-field' }, el('span', { text: '名称' }), el('input', { id: 're-new-name', value: state.newApp.name, placeholder: '优惠券使用 · ESE B' })),
        el('label', { cls: 're-field' }, el('span', { text: '开始变量' }), el('input', { id: 're-new-inputs', value: state.newApp.inputs })),
        el('button', { type: 'button', 'data-act': 'reg', text: '登记' }))));

    // 保存：先列改动，确认后才写
    const total = diff.routes.length + diff.solutions.length + (diff.scenes ? 1 : 0);
    out.push(el('section', { cls: 're-card' + (state.confirming ? ' re-confirm' : '') }, el('h2', { text: state.confirming ? '确认保存为第 ' + (state.version + 1) + ' 版' : '保存' }),
      total ? el('details', { open: state.confirming }, el('summary', { text: '和第 ' + state.version + ' 版相比：去向 ' + diff.routes.length + ' 条 · 方案登记 ' + diff.solutions.length + ' 项' + (diff.scenes ? ' · 场景目录' : '') }), changeList(diff, d))
        : el('p', { cls: 're-muted', text: '和现存版本相比没有改动。' }),
      state.confirming ? el('p', { cls: 're-warn', text: '确认后立即生效：读这张表的现网流程，下一次运行就按新表走。' }) : null,
      state.confirming ? null : el('label', { cls: 're-field' }, el('span', { text: '改动说明（必填）' }), el('textarea', { id: 're-note', rows: '2', text: d.note })),
      state.confirming ? el('p', {}, '改动说明：' + d.note) : null,
      el('div', { cls: 're-row' }, state.confirming
        ? [el('button', { type: 'button', cls: 'primary', 'data-act': 'confirm', disabled: state.busy, text: '确认保存' }), el('button', { type: 'button', 'data-act': 'cancel', text: '返回修改' })]
        : el('button', { type: 'button', cls: 'primary', 'data-act': 'save', disabled: state.busy || !total, text: state.busy ? '正在保存…' : '检查改动并保存' }))));

    out.push(el('details', { cls: 're-card' }, el('summary', { text: '场景目录（' + Object.keys(d.scenes).length + ' 个场景）' }),
      el('p', { cls: 're-muted', text: '场景目录取自现网定位。定位发布新目录后在这里导入：粘贴 {场景编号: {name, domain}}，或 [{scene_id, name, domain}]（定位 CATALOG 的行就可以）。' }),
      el('textarea', { id: 're-scenes', rows: '4', 'aria-label': '场景目录 JSON' }),
      el('div', { cls: 're-row' }, el('button', { type: 'button', 'data-act': 'scenes', text: '替换场景目录' }))));
    out.push(el('details', { cls: 're-card' }, el('summary', { text: '历史版本（' + state.history.length + '）' }),
      el('ul', { cls: 're-list' }, state.history.map(h => el('li', {}, '第 ' + h.version + ' 版 · ' + h.saved_at + ' · ' + h.routes + ' 条 · ' + (h.note || '（无说明）') + ' ',
        el('button', { type: 'button', cls: 'link', 'data-act': 'version', 'data-v': h.version, text: '载入编辑区' }))))));
    out.push(el('details', { cls: 're-card' }, el('summary', { text: '导入 / 导出 JSON' }),
      el('label', { cls: 're-field' }, el('span', { text: '粘贴整张路由表（' + SCHEMA + '）' }), el('textarea', { id: 're-import', rows: '5' })),
      el('div', { cls: 're-row' }, el('button', { type: 'button', 'data-act': 'import', text: '导入到编辑区' }),
        el('button', { type: 'button', 'data-act': 'export-draft', text: '导出编辑区（未保存的也在）' }), el('button', { type: 'button', 'data-act': 'export', text: '导出已保存版本' })),
      state.exported ? el('textarea', { rows: '8', readonly: true, 'aria-label': '导出的 JSON', text: state.exported }) : null));
    return out;
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
    if (act === 'load') { state.key = app.querySelector('#re-key').value; state.table = app.querySelector('#re-table').value.trim(); keepKey(state.key); void load(); }
    else if (act === 'save') askSave();
    else if (act === 'confirm') void save();
    else if (act === 'cancel') { state.confirming = false; paint(); }
    else if (act === 'rebase') void rebase();
    else if (act === 'kind') { state.view.kind = b.dataset.v; paint(); }
    else if (act === 'reg' && d) addSolution();
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
    if (d && t.dataset.app && d.solutions[t.dataset.app]) { const s = d.solutions[t.dataset.app]; if (t.dataset.f === 'name') s.name = t.value; else s.inputs = words(t.value); state.confirming = false; }
  });
  app.addEventListener('change', e => {
    const t = e.target, d = state.draft, v = state.view;
    if (t.id === 're-type') { v.issue_type = t.value; paint(); return; }
    if (t.id === 're-phase') { v.phase = t.value; paint(); return; }
    if (t.id === 're-domain') { v.domain = t.value; paint(); return; }
    if (!d || t.dataset.f !== 'target' || !t.dataset.k) return;
    const k = t.dataset.k, val = t.value;
    if (val === 'NONE') d.routes.delete(k);
    else if (val === 'TRANSFER') d.routes.set(k, { kind: 'TRANSFER', to: 'SAFETY' });
    else if (val.startsWith('APP:')) d.routes.set(k, { kind: 'SOLUTION_APP', app_id: val.slice(4) });
    if (state.errKey === k) state.errKey = null;
    state.confirming = false;
    paint();
  });
  paint();
  if (state.key) void load();
})();
