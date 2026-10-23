(() => {
  'use strict';
  // 路由表编辑页（v2，Brian 10-23「agenthub的插件应该要有对于场景和方案的配置能力」）：
  // 按场景列出现网定位的全部场景，每行选一个去向（方案应用 / 转安全 / 知识库 / 无方案）；方案应用先在「方案登记」里登记。
  // 只经本端点的 /api/* 读写插件存储（请求头带编辑口令），不执行规则、不连别的地址。DOM API + textContent 出字，不拼 HTML；
  // 口令只放本标签页的 sessionStorage。
  const KEY_STORE = 'route-edit-key';
  const SCHEMA = 'dmn-json.route-table/v2';
  const STATES = [['ALL', '全部'], ['SOLUTION_APP', '方案应用'], ['TRANSFER', '转安全'], ['KNOWLEDGE', '知识库'], ['NONE', '无方案']];
  const TYPE_LABEL = { COMPLAINT: '投诉', CONSULT: '咨询', FEEDBACK: '反馈' };
  const app = document.getElementById('app');
  const base = (() => { const p = location.pathname; return p.endsWith('/') ? p : p.endsWith('/index.html') ? p.slice(0, -'index.html'.length) : p + '/'; })();
  const state = { key: readKey(), table: 'dispatch.route', loaded: false, doc: null, version: 0, draft: null, basedOn: 0, history: [], busy: false, msg: null,
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

  async function api(path, body) {
    const r = await fetch(base + 'api/' + path, { method: body ? 'POST' : 'GET', cache: 'no-store', credentials: 'omit', redirect: 'error',
      headers: { 'X-Route-Edit-Key': state.key, ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
    let v; try { v = await r.json(); } catch (_) { throw Error('服务没返回 JSON（' + r.status + '）'); }
    if (!r.ok || v.error) { const e = Error((v.error?.message || '请求失败（' + r.status + '）') + (v.error?.path ? '（' + v.error.path + '）' : '')); e.code = v.error?.code; throw e; }
    return v;
  }
  const q = name => name + '?table=' + encodeURIComponent(state.table);

  // 存的文档 ↔ 页面草稿：路由按「场景|类型|阶段」放进 Map；知识条目一行一条 space_id/article_id，话题、诉求空格分隔。
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
  const label = (t, d) => !t ? '无方案' : t.kind === 'TRANSFER' ? '转安全' : t.kind === 'KNOWLEDGE' ? '知识库（' + t.kb_refs.length + ' 条）'
    : (d.solutions[t.app_id]?.name || '未登记的应用') + ' · ' + t.app_id.slice(0, 8);
  // 和现存版本比：哪些 场景×类型×阶段 的去向变了（编辑区载入旧版时，也是和现存版本比）。按键排序后比，存储和接口回来的键序不同。
  const canon = v => Array.isArray(v) ? '[' + v.map(canon).join(',') + ']' : v && typeof v === 'object'
    ? '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + canon(v[k])).join(',') + '}' : JSON.stringify(v ?? null);
  function changes(d) {
    const before = toDraft(state.doc?.schema === SCHEMA ? state.doc : null).routes, out = [];
    for (const k of new Set([...before.keys(), ...d.routes.keys()])) {
      const a = before.get(k), b = d.routes.get(k);
      if (canon(a) !== canon(b)) out.push([k, a, b]);
    }
    return out;
  }

  async function load() {
    state.busy = true; state.msg = null; paint();
    try {
      const [t, h] = await Promise.all([api(q('table')), api(q('history'))]);
      state.doc = t.doc; state.version = t.version; state.history = h.versions; state.basedOn = t.version; state.loaded = true;
      if (t.doc && t.doc.schema !== SCHEMA) {
        state.draft = toDraft(null);
        state.msg = { cls: 'warn', text: '插件里的 ' + state.table + ' 是旧格式（' + t.doc.schema + '），本页只编辑 v2：用「导入 JSON」放进 v2 第一版后保存。' };
      } else {
        state.draft = toDraft(t.doc);
        state.msg = t.doc ? null : { cls: 'warn', text: '插件里还没有路由表 ' + state.table + '：用「导入 JSON」放进第一版（场景目录 + 方案登记 + 路由）。' };
      }
    } catch (e) { state.msg = { cls: 'bad', text: e.message }; if (e.code === 'EDIT_KEY_REJECTED') state.loaded = false; }
    state.busy = false; paint();
  }
  async function save() {
    const d = state.draft; if (!d) return;
    if (!d.note.trim()) { state.msg = { cls: 'bad', text: '写一句改动说明再保存。' }; paint(); return; }
    state.busy = true; state.msg = null; paint();
    try {
      const n = changes(d).length;
      const r = await api('table', { ...toDoc(d), expected_version: state.version });
      state.doc = r.doc; state.version = r.version; state.draft = toDraft(r.doc); state.basedOn = r.version;
      state.history = (await api(q('history'))).versions;
      state.msg = { cls: 'ok', text: '已保存第 ' + r.version + ' 版（改了 ' + n + ' 条去向，共 ' + r.doc.routes.length + ' 条路由）。现网流程下一次运行就读这一版。' };
    } catch (e) { state.msg = { cls: 'bad', text: e.message }; }
    state.busy = false; paint();
  }
  async function loadVersion(v) {
    try {
      const r = await api(q('version') + '&v=' + v);
      if (r.doc.schema !== SCHEMA) throw Error('第 ' + v + ' 版是旧格式，本页不能载入');
      state.draft = toDraft(r.doc); state.basedOn = v;
      state.msg = { cls: 'warn', text: '已载入第 ' + v + ' 版到编辑区；写说明后保存，就成为第 ' + (state.version + 1) + ' 版（回滚）。' };
    } catch (e) { state.msg = { cls: 'bad', text: e.message }; }
    paint();
  }
  // 导入整张 v2 表（例如 80 生成的第一版），或只导入场景目录（{场景编号: {name, domain}} 或 [{scene_id, name, domain}]，例如现网定位 CATALOG）。
  function parse(text) { try { return JSON.parse(text); } catch (_) { state.msg = { cls: 'bad', text: '导入的内容不是 JSON。' }; paint(); return null; } }
  function importTable(text) {
    const v = parse(text); if (!v) return;
    if (v.schema !== SCHEMA || !v.scenes || !v.solutions || !Array.isArray(v.routes)) { state.msg = { cls: 'bad', text: '要导入的是 ' + SCHEMA + '：要有 scenes、solutions、routes。' }; paint(); return; }
    const note = state.draft?.note || v.note || '';
    state.draft = { ...toDraft(v), note };
    state.msg = { cls: 'warn', text: '已导入 ' + Object.keys(v.scenes).length + ' 个场景、' + Object.keys(v.solutions).length + ' 个方案、' + v.routes.length + ' 条路由到编辑区，还没保存。' }; paint();
  }
  function importScenes(text) {
    const v = parse(text); if (!v || !state.draft) return;
    const rows = Array.isArray(v) ? v : v.scenes && !Array.isArray(v.scenes) ? Object.entries(v.scenes).map(([scene_id, s]) => ({ scene_id, ...s }))
      : Array.isArray(v.scenes) ? v.scenes : Object.entries(v).map(([scene_id, s]) => ({ scene_id, ...s }));
    const scenes = {};
    for (const r of rows) if (r && /^P_\d{2}_\d{2}_\d{2}$/.test(r.scene_id || '') && r.name && r.domain) scenes[r.scene_id] = { name: String(r.name), domain: String(r.domain) };
    if (!Object.keys(scenes).length) { state.msg = { cls: 'bad', text: '没认出场景：每项要有 scene_id、name、domain。' }; paint(); return; }
    const orphan = [...state.draft.routes.keys()].filter(k => !scenes[k.split('|')[0]]);
    state.draft.scenes = scenes;
    state.msg = { cls: orphan.length ? 'bad' : 'warn', text: '场景目录换成 ' + Object.keys(scenes).length + ' 个场景，还没保存。' + (orphan.length ? ' 有 ' + orphan.length + ' 条路由指向目录里没有的场景，保存前先删掉或改回：' + orphan.slice(0, 6).join('，') : '') };
    paint();
  }
  function addSolution() {
    const d = state.draft, a = state.newApp, id = a.app_id.trim().toLowerCase();
    if (!/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(id)) { state.msg = { cls: 'bad', text: '应用编号要是 AgentHub 应用 id（36 位，形如 ee1aebd3-41ff-…）。' }; paint(); return; }
    if (!a.name.trim() || !words(a.inputs).length) { state.msg = { cls: 'bad', text: '登记方案要写名称和开始变量。' }; paint(); return; }
    d.solutions[id] = { name: a.name.trim(), inputs: words(a.inputs) };
    state.newApp = { app_id: '', name: '', inputs: 'call_id request_json' };
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
      ...Object.entries(d.solutions).map(([id, s]) => opt('APP:' + id, s.name + ' · ' + id.slice(0, 8), value === 'APP:' + id)),
      opt('KNOWLEDGE', t?.kind === 'KNOWLEDGE' ? '知识库（' + t.kb_refs.length + ' 条）' : '知识库…', value === 'KNOWLEDGE')];
    if (t?.kind === 'SOLUTION_APP' && !d.solutions[t.app_id]) opts.push(opt(value, '未登记的应用 · ' + t.app_id.slice(0, 8), true));
    const box = [el('select', { 'data-k': k, 'data-f': 'target', 'aria-label': sid + ' 的去向' }, opts)];
    if (t?.kind === 'KNOWLEDGE') {
      box.push(el('textarea', { 'data-k': k, 'data-f': 'kb', rows: '2', 'aria-label': '知识条目', placeholder: '一行一条：知识空间 UUID/文章编号', text: t.kb_refs.map(x => x.space_id + '/' + x.article_id).join('\n') }),
        el('input', { 'data-k': k, 'data-f': 'topics', value: t.topics.join(' '), placeholder: '话题，空格分隔', 'aria-label': '话题' }),
        el('input', { 'data-k': k, 'data-f': 'intents', value: (t.intents || []).join(' '), placeholder: '诉求（可空），空格分隔', 'aria-label': '诉求' }));
    }
    if (t?.kind === 'SOLUTION_APP') box.push(el('span', { cls: 're-app' }, el('code', { text: t.app_id }), ' ', appLink(t.app_id)));
    return el('div', { cls: 're-target' }, box);
  }
  function view() {
    const out = [el('h1', { text: '受理路由表' }), el('p', { cls: 're-lead', text: '每个场景选一个去向：方案应用、转安全、知识库，或无方案。方案应用先在「方案登记」里登记。保存后现网流程下一次运行就读新表，不用重发受理路由或交付流程；每次保存都留一版，可以载入旧版再存回去。' })];
    out.push(el('div', { cls: 're-card' }, el('div', { cls: 're-row' },
      el('label', { cls: 're-field' }, el('span', { text: '编辑口令' }), el('input', { type: 'password', id: 're-key', value: state.key, autocomplete: 'off' })),
      el('label', { cls: 're-field' }, el('span', { text: '路由表' }), el('input', { id: 're-table', value: state.table })),
      el('button', { type: 'button', cls: 'primary', 'data-act': 'load', disabled: state.busy, text: state.loaded ? '重新读取' : '读取' }))));
    if (state.msg) out.push(el('p', { cls: 're-status ' + state.msg.cls, role: state.msg.cls === 'bad' ? 'alert' : 'status', text: state.msg.text }));
    const d = state.draft; if (!state.loaded || !d) return out;
    const doc = state.doc?.schema === SCHEMA ? state.doc : null, v = state.view, diff = changes(d);
    out.push(el('p', { cls: 're-muted', text: doc ? state.table + ' · 现在是第 ' + state.version + ' 版 · 保存于 ' + doc.saved_at + ' · 内容摘要 ' + String(doc.content_sha256).slice(0, 12)
      + (state.basedOn !== state.version ? ' · 编辑区是第 ' + state.basedOn + ' 版' : '') : state.table + ' · 还没有 v2 版本' }));

    // 场景路由
    const counts = { SOLUTION_APP: 0, TRANSFER: 0, KNOWLEDGE: 0, NONE: 0 };
    for (const sid of Object.keys(d.scenes)) counts[kindOf(d.routes.get(key3(sid, v.issue_type, v.phase)))]++;
    const domains = [...new Set(Object.values(d.scenes).map(s => s.domain))].sort();
    const rows = sceneRows(d);
    out.push(el('section', { cls: 're-card' },
      el('h2', { text: '场景路由 · ' + (TYPE_LABEL[v.issue_type] || v.issue_type) + ' · ' + v.phase }),
      el('p', { cls: 're-muted', text: Object.keys(d.scenes).length + ' 个场景：方案应用 ' + counts.SOLUTION_APP + ' · 转安全 ' + counts.TRANSFER + ' · 知识库 ' + counts.KNOWLEDGE + ' · 无方案 ' + counts.NONE }),
      el('div', { cls: 're-row' },
        el('label', { cls: 're-field' }, el('span', { text: '问题类型' }), el('select', { id: 're-type' }, d.issue_types.map(t => opt(t, (TYPE_LABEL[t] || t) + ' ' + t, t === v.issue_type)))),
        el('label', { cls: 're-field' }, el('span', { text: '处理阶段' }), el('select', { id: 're-phase' }, d.phases.map(p => opt(p, p, p === v.phase)))),
        el('label', { cls: 're-field' }, el('span', { text: '域' }), el('select', { id: 're-domain' }, [opt('', '全部域', !v.domain), ...domains.map(x => opt(x, x, x === v.domain))])),
        el('label', { cls: 're-field re-grow' }, el('span', { text: '搜索' }), el('input', { id: 're-q', type: 'search', value: v.q, placeholder: '场景名、编号或应用 id' }))),
      el('div', { cls: 're-chips', role: 'group', 'aria-label': '按去向筛选' }, STATES.map(([k, t]) => el('button', { type: 'button', 'data-act': 'kind', 'data-v': k, 'aria-pressed': String(v.kind === k), text: t }))),
      el('div', { cls: 're-scroll re-tall' }, el('table', {}, el('thead', {}, el('tr', {}, ['场景编号', '场景名称', '域', '去向'].map(t => el('th', { text: t })))),
        el('tbody', {}, rows.map(([sid, s]) => {
          const k = key3(sid, v.issue_type, v.phase), changed = diff.some(c => c[0] === k);
          return el('tr', { cls: changed ? 're-changed' : null }, el('td', {}, el('code', { text: sid })), el('td', { text: s.name }), el('td', { text: s.domain }),
            el('td', {}, targetCell(sid, d.routes.get(k), d)));
        })))),
      el('p', { cls: 're-muted', text: '显示 ' + rows.length + ' / ' + Object.keys(d.scenes).length + ' 个场景' })));

    // 方案登记
    const used = {}; for (const t of d.routes.values()) if (t.kind === 'SOLUTION_APP') used[t.app_id] = (used[t.app_id] || 0) + 1;
    out.push(el('section', { cls: 're-card' }, el('h2', { text: '方案登记（' + Object.keys(d.solutions).length + ' 个应用）' }),
      el('p', { cls: 're-muted', text: '只有登记过的应用能被路由、能被「调用方案」工具调用；开始变量要和应用开始节点一致（统一入口是 call_id request_json）。一个场景只挂一个在用应用，副本和草稿不登记。' }),
      el('div', { cls: 're-scroll' }, el('table', {}, el('thead', {}, el('tr', {}, ['应用编号', '名称', '开始变量', '用到的路由', ''].map(t => el('th', { text: t })))),
        el('tbody', {}, Object.entries(d.solutions).map(([id, s]) => el('tr', {},
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

    // 保存
    out.push(el('section', { cls: 're-card' }, el('h2', { text: '保存' }),
      diff.length ? el('details', {}, el('summary', { text: '未保存的去向改动 ' + diff.length + ' 条' }),
        el('ul', { cls: 're-list' }, diff.slice(0, 200).map(([k, a, b]) => { const [sid, t, p] = k.split('|'); return el('li', {}, el('code', { text: sid }), ' ' + (d.scenes[sid]?.name || '') + ' · ' + (TYPE_LABEL[t] || t) + ' · ' + p + '：' + label(a, d) + ' → ' + label(b, d)); })))
        : el('p', { cls: 're-muted', text: '去向没有改动（方案登记或场景目录的改动也会随保存生效）。' }),
      el('label', { cls: 're-field' }, el('span', { text: '改动说明（必填）' }), el('textarea', { id: 're-note', rows: '2', text: d.note })),
      el('div', { cls: 're-row' }, el('button', { type: 'button', cls: 'primary', 'data-act': 'save', disabled: state.busy, text: state.busy ? '正在保存…' : '保存为第 ' + (state.version + 1) + ' 版' }))));

    out.push(el('details', { cls: 're-card' }, el('summary', { text: '场景目录（' + Object.keys(d.scenes).length + ' 个场景）' }),
      el('p', { cls: 're-muted', text: '场景目录取自现网定位。定位发布新目录后在这里导入：粘贴 {场景编号: {name, domain}}，或 [{scene_id, name, domain}]（定位 CATALOG 的行就可以）。' }),
      el('textarea', { id: 're-scenes', rows: '4', 'aria-label': '场景目录 JSON' }),
      el('div', { cls: 're-row' }, el('button', { type: 'button', 'data-act': 'scenes', text: '替换场景目录' }))));
    out.push(el('details', { cls: 're-card' }, el('summary', { text: '历史版本（' + state.history.length + '）' }),
      el('ul', { cls: 're-list' }, state.history.map(h => el('li', {}, '第 ' + h.version + ' 版 · ' + h.saved_at + ' · ' + h.routes + ' 条 · ' + (h.note || '（无说明）') + ' ',
        el('button', { type: 'button', cls: 'link', 'data-act': 'version', 'data-v': h.version, text: '载入编辑区' }))))));
    out.push(el('details', { cls: 're-card' }, el('summary', { text: '导入 / 导出 JSON' }),
      el('label', { cls: 're-field' }, el('span', { text: '粘贴整张路由表（' + SCHEMA + '）' }), el('textarea', { id: 're-import', rows: '5' })),
      el('div', { cls: 're-row' }, el('button', { type: 'button', 'data-act': 'import', text: '导入到编辑区' }), el('button', { type: 'button', 'data-act': 'export', text: '导出当前版本' })),
      state.exported ? el('textarea', { rows: '8', readonly: true, 'aria-label': '导出的 JSON', text: state.exported }) : null));
    return out;
  }
  function paint() {
    const a = document.activeElement;
    const focus = a && app.contains(a) ? (a.id ? '#' + a.id : a.dataset.k ? '[data-k="' + a.dataset.k + '"][data-f="' + a.dataset.f + '"]' : a.dataset.app ? '[data-app="' + a.dataset.app + '"][data-f="' + a.dataset.f + '"]' : null) : null;
    app.replaceChildren(...view());
    if (focus) app.querySelector(focus)?.focus();
  }

  app.addEventListener('click', e => {
    const b = e.target.closest('[data-act]'); if (!b) return;
    const act = b.dataset.act, d = state.draft;
    if (act === 'load') { state.key = app.querySelector('#re-key').value; state.table = app.querySelector('#re-table').value.trim(); keepKey(state.key); void load(); }
    else if (act === 'save') void save();
    else if (act === 'kind') { state.view.kind = b.dataset.v; paint(); }
    else if (act === 'reg' && d) addSolution();
    else if (act === 'unreg' && d) { delete d.solutions[b.dataset.v]; paint(); }
    else if (act === 'version') void loadVersion(Number(b.dataset.v));
    else if (act === 'import') importTable(app.querySelector('#re-import').value);
    else if (act === 'scenes') importScenes(app.querySelector('#re-scenes').value);
    else if (act === 'export') { state.exported = state.doc ? JSON.stringify(state.doc, null, 2) : '（还没有保存过的版本）'; paint(); }
  });
  // 输入只改草稿；会改变行数或下拉内容的才重画。
  app.addEventListener('input', e => {
    const t = e.target, d = state.draft;
    if (t.id === 're-q') { state.view.q = t.value; paint(); return; }
    if (t.id === 're-note' && d) { d.note = t.value; return; }
    if (t.id === 're-new-id') { state.newApp.app_id = t.value; return; }
    if (t.id === 're-new-name') { state.newApp.name = t.value; return; }
    if (t.id === 're-new-inputs') { state.newApp.inputs = t.value; return; }
    if (d && t.dataset.app && d.solutions[t.dataset.app]) { const s = d.solutions[t.dataset.app]; if (t.dataset.f === 'name') s.name = t.value; else s.inputs = words(t.value); return; }
    if (d && t.dataset.k && ['kb', 'topics', 'intents'].includes(t.dataset.f)) {
      const cur = d.routes.get(t.dataset.k); if (!cur || cur.kind !== 'KNOWLEDGE') return;
      if (t.dataset.f === 'kb') cur.kb_refs = t.value.split(/\n+/).map(s => s.trim()).filter(Boolean).map(s => { const [space_id, article_id] = s.split(/[\/\s]+/); return { space_id: space_id || '', article_id: article_id || '' }; });
      else if (t.dataset.f === 'topics') cur.topics = words(t.value);
      else { const w = words(t.value); if (w.length) cur.intents = w; else delete cur.intents; }
    }
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
    else if (val === 'KNOWLEDGE') { if (d.routes.get(k)?.kind !== 'KNOWLEDGE') d.routes.set(k, { kind: 'KNOWLEDGE', kb_refs: [], topics: [] }); }
    else if (val.startsWith('APP:')) d.routes.set(k, { kind: 'SOLUTION_APP', app_id: val.slice(4) });
    paint();
  });
  paint();
  if (state.key) void load();
})();
