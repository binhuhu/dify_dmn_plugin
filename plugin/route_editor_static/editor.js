(() => {
  'use strict';
  // 路由表编辑页：只经本端点的 /api/* 读写插件存储（请求头带编辑口令），不执行规则、不连别的地址。
  // 用 DOM API + textContent 出字，不拼 HTML；口令只放本标签页的 sessionStorage。
  const KEY_STORE = 'route-edit-key';
  const KINDS = [['SOLUTION_APP', '方案应用'], ['KNOWLEDGE', '知识库']];
  const app = document.getElementById('app');
  const base = (() => { const p = location.pathname; return p.endsWith('/') ? p : p.endsWith('/index.html') ? p.slice(0, -'index.html'.length) : p + '/'; })();
  const state = { key: readKey(), table: 'dispatch.route', loaded: false, doc: null, version: 0, draft: null, basedOn: 0, history: [], busy: false, msg: null, filter: '', exported: '' };

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

  async function api(path, body) {
    const r = await fetch(base + 'api/' + path, { method: body ? 'POST' : 'GET', cache: 'no-store', credentials: 'omit', redirect: 'error',
      headers: { 'X-Route-Edit-Key': state.key, ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
    let v; try { v = await r.json(); } catch (_) { throw Error('服务没返回 JSON（' + r.status + '）'); }
    if (!r.ok || v.error) { const e = Error(v.error?.message || '请求失败（' + r.status + '）'); e.code = v.error?.code; throw e; }
    return v;
  }
  const q = name => name + '?table=' + encodeURIComponent(state.table);

  // 存的文档 ↔ 页面草稿：知识条目一行一条 space_id/article_id，话题、诉求空格分隔。
  function toDraft(doc) {
    return { issue_types: doc?.issue_types || ['COMPLAINT', 'CONSULT', 'FEEDBACK'], phases: doc?.phases || ['STAGE_1', 'STAGE_2', 'STAGE_3'], registry: doc?.registry || {},
      note: '', routes: (doc?.routes || []).map(r => ({ scene_id: r.scene_id, issue_type: r.issue_type, phase: r.phase, kind: r.target.kind,
        app_id: r.target.app_id || '', kb: (r.target.kb_refs || []).map(k => k.space_id + '/' + k.article_id).join('\n'),
        topics: (r.target.topics || []).join(' '), intents: (r.target.intents || []).join(' ') })) };
  }
  const words = s => String(s || '').split(/[\s,，、]+/).map(x => x.trim()).filter(Boolean);
  function toDoc(d) {
    return { table: state.table, issue_types: d.issue_types, phases: d.phases, registry: d.registry, note: d.note.trim(), routes: d.routes.map(r => {
      if (r.kind === 'SOLUTION_APP') { const e = (d.registry[r.scene_id] || []).find(x => x.app_id === r.app_id); return { scene_id: r.scene_id.trim(), issue_type: r.issue_type, phase: r.phase, target: { kind: r.kind, app_id: r.app_id, solution_id: e ? e.solution_id : '' } }; }
      const t = { kind: r.kind, kb_refs: r.kb.split(/\n+/).map(s => s.trim()).filter(Boolean).map(s => { const [space_id, article_id] = s.split(/[\/\s]+/); return { space_id: space_id || '', article_id: article_id || '' }; }), topics: words(r.topics) };
      const intents = words(r.intents); if (intents.length) t.intents = intents;
      return { scene_id: r.scene_id.trim(), issue_type: r.issue_type, phase: r.phase, target: t };
    }) };
  }

  async function load() {
    state.busy = true; state.msg = null; paint();
    try {
      const [t, h] = await Promise.all([api(q('table')), api(q('history'))]);
      state.doc = t.doc; state.version = t.version; state.history = h.versions; state.draft = toDraft(t.doc); state.basedOn = t.version; state.loaded = true;
      state.msg = t.doc ? null : { cls: 'warn', text: '插件里还没有路由表 ' + state.table + '：先用「导入 JSON」放进第一版。' };
    } catch (e) { state.msg = { cls: 'bad', text: e.message }; if (e.code === 'EDIT_KEY_REJECTED') state.loaded = false; }
    state.busy = false; paint();
  }
  async function save() {
    const d = state.draft; if (!d) return;
    if (!d.note.trim()) { state.msg = { cls: 'bad', text: '写一句改动说明再保存。' }; paint(); return; }
    state.busy = true; state.msg = null; paint();
    try {
      const r = await api('table', { ...toDoc(d), expected_version: state.version });
      state.doc = r.doc; state.version = r.version; state.draft = toDraft(r.doc); state.basedOn = r.version;
      state.history = (await api(q('history'))).versions;
      state.msg = { cls: 'ok', text: '已保存第 ' + r.version + ' 版（' + r.doc.routes.length + ' 条路由）。现网流程下一次运行就读这一版。' };
    } catch (e) { state.msg = { cls: 'bad', text: e.message }; }
    state.busy = false; paint();
  }
  async function loadVersion(v) {
    try { const r = await api(q('version') + '&v=' + v); state.draft = toDraft(r.doc); state.basedOn = v; state.msg = { cls: 'warn', text: '已载入第 ' + v + ' 版到编辑区；写说明后保存，就成为第 ' + (state.version + 1) + ' 版（回滚）。' }; }
    catch (e) { state.msg = { cls: 'bad', text: e.message }; }
    paint();
  }
  // 导入：dmn-json.route-table/v1 或 admission-routing-table/v1（可带 registry）；没带登记表就沿用当前的。
  function importJson(text) {
    let v; try { v = JSON.parse(text); } catch (_) { state.msg = { cls: 'bad', text: '导入的内容不是 JSON。' }; paint(); return; }
    if (!v || !Array.isArray(v.routes)) { state.msg = { cls: 'bad', text: '导入的 JSON 要有 routes 列表。' }; paint(); return; }
    const cur = state.draft || toDraft(null);
    state.draft = { ...toDraft({ issue_types: v.issue_types || cur.issue_types, phases: v.phases || cur.phases, registry: v.registry || cur.registry, routes: v.routes }), note: cur.note };
    state.msg = { cls: 'warn', text: '已导入 ' + v.routes.length + ' 条路由到编辑区，还没保存。' }; paint();
  }

  function routeRow(r, i, d) {
    const sel = (f, list, value) => el('select', { 'data-row': i, 'data-f': f, 'aria-label': f }, list.map(([v, t]) => opt(v, t, v === value)));
    const reg = d.registry[r.scene_id] || [];
    // 方案应用 = AgentHub 上的一条解决方案流程（应用编号跨发布不变）；本页就开在 AgentHub 上，链接用同源路径。
    const target = r.kind === 'SOLUTION_APP'
      ? (reg.length ? [sel('app_id', [['', '选方案应用'], ...reg.map(e => [e.app_id, (e.name ? e.name + ' · ' : '') + e.solution_id + ' · ' + e.app_id.slice(0, 8)])], r.app_id),
          r.app_id ? el('span', { cls: 're-app' }, '应用编号 ', el('code', { text: r.app_id }), ' ',
            el('a', { href: location.origin + '/v2/app/' + encodeURIComponent(r.app_id) + '?mode=workflow', target: '_blank', rel: 'noopener noreferrer', text: '在 AgentHub 打开 ↗' })) : null]
        : el('span', { cls: 're-bad', text: '这个场景在方案登记表里没有方案应用；新方案要先随受理路由流程登记' }))
      : [el('textarea', { 'data-row': i, 'data-f': 'kb', rows: '2', 'aria-label': '知识条目', placeholder: '一行一条：知识空间 UUID/文章编号', text: r.kb }),
        el('input', { 'data-row': i, 'data-f': 'topics', value: r.topics, placeholder: '话题，空格分隔', 'aria-label': '话题' }),
        el('input', { 'data-row': i, 'data-f': 'intents', value: r.intents, placeholder: '诉求（可空），空格分隔', 'aria-label': '诉求' })];
    return el('tr', {},
      el('td', {}, el('input', { 'data-row': i, 'data-f': 'scene_id', value: r.scene_id, placeholder: 'P_xx_xx_xx', 'aria-label': '场景' })),
      el('td', {}, sel('issue_type', d.issue_types.map(t => [t, t]), r.issue_type)),
      el('td', {}, sel('phase', d.phases.map(p => [p, p]), r.phase)),
      el('td', {}, sel('kind', KINDS, r.kind)),
      el('td', {}, el('div', { cls: 're-target' }, target)),
      el('td', {}, el('button', { type: 'button', cls: 'link', 'data-act': 'del', 'data-row': i, text: '删除' })));
  }
  function view() {
    const out = [el('h1', { text: '路由表编辑' }), el('p', { cls: 're-lead', text: '场景 × 问题类型 × 处理阶段 → 方案应用 / 知识库。保存后现网流程下一次运行就读新表，不用重发受理路由流程；每次保存都留一版，可以载入旧版再存回去。' })];
    out.push(el('div', { cls: 're-card' }, el('div', { cls: 're-row' },
      el('label', { cls: 're-field' }, el('span', { text: '编辑口令' }), el('input', { type: 'password', id: 're-key', value: state.key, autocomplete: 'off' })),
      el('label', { cls: 're-field' }, el('span', { text: '路由表' }), el('input', { id: 're-table', value: state.table })),
      el('button', { type: 'button', cls: 'primary', 'data-act': 'load', disabled: state.busy, text: state.loaded ? '重新读取' : '读取' }))));
    if (state.msg) out.push(el('p', { cls: 're-status ' + state.msg.cls, role: state.msg.cls === 'bad' ? 'alert' : 'status', text: state.msg.text }));
    const d = state.draft; if (!state.loaded || !d) return out;
    const doc = state.doc;
    out.push(el('p', { cls: 're-muted', text: doc ? state.table + ' · 现在是第 ' + state.version + ' 版 · 保存于 ' + doc.saved_at + ' · ' + doc.routes.length + ' 条 · 内容摘要 ' + String(doc.content_sha256).slice(0, 12) + (state.basedOn !== state.version ? ' · 编辑区是第 ' + state.basedOn + ' 版' : '') : state.table + ' · 还没有任何版本' }));
    const f = state.filter.trim().toUpperCase(), rows = d.routes.map((r, i) => [r, i]).filter(([r]) => !f || r.scene_id.toUpperCase().includes(f));
    out.push(el('div', { cls: 're-card' },
      el('div', { cls: 're-row' }, el('button', { type: 'button', 'data-act': 'add', text: '新增一行' }),
        el('label', { cls: 're-field' }, el('span', { text: '按场景过滤' }), el('input', { id: 're-filter', value: state.filter, placeholder: 'P_02' })),
        el('span', { cls: 're-muted', text: '共 ' + d.routes.length + ' 条，最多 128 条' })),
      el('div', { cls: 're-scroll' }, el('table', {}, el('thead', {}, el('tr', {}, ['场景', '问题类型', '处理阶段', '去向', '目标', ''].map(t => el('th', { text: t })))),
        el('tbody', {}, rows.map(([r, i]) => routeRow(r, i, d))))),
      el('label', { cls: 're-field' }, el('span', { text: '改动说明（必填）' }), el('textarea', { id: 're-note', rows: '2', text: d.note })),
      el('div', { cls: 're-row' }, el('button', { type: 'button', cls: 'primary', 'data-act': 'save', disabled: state.busy, text: state.busy ? '正在保存…' : '保存为第 ' + (state.version + 1) + ' 版' }))));
    const scenes = Object.keys(d.registry);
    out.push(el('details', { cls: 're-card' }, el('summary', { text: '方案登记表（' + scenes.length + ' 个场景，只读）' }),
      el('p', { cls: 're-muted', text: '方案应用去向只能指向这里登记过的应用：受理回执按受理路由流程里的登记表核对。新方案要先随受理路由流程登记，再用「导入 JSON」带上新的登记表。' }),
      el('ul', { cls: 're-list' }, scenes.map(s => el('li', {}, el('code', { text: s }), ' ' + d.registry[s].map(e => (e.name ? e.name + ' ' : '') + e.solution_id + '（' + e.app_id + '）').join('；'))))));
    out.push(el('details', { cls: 're-card' }, el('summary', { text: '历史版本（' + state.history.length + '）' }),
      el('ul', { cls: 're-list' }, state.history.map(h => el('li', {}, '第 ' + h.version + ' 版 · ' + h.saved_at + ' · ' + h.routes + ' 条 · ' + (h.note || '（无说明）') + ' ',
        el('button', { type: 'button', cls: 'link', 'data-act': 'version', 'data-v': h.version, text: '载入编辑区' }))))));
    out.push(el('details', { cls: 're-card' }, el('summary', { text: '导入 / 导出 JSON' }),
      el('label', { cls: 're-field' }, el('span', { text: '粘贴路由表 JSON（routes，可带 registry / issue_types / phases）' }), el('textarea', { id: 're-import', rows: '5' })),
      el('div', { cls: 're-row' }, el('button', { type: 'button', 'data-act': 'import', text: '导入到编辑区' }), el('button', { type: 'button', 'data-act': 'export', text: '导出当前版本' })),
      state.exported ? el('textarea', { rows: '8', readonly: true, 'aria-label': '导出的 JSON', text: state.exported }) : null));
    return out;
  }
  function paint() {
    const a = document.activeElement, focus = a && app.contains(a) ? (a.id ? '#' + a.id : a.dataset.f ? '[data-row="' + a.dataset.row + '"][data-f="' + a.dataset.f + '"]' : null) : null;
    app.replaceChildren(...view());
    if (focus) app.querySelector(focus)?.focus();
  }
  app.addEventListener('click', e => {
    const b = e.target.closest('[data-act]'); if (!b) return;
    const act = b.dataset.act, d = state.draft;
    if (act === 'load') { state.key = app.querySelector('#re-key').value; state.table = app.querySelector('#re-table').value.trim(); keepKey(state.key); void load(); }
    else if (act === 'save') void save();
    else if (act === 'add' && d) { d.routes.push({ scene_id: '', issue_type: d.issue_types[0], phase: d.phases[0], kind: 'SOLUTION_APP', app_id: '', kb: '', topics: '', intents: '' }); state.filter = ''; paint(); }
    else if (act === 'del' && d) { d.routes.splice(Number(b.dataset.row), 1); paint(); }
    else if (act === 'version') void loadVersion(Number(b.dataset.v));
    else if (act === 'import') importJson(app.querySelector('#re-import').value);
    else if (act === 'export') { state.exported = state.doc ? JSON.stringify(state.doc, null, 2) : '（还没有保存过的版本）'; paint(); }
  });
  // 输入只改草稿不重画；换场景 / 去向要重画这一行的目标格。
  app.addEventListener('input', e => {
    const t = e.target, d = state.draft;
    if (t.id === 're-filter') { state.filter = t.value; paint(); return; }
    if (t.id === 're-note' && d) { d.note = t.value; return; }
    if (d && t.dataset.f && t.dataset.row != null) d.routes[Number(t.dataset.row)][t.dataset.f] = t.value;
  });
  app.addEventListener('change', e => {
    const t = e.target;
    if (!state.draft || t.dataset.row == null || !t.dataset.f) return;
    state.draft.routes[Number(t.dataset.row)][t.dataset.f] = t.value;
    if (['scene_id', 'kind', 'app_id'].includes(t.dataset.f)) paint();
  });
  paint();
  if (state.key) void load();
})();
