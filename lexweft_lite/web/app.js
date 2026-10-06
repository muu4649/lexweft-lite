'use strict';
// LeXWeft Lite の画面. 資料 / 意味層 / つながり / 検索 / LLM と接続.

const S = {tab: 'docs', ov: null, docs: [], doc: null, picked: new Set(), concepts: [], concept: null, layerQ: '',
           graph: null, gopts: {documents: true, type: ''}, gsel: null, search: null, searchQ: '', mcp: null};
const $ = (q, r = document) => r.querySelector(q);
const $$ = (q, r = document) => [...r.querySelectorAll(q)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const num = n => Number(n || 0).toLocaleString('ja-JP');

async function api(path, opts = {}) {
  const init = {method: opts.method || 'GET', headers: {}};
  if (init.method !== 'GET') init.headers['X-LexWeft'] = '1';
  if (opts.json !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.json); }
  if (opts.form) init.body = opts.form;
  const r = await fetch(path, init);
  const ct = r.headers.get('content-type') || '';
  const body = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) throw new Error(body?.detail || body || r.statusText);
  return body;
}
const post = (p, json) => api(p, {method: 'POST', json});
const del = p => api(p, {method: 'DELETE'});

let toastTimer = null;
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 3200);
}
async function guard(fn) { try { await fn(); } catch (e) { toast('失敗しました: ' + e.message); } }

function scopes() { return S.ov?.scopes || []; }
function currentScope() {
  const all = scopes();
  if (S.scope && all.some(x => x.scope === S.scope)) return S.scope;
  const folder = all.find(x => x.scope !== 'central');
  return folder ? folder.scope : (all[0]?.scope || 'central');
}
function setScope(scope) {
  S.scope = scope;
  try { localStorage.setItem('lw-scope', scope); } catch (e) { /* 保存できなくても動く */ }
}
function scopeSelect(withAll = false, id = 'scopesel', cur = null) {
  cur = cur ?? (withAll ? (S.scopeAll || '') : currentScope());
  const all = withAll ? `<option value="" ${cur === '' ? 'selected' : ''}>すべてのフォルダ</option>` : '';
  return `<select id="${id}" title="フォルダ">${all}${scopes().filter(x => x.exists !== false).map(x => `<option value="${esc(x.scope)}" ${x.scope === cur ? 'selected' : ''}>${esc(x.label)} (${num(x.documents_now || 0)} 件)</option>`).join('')}</select>`;
}
function whereBadge(x) {
  if (x.scope === 'central') return '<span class="chip">アプリ側に保存</span>';
  return x.location === 'folder' ? `<span class="chip" title="${esc(x.data_dir || '')}">意味層はフォルダの中（_LeXWeft）</span>`
    : `<span class="chip" title="${esc(x.reason || '')}">意味層はアプリ側に保存</span>`;
}
function bindScope(reload) {
  const sel = $('#scopesel');
  if (sel) sel.onchange = e => { setScope(e.target.value); S.mapSel = null; reload(); };
}

function typeColor(name) { return (S.ov?.types || []).find(t => t.name === name)?.color || '#6b7280'; }
function typeChip(name) { return `<span class="chip type" style="background:${esc(typeColor(name))}">${esc(name)}</span>`; }
function typeOptions(sel) { return (S.ov?.types || []).map(t => `<option ${t.name === sel ? 'selected' : ''}>${esc(t.name)}</option>`).join(''); }

async function refreshOverview() {
  S.ov = await api('/api/overview');
  const s = S.ov.stats;
  $('#stats').textContent = `資料 ${num(s.documents)} ・ 概念 ${num(s.concepts)} ・ 関係 ${num(s.relations)} ・ v${S.ov.version}`;
  const fb = S.ov.distribution?.feedback_url;
  if (fb) { $('#feedback').href = fb; $('#feedback').hidden = false; }
}

function setTab(tab) {
  S.tab = tab;
  $$('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
  try { localStorage.setItem('lw-tab', tab); } catch (e) { /* 保存できなくても動く */ }
  load();
}

async function load() {
  await guard(async () => {
    await refreshOverview();
    await loaders[S.tab]();
  });
}

// ---------------- 資料 ----------------
const loaders = {};
loaders.docs = async () => {
  const [list, outside] = await Promise.all([api('/api/documents?' + new URLSearchParams({q: S.docQ || '', limit: 300})), api('/api/documents/outside')]);
  S.docs = list.documents; S.docTotal = list.total; S.outside = outside.documents;
  if (S.doc) S.doc = await api(`/api/documents/${S.doc.id}`).catch(() => null);
  if (S.showFolders) S.folders = await api('/api/documents/folders?depth=' + (S.folderDepth || 4));
  renderDocs();
  if (S.doc) loadSimilar();
};

function jobHtml() {
  const j = S.job;
  if (S.scan) {
    const sc = S.scan, many = sc.files > 1000;
    const kinds = Object.entries(sc.by_suffix || {}).map(([k, n]) => `${esc(k)} ${num(n)}`).join(' ・ ');
    return `<div class="panel" style="background:var(--bg)"><b>このフォルダを取り込みますか</b>
      <div class="small" style="margin:6px 0;word-break:break-all">${esc(sc.path)}</div>
      <div>取り込めるファイル <b>${num(sc.files)}${sc.truncated ? ' 以上' : ''}</b> 件 <span class="small muted">${kinds}</span></div>
      ${sc.files === 0 ? '<div class="small muted">取り込める形式 (md / txt / html / pdf / docx / csv) のファイルがありません</div>' : ''}
      ${many ? '<div class="small" style="color:var(--warn);margin-top:4px">多いです。テーマごとのフォルダに絞ると、見通しがよくなります</div>' : ''}
      <div class="small muted" style="margin-top:4px">隠しフォルダ・アプリや開発用のフォルダ・ライセンス文は読みません</div>
      ${sc.is_dir && sc.files ? (sc.layer_location === 'folder'
        ? `<div class="small" style="margin-top:6px">意味層のデータは、このフォルダの中に <b>_LeXWeft</b> というフォルダを作って保存します。フォルダごと移動・コピーすると、意味層も一緒に付いていきます。</div>`
        : `<div class="small" style="margin-top:6px;color:var(--warn)">${esc(sc.layer_reason)}</div>`) : ''}
      <div class="row" style="margin-top:8px">${sc.files ? '<button class="btn primary" id="scango">取り込む</button>' : ''}<button class="btn" id="scanno">やめる</button></div></div>`;
  }
  if (!j) return '';
  const pct = j.total ? Math.round(j.done * 100 / j.total) : 0;
  const c = j.counts || {};
  const parts = [['added', '追加'], ['updated', '更新'], ['unchanged', '変更なし'], ['empty', '本文なし'], ['error', '読めない']].filter(([k]) => c[k]).map(([k, l]) => `${l} ${num(c[k])}`).join(' ・ ');
  const label = {running: '取り込んでいます', done: '取り込みました', cancelled: '止めました', failed: '取り込めませんでした'}[j.state] || j.state;
  return `<div class="panel" style="background:var(--bg)"><div class="row" style="justify-content:space-between"><b>${label}</b>
      ${j.state === 'running' ? '<button class="btn danger" id="jobstop">止める</button>' : '<button class="link small" id="jobclose">閉じる</button>'}</div>
    <div class="small" style="word-break:break-all">${esc(j.target)}</div>
    <div style="height:8px;background:var(--line);border-radius:4px;margin:8px 0;overflow:hidden"><div style="height:100%;width:${pct}%;background:var(--accent)"></div></div>
    <div class="small">${num(j.done)} / ${num(j.total)} 件 ${parts ? '・ ' + parts : ''}</div>
    ${j.current ? `<div class="small muted" style="word-break:break-all">${esc(j.current)}</div>` : ''}
    ${j.error_count ? `<details class="small"><summary>読めなかったファイル ${num(j.error_count)} 件</summary>${j.errors.map(e => `<div>${esc(e)}</div>`).join('')}</details>` : ''}</div>`;
}

let jobTimer = null;
function pollJob() {
  clearTimeout(jobTimer);
  jobTimer = setTimeout(async () => {
    if (S.tab !== 'home') return;
    try {
      S.job = await api('/api/jobs/latest');
      const box = $('#jobbox');
      if (box) { box.innerHTML = jobHtml(); bindJob(); }
      if (S.job.state === 'running') pollJob(); else load();
    } catch (e) { /* 次の読み込みで直る */ }
  }, 1000);
}

async function pickFolder() {
  // Mac アプリでは自前の窓、ブラウザではサーバーが OS の窓を出す
  if (window.webkit?.messageHandlers?.pickFolder) return await window.webkit.messageHandlers.pickFolder.postMessage({});
  return (await post('/api/pick-folder', {})).path;
}

async function prepare(path) {
  if (!path) return;
  if (/^https?:\/\//.test(path)) { S.job = await post('/api/jobs', {path}); S.scan = null; renderHome(); pollJob(); return; }
  S.scan = await api('/api/scan?' + new URLSearchParams({path}));
  S.scan.is_dir = !/\.(md|markdown|txt|text|html?|pdf|docx|csv)$/i.test(path);
  renderHome();
}

function bindJob() {
  const go = $('#scango'), no = $('#scanno'), stop = $('#jobstop'), close = $('#jobclose');
  if (go) go.onclick = () => guard(async () => {
    S.job = S.scan.is_dir ? await post('/api/sources', {path: S.scan.path}) : await post('/api/jobs', {path: S.scan.path});
    S.scan = null; renderHome(); pollJob();
  });
  if (no) no.onclick = () => { S.scan = null; renderHome(); };
  if (stop) stop.onclick = () => guard(async () => { S.job = await post(`/api/jobs/${S.job.id}/cancel`, {}); });
  if (close) close.onclick = () => { S.job = null; renderHome(); };
}

// ---------------- 取り込み ----------------
loaders.home = async () => {
  const [sources, job] = await Promise.all([api('/api/sources'), api('/api/jobs/latest')]);
  S.sources = sources;
  if (job && job.id) S.job = job;
  renderHome();
  if (S.job?.state === 'running') pollJob();
  if (scopes().some(x => x.building)) pollLayer();
};


function changesText(x) {
  const c = x.changes || {};
  const parts = [];
  if (c.added_documents) parts.push(`資料 +${num(c.added_documents)}`);
  if (c.removed_documents) parts.push(`資料 −${num(c.removed_documents)}`);
  if (c.updated_documents) parts.push(`更新 ${num(c.updated_documents)}`);
  if ((c.new_clusters || []).length && x.version > 1) parts.push(`新しいまとまり ${c.new_clusters.length}`);
  return x.version > 1 && parts.length ? `版 ${x.version}（前回から ${parts.join('・')}）` : (x.version ? `版 ${x.version}` : '');
}

function layerHtml() {
  const rows = scopes().filter(x => x.exists !== false);
  if (!S.ov.stats.documents) return '<div class="muted small">資料を取り込むと、ここで自動で作ります。</div>';
  if (!rows.length) return '<div class="small muted">フォルダを登録すると、フォルダごとに作ります。</div>';
  return `<table>${rows.map(x => {
    let st;
    if (x.building) st = '<span class="small">作っています…</span>';
    else if (x.error) st = `<span class="small" style="color:var(--warn)">作れませんでした</span>`;
    else if (!x.built_at) st = '<span class="small muted">まだ</span>';
    else st = `<span class="small">${num(x.clusters)} のまとまり</span>${x.stale ? ' <span class="small" style="color:var(--warn)">（資料が変わりました）</span>' : ''}<div class="small muted">${esc(changesText(x))}</div>`;
    return `<tr><td><b>${esc(x.label)}</b><div class="small muted">${num(x.documents_now)} 件の資料</div>${whereBadge(x)}</td><td>${st}</td>
      <td style="white-space:nowrap;text-align:right"><button class="btn" data-mapof="${esc(x.scope)}">地図</button> <button class="link small" data-rebuild="${esc(x.scope)}">作り直す</button></td></tr>`;
  }).join('')}</table>
  <div class="small muted" style="margin-top:6px">意味層はフォルダごとに作り、ふつうはそのフォルダの中の _LeXWeft に保存します。フォルダをまたいで探したいときは、「検索」で「すべてのフォルダ」を選びます。</div>`;
}

function bindLayer() {
  $$('[data-mapof]').forEach(b => b.onclick = () => { setScope(b.dataset.mapof); S.mapSel = null; setTab('map'); });
  $$('[data-rebuild]').forEach(b => b.onclick = () => guard(async () => { await post('/api/layer/rebuild', {scope: b.dataset.rebuild}); await refreshOverview(); renderHome(); pollLayer(); }));
}

let layerTimer = null;
function pollLayer() {
  clearTimeout(layerTimer);
  layerTimer = setTimeout(async () => {
    if (S.tab !== 'home') return;
    try {
      await refreshOverview();
      const box = $('#layerbox');
      if (box) { box.innerHTML = layerHtml(); bindLayer(); }
      if (scopes().some(x => x.building)) pollLayer();
    } catch (e) { /* 次で直る */ }
  }, 2000);
}

function renderHome() {
  const src = (S.sources || []).map(f => `<tr><td><div style="word-break:break-all">${esc(f.path)}</div>
      <div class="small muted">${num(f.documents)} 件 ・ ${f.last_run ? '最後に取り込み ' + esc(f.last_run.replace('T', ' ').slice(0, 16)) : 'まだ取り込んでいません'}${f.exists ? '' : ' ・ <span style="color:var(--warn)">フォルダが見つかりません</span>'}</div>
      ${whereBadge({scope: f.path, location: f.location, reason: f.reason, data_dir: f.data_dir})}
      ${f.changed ? '<span class="chip" style="color:var(--warn)">新しいファイル・変わったファイルがあります</span>' : ''}
      <label class="small"><input type="checkbox" data-auto="${esc(f.path)}" ${f.auto ? 'checked' : ''}> 変わったら自動で取り込む</label></td>
      <td style="white-space:nowrap;text-align:right"><button class="btn" data-run="${esc(f.path)}">取り込み直す</button> <button class="link small" data-unreg="${esc(f.path)}">外す</button></td></tr>`).join('');
  $('#main').innerHTML = `<div class="steps3">
    <div class="panel step"><div class="stepno">1</div><h2>フォルダを登録する</h2>
      <p class="small muted">資料の入ったフォルダを選びます。中の md / txt / html / pdf / docx / csv を読みます（隠しフォルダや開発用のフォルダは読みません）。</p>
      <div class="row"><button class="btn primary" id="pickdir">フォルダを追加</button>${(S.sources || []).length ? '<button class="btn" id="runall">すべて取り込み直す</button>' : ''}</div>
      ${src ? `<table style="margin-top:10px">${src}</table>` : '<div class="empty small">まだフォルダがありません</div>'}
      <details style="margin-top:10px"><summary class="small">ファイル・URL・文章を個別に入れる</summary>
        <div class="row" style="margin-top:8px"><button class="btn" id="pick">ファイルを選ぶ</button></div>
        <div class="drop" id="drop" style="margin-top:8px">ここにファイルを落としても入れられます</div>
        <input type="file" id="file" multiple hidden accept=".md,.markdown,.txt,.html,.htm,.pdf,.docx,.csv">
        <div class="row" style="margin-top:8px"><input type="text" id="path" placeholder="パス または https://..." style="flex:1"><button class="btn" id="addpath">確かめる</button></div>
        <input type="text" id="ttitle" placeholder="貼り付ける文章の題名" style="margin:8px 0"><textarea id="ttext" placeholder="本文"></textarea>
        <button class="btn" id="addtext" style="margin-top:6px">文章を保存</button></details>
    </div>
    <div class="panel step"><div class="stepno">2</div><h2>取り込む</h2>
      <div id="jobbox">${jobHtml() || `<div class="small muted">${S.ov.stats.documents ? `資料 ${num(S.ov.stats.documents)} 件を取り込んであります。` : 'フォルダを追加すると、ここで進み具合が見られます。'}</div>`}</div>
      ${S.ov.stats.documents ? '<div class="row" style="margin-top:8px"><button class="btn" id="todocs">資料の一覧を見る</button></div>' : ''}
    </div>
    <div class="panel step"><div class="stepno">3</div><h2>意味層ができる</h2>
      <p class="small muted">取り込むと自動で、フォルダごとに資料をベクトルにしてまとまり（クラスター）に分け、それぞれがどんな集まりかを語で示します。よく一緒に出る語のつながりも作ります。</p>
      <div id="layerbox">${layerHtml()}</div>
    </div></div>`;
  bindHome();
}

function bindHome() {
  const drop = $('#drop'), file = $('#file');
  $('#pickdir').onclick = () => guard(async () => { const path = await pickFolder(); if (path) await prepare(path); });
  if ($('#runall')) $('#runall').onclick = () => guard(async () => { S.job = await post('/api/sources/run', {}); renderHome(); pollJob(); });
  $$('[data-run]').forEach(b => b.onclick = () => guard(async () => { S.job = await post('/api/sources/run', {path: b.dataset.run}); renderHome(); pollJob(); }));
  $$('[data-unreg]').forEach(b => b.onclick = () => guard(async () => {
    const path = b.dataset.unreg;
    if (!confirm(`「${path}」の登録を外します。`)) return;
    const del = confirm('このフォルダの意味層のデータ（_LeXWeft：まとまり・地図・Claude が書いた課題と解決手段）も消しますか？\n元の資料は消えません。\nOK: 消す ／ やめる: 残す（あとで登録し直すと、そのまま使えます）');
    const r = await post('/api/sources/remove', {path, delete_data: del});
    toast(del && r.deleted ? '登録を外し、意味層のデータを消しました' : '登録を外しました（意味層のデータは残しています）'); load();
  }));
  $$('[data-auto]').forEach(c => c.onchange = () => guard(async () => { await post('/api/sources/auto', {path: c.dataset.auto, auto: c.checked}); toast(c.checked ? '変わったら自動で取り込みます' : '自動の取り込みを止めました'); }));
  $('#pick').onclick = () => file.click();
  file.onchange = () => upload(file.files);
  drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = () => drop.classList.remove('over');
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over'); upload(e.dataTransfer.files); };
  $('#addpath').onclick = () => guard(async () => prepare($('#path').value.trim()));
  $('#addtext').onclick = () => guard(async () => {
    const r = await post('/api/documents/text', {title: $('#ttitle').value, text: $('#ttext').value});
    toast(summary([r])); load();
  });
  if ($('#todocs')) $('#todocs').onclick = () => setTab('docs');
  bindJob();
  bindLayer();
}

async function loadSimilar() {
  if (!S.doc) return;
  try {
    S.similar = await api(`/api/documents/${S.doc.id}/similar`);
    const box = $('#similar');
    if (box) box.innerHTML = similarHtml();
    $$('#similar [data-opendoc]').forEach(b => b.onclick = () => openDoc(+b.dataset.opendoc));
  } catch (e) { /* 意味層がまだ無いとき */ }
}

function similarHtml() {
  if (!S.similar) return '<span class="small muted">計算しています…</span>';
  if (!S.similar.length) return '<span class="small muted">似た資料はまだありません（意味層を作ると出ます）</span>';
  return S.similar.map(d => `<div class="small"><button class="link" data-opendoc="${d.id}">${esc(d.title)}</button> <span class="muted">${d.similarity.toFixed(2)}</span></div>`).join('');
}

// ---------------- 資料 ----------------
function renderDocs() {
  const rows = S.docs.map(d => `<tr class="click ${S.doc?.id === d.id ? 'sel' : ''}" data-doc="${d.id}">
      <td>${esc(d.title)}<div class="small muted">${esc(d.folder || '')} ・ ${esc(d.kind)} ・ ${num(d.paragraphs)} 段落</div></td>
      <td class="small">${d.concepts ? num(d.concepts) + ' 概念' : '<span class="muted">未記述</span>'}</td></tr>`).join('');
  const folders = S.showFolders ? `<div style="margin-top:8px">
      <div class="row small"><span class="muted">まとめる階層</span><button class="btn" id="fdless">−</button><span>${S.folderDepth || 4}</span><button class="btn" id="fdmore">＋</button></div>
      <table>${(S.folders || []).map(f => `<tr><td class="small" style="word-break:break-all">${esc(f.folder)}</td><td class="small" style="white-space:nowrap">${num(f.documents)} 件</td>
        <td style="white-space:nowrap">${/^([A-Za-z]:)?[\\/]/.test(f.folder) ? `<button class="link small" data-delunder="${esc(f.folder)}">まとめて消す</button>` : ''}</td></tr>`).join('')}</table></div>` : '';
  $('#main').innerHTML = `<div class="cols">
    <div>
      <div class="panel"><div class="row" style="justify-content:space-between"><h2 style="margin:0">資料 <span class="muted small">${num(S.docTotal)} 件</span></h2>
          <button class="link small" id="togglefolders">${S.showFolders ? 'フォルダ別を閉じる' : 'フォルダ別に見る・消す'}</button></div>
        ${folders}
        ${S.outside && S.ov.sources ? `<div class="small" style="margin-top:8px;padding:8px;border-radius:6px;background:var(--bg)">登録したフォルダの外の資料が ${num(S.outside)} 件あります（意味層には入りません）。
          <button class="link" id="deloutside">まとめて消す</button></div>` : ''}
        <input type="text" id="docq" placeholder="題名・場所で絞る" value="${esc(S.docQ || '')}" style="margin:10px 0">
        ${S.docs.length ? `<table>${rows}</table>${S.docTotal > S.docs.length ? `<div class="small muted" style="margin-top:6px">新しい ${num(S.docs.length)} 件を表示しています。題名や場所で絞ってください</div>` : ''}` : '<div class="empty">まだ資料がありません</div>'}</div>
    </div>
    <div id="docdetail">${docDetailHtml()}</div></div>`;
  bindDocs();
}

function docDetailHtml() {
  const d = S.doc;
  if (!d) return `<div class="panel empty">左の一覧から資料を選ぶと、段落番号つきの本文が出ます。<br>意味層は「LLM と接続」から Claude などに書かせるか、段落を選んでここで書けます。</div>`;
  const byPara = {};
  d.concepts.forEach(c => c.paragraphs.forEach(p => (byPara[p] ||= []).push(c)));
  let head = null;
  const paras = d.paragraphs.map(p => {
    let h = '';
    if (p.heading && p.heading !== head) { head = p.heading; h = `<h3>${esc(p.heading.replace(/^#+\s*/, ''))}</h3>`; }
    const body = p.heading && p.text.startsWith(p.heading) ? p.text.slice(p.heading.length).replace(/^\n/, '') : p.text;
    const tags = (byPara[p.id] || []).map(c => `<button class="link chip" data-concept="${c.id}" style="border-color:${esc(typeColor(c.type))}">${esc(c.name)}</button>`).join('');
    return `${h}<div class="para ${byPara[p.id] ? 'linked' : ''}"><label><input type="checkbox" data-pick="${p.id}" ${S.picked.has(p.id) ? 'checked' : ''}> <span class="pid">¶${p.id}</span></label>${esc(body)}${tags ? `<div class="tags">${tags}</div>` : ''}</div>`;
  }).join('');
  const meta = Object.entries(d.meta || {}).filter(([k, v]) => typeof v !== 'object' && k !== 'title').map(([k, v]) => `<span class="chip">${esc(k)}: ${esc(v)}</span>`).join('');
  return `<div class="panel">
    <div class="row" style="justify-content:space-between"><h2 style="margin:0">${esc(d.title)}</h2>
      <div class="row"><button class="btn" id="copymd">Markdown をコピー</button><button class="btn danger" id="deldoc">削除</button></div></div>
    <div class="small muted" style="margin:4px 0 8px">${esc(d.source)}</div>${meta}
    <details class="panel" style="margin-top:10px;background:var(--bg)" open><summary class="small"><b>似た資料</b>（ベクトルの近さ）</summary><div id="similar" style="margin-top:6px">${similarHtml()}</div></details>
    <div class="panel" style="margin-top:12px;background:var(--bg)">
      <div class="small muted">段落にチェックを入れて、その段落を根拠に概念を書く</div>
      <div class="row" style="margin-top:6px"><input type="text" id="cname" placeholder="概念の名前 (例: セル間の熱伝播)" style="flex:1;min-width:180px">
        <select id="ctype">${typeOptions('課題')}</select><button class="btn primary" id="cadd">書く</button></div>
      <div class="small muted" id="pickinfo" style="margin-top:4px">選んだ段落: ${S.picked.size ? [...S.picked].map(x => '¶' + x).join(', ') : 'なし'}</div>
    </div>
    ${paras}</div>`;
}

function bindDocs() {
  $('#docq').onchange = e => { S.docQ = e.target.value.trim(); loaders.docs(); };
  if ($('#deloutside')) $('#deloutside').onclick = () => guard(async () => {
    if (!confirm(`登録したフォルダの外にある資料 ${num(S.outside)} 件を LeXWeft Lite から消します。元のファイルは消えません。`)) return;
    const r = await post('/api/documents/delete-outside', {});
    S.doc = null; toast(`${num(r.deleted)} 件を消しました`); load();
  });
  $('#togglefolders').onclick = () => { S.showFolders = !S.showFolders; loaders.docs(); };
  const fd = d => { S.folderDepth = Math.max(1, Math.min(12, (S.folderDepth || 4) + d)); loaders.docs(); };
  if ($('#fdless')) { $('#fdless').onclick = () => fd(-1); $('#fdmore').onclick = () => fd(1); }
  $$('[data-delunder]').forEach(b => b.onclick = () => guard(async () => {
    const f = b.dataset.delunder;
    if (!confirm(`「${f}」の資料をまとめて消します。元のファイルは消しません。この資料を根拠にした意味層の結びつきは外れます。`)) return;
    const r = await post('/api/documents/delete-under', {prefix: f});
    S.doc = null; toast(`${num(r.deleted)} 件を消しました`); load();
  }));
  $$('[data-doc]').forEach(tr => tr.onclick = () => guard(async () => { S.picked.clear(); S.doc = await api(`/api/documents/${tr.dataset.doc}`); S.similar = null; renderDocs(); loadSimilar(); }));
  bindDocDetail();
}

function bindDocDetail() {
  if (!S.doc) return;
  $$('[data-pick]').forEach(cb => cb.onchange = () => {
    const id = +cb.dataset.pick; cb.checked ? S.picked.add(id) : S.picked.delete(id);
    $('#pickinfo').textContent = '選んだ段落: ' + (S.picked.size ? [...S.picked].map(x => '¶' + x).join(', ') : 'なし');
  });
  $$('#docdetail [data-concept]').forEach(b => b.onclick = () => openConcept(+b.dataset.concept));
  $('#copymd').onclick = () => guard(async () => { await navigator.clipboard.writeText(await api(`/api/documents/${S.doc.id}/markdown`)); toast('Markdown をコピーしました'); });
  $('#deldoc').onclick = () => guard(async () => {
    if (!confirm(`「${S.doc.title}」を消します。この資料を根拠にした結びつきも消えます。`)) return;
    await del(`/api/documents/${S.doc.id}`); S.doc = null; toast('消しました'); load();
  });
  $('#cadd').onclick = () => guard(async () => {
    const name = $('#cname').value.trim();
    if (!name) return toast('概念の名前を入れてください');
    if (!S.picked.size) return toast('根拠にする段落にチェックを入れてください');
    const r = await post('/api/concepts', {name, type: $('#ctype').value, paragraph_ids: [...S.picked]});
    S.picked.clear(); toast(r.created ? `「${r.name}」を書きました` : `既にある「${r.name}」に根拠を足しました`); load();
  });
}

function summary(rs) {
  const n = k => rs.filter(r => r.status === k).length;
  const parts = [['added', '追加'], ['updated', '更新'], ['unchanged', '変更なし'], ['empty', '本文なし'], ['unsupported', '未対応']].filter(([k]) => n(k)).map(([k, l]) => `${l} ${n(k)}`);
  return parts.join(' ・ ') || '対象がありませんでした';
}

async function upload(files) {
  if (!files || !files.length) return;
  await guard(async () => {
    const form = new FormData();
    [...files].forEach(f => form.append('files', f));
    toast(`${files.length} 件を取り込んでいます…`);
    const r = await api('/api/documents/upload', {method: 'POST', form});
    toast(summary(r)); load();
  });
}

// ---------------- 意味層 ----------------
loaders.layer = async () => {
  S.concepts = await api('/api/concepts?' + new URLSearchParams({...(S.layerQ ? {q: S.layerQ} : {}), ...(S.scopeAll ? {scope: S.scopeAll} : {})}));
  if (S.concept) S.concept = await api(`/api/concepts/${S.concept.id}`).catch(() => null);
  renderLayer();
};

async function openConcept(id) {
  await guard(async () => { S.concept = await api(`/api/concepts/${id}`); if (S.tab !== 'layer') setTab('layer'); else renderLayer(); });
}

function renderLayer() {
  const cols = (S.ov.types || []).map(t => {
    const items = S.concepts.filter(c => c.type === t.name);
    return `<div class="panel typecol"><h3><span class="dot" style="background:${esc(t.color)}"></span>${esc(t.name)} <span class="muted small">${num(items.length)}</span></h3>
      ${t.description ? `<div class="small muted">${esc(t.description)}</div>` : ''}
      <ul class="clist">${items.map(c => `<li data-c="${c.id}" class="${S.concept?.id === c.id ? 'sel' : ''}"><span>${esc(c.name)}${S.scopeAll ? '' : ` <span class="small muted">${esc(c.folder)}</span>`}</span><span class="small muted">${num(c.documents)} 資料</span></li>`).join('') || '<li class="muted small">なし</li>'}</ul></div>`;
  }).join('');
  $('#main').innerHTML = `<div class="row" style="margin-bottom:12px">${scopeSelect(true, 'lscope')}<input type="text" id="lq" placeholder="名前・別名・説明で絞る" value="${esc(S.layerQ)}" style="flex:1;max-width:320px">
      <a class="btn" style="margin-left:auto;text-decoration:none" href="/api/export/layer.md${S.scopeAll ? '?scope=' + encodeURIComponent(S.scopeAll) : ''}" download="layer.md">意味層を Markdown で保存</a></div>
    <div class="cols"><div><div class="typecols" style="grid-template-columns:1fr">${cols}</div>
      <details class="panel"><summary class="small">型を足す (例: 効果、材料、評価指標)</summary>
        <div class="row" style="margin-top:8px"><input type="text" id="tname" placeholder="型の名前" style="flex:1"><button class="btn" id="tadd">足す</button></div>
        <input type="text" id="tdesc" placeholder="説明 (任意)" style="margin-top:6px"></details></div>
      <div>${conceptHtml()}</div></div>`;
  $('#lq').onchange = e => { S.layerQ = e.target.value.trim(); loaders.layer(); };
  $('#lscope').onchange = e => { S.scopeAll = e.target.value; S.concept = null; loaders.layer(); };
  $('#tadd').onclick = () => guard(async () => { await post('/api/types', {name: $('#tname').value, description: $('#tdesc').value}); toast('型を足しました'); load(); });
  $$('[data-c]').forEach(li => li.onclick = () => openConcept(+li.dataset.c));
  bindConcept();
}

function conceptHtml() {
  const c = S.concept;
  if (!c) return `<div class="panel empty">概念を選ぶと、別名・根拠の段落・関係が出ます。${S.ov.stats.concepts ? '' : '<br>まだ概念がありません。「LLM と接続」から Claude に書かせるか、資料の段落を選んで書けます。'}</div>`;
  const others = S.concepts.filter(x => x.id !== c.id && x.scope === c.scope);   // 結べるのは同じフォルダの概念だけ
  const rel = c.relations.map(r => `<tr><td>${r.direction === 'out' ? `<b>${esc(c.name)}</b> ─${esc(r.kind)}→ <button class="link" data-c2="${r.dst_id}">${esc(r.dst)}</button>` : `<button class="link" data-c2="${r.src_id}">${esc(r.src)}</button> ─${esc(r.kind)}→ <b>${esc(c.name)}</b>`}
      ${r.paragraph_id ? `<span class="small muted">¶${r.paragraph_id}</span>` : ''}</td><td style="width:48px;white-space:nowrap"><button class="link" data-delrel="${r.id}">外す</button></td></tr>`).join('');
  const ev = c.evidence.map(e => `<div class="para linked"><span class="pid">¶${e.paragraph_id}</span><button class="link" data-opendoc="${e.document_id}">${esc(e.title)}</button>
      <button class="link small" style="float:right" data-delev="${e.paragraph_id}">外す</button><div>${esc(e.text)}</div></div>`).join('');
  return `<div class="panel">
    <div class="small muted" style="margin-bottom:6px">フォルダ: ${esc(c.folder || '')}</div>
    <div class="row"><input type="text" id="ename" value="${esc(c.name)}" style="flex:1;font-weight:600"><select id="etype">${typeOptions(c.type)}</select></div>
    <textarea id="edesc" placeholder="説明" style="min-height:60px;margin-top:8px">${esc(c.description)}</textarea>
    <div class="row" style="margin-top:6px"><button class="btn primary" id="esave">保存</button><button class="btn danger" id="edel">概念を消す</button></div>
    <h3>別名</h3><div>${c.aliases.map(a => `<span class="chip">${esc(a)} <button class="link" data-delalias="${esc(a)}">×</button></span>`).join('') || '<span class="muted small">なし</span>'}</div>
    <div class="row" style="margin-top:6px"><input type="text" id="alias" placeholder="別名を足す (例: 熱連鎖)" style="flex:1"><button class="btn" id="aadd">足す</button></div>
    <h3>関係</h3>${rel ? `<table>${rel}</table>` : '<div class="muted small">なし</div>'}
    <div class="row" style="margin-top:6px"><select id="rdir"><option value="out">この概念が</option><option value="in">この概念へ</option></select>
      <select id="rother">${others.map(o => `<option value="${o.id}">${esc(o.name)} (${esc(o.type)})</option>`).join('')}</select>
      <input type="text" id="rkind" list="kinds" value="${c.type === '解決手段' ? '解決する' : '関連する'}" style="width:110px"><datalist id="kinds">${(S.ov.relation_kinds || []).map(k => `<option>${esc(k)}</option>`).join('')}</datalist>
      <button class="btn" id="radd">結ぶ</button></div>
    <h3>根拠の段落 <span class="muted small">${num(c.evidence_total)}</span></h3>${ev || '<div class="muted small">なし</div>'}
    <h3>まとめる</h3><div class="row"><select id="mdrop">${others.map(o => `<option value="${o.id}">${esc(o.name)}</option>`).join('')}</select>
      <button class="btn" id="merge">をこの概念にまとめる</button></div></div>`;
}

function bindConcept() {
  const c = S.concept; if (!c) return;
  const reload = async msg => { toast(msg); await load(); };
  $('#esave').onclick = () => guard(async () => { await api(`/api/concepts/${c.id}`, {method: 'PATCH', json: {name: $('#ename').value, type: $('#etype').value, description: $('#edesc').value}}); reload('保存しました'); });
  $('#edel').onclick = () => guard(async () => { if (!confirm(`「${c.name}」を消します`)) return; await del(`/api/concepts/${c.id}`); S.concept = null; reload('消しました'); });
  $('#aadd').onclick = () => guard(async () => { await post(`/api/concepts/${c.id}/aliases`, {aliases: [$('#alias').value]}); reload('別名を足しました'); });
  $$('[data-delalias]').forEach(b => b.onclick = () => guard(async () => { await del(`/api/concepts/${c.id}/aliases/${encodeURIComponent(b.dataset.delalias)}`); reload('別名を外しました'); }));
  $$('[data-delrel]').forEach(b => b.onclick = () => guard(async () => { await del(`/api/relations/${b.dataset.delrel}`); reload('関係を外しました'); }));
  $$('[data-delev]').forEach(b => b.onclick = () => guard(async () => { await del(`/api/concepts/${c.id}/evidence/${b.dataset.delev}`); reload('根拠を外しました'); }));
  $$('[data-c2]').forEach(b => b.onclick = () => openConcept(+b.dataset.c2));
  $$('[data-opendoc]').forEach(b => b.onclick = () => openDoc(+b.dataset.opendoc));
  const radd = $('#radd');
  if (radd) radd.onclick = () => guard(async () => {
    const other = +$('#rother').value; if (!other) return;
    const [source, target] = $('#rdir').value === 'out' ? [c.id, other] : [other, c.id];
    await post('/api/relations', {source, target, kind: $('#rkind').value}); reload('結びました');
  });
  const merge = $('#merge');
  if (merge) merge.onclick = () => guard(async () => {
    const drop = +$('#mdrop').value; if (!drop) return;
    if (!confirm('選んだ概念をこの概念にまとめます。選んだ概念の名前は別名になります')) return;
    await post('/api/concepts/merge', {keep: c.id, drop}); reload('まとめました');
  });
}

async function openDoc(id) {
  await guard(async () => { S.picked.clear(); S.doc = await api(`/api/documents/${id}`); S.similar = null; setTab('docs'); });
}

// ---------------- 地図 ----------------
loaders.map = async () => {
  S.map = await api('/api/map?' + new URLSearchParams({scope: currentScope()}));
  renderMap();
  if (S.map.status.building || (S.map.status.stale && S.map.status.documents_now)) setTimeout(() => S.tab === 'map' && loaders.map(), 3000);
};

function hasGroups(M) { return (M.groups || []).length >= 2; }
function groupColor(M, key) { return (M.groups || []).find(g => g.key === key)?.color || '#6b7280'; }
function groupLabel(M, key) { return (M.groups || []).find(g => g.key === key)?.label || key; }
// まとまりの中のグループの内訳 (細い帯)
function mixBar(M, c) {
  if (!hasGroups(M) || !c.groups) return '';
  const total = Object.values(c.groups).reduce((a, b) => a + b, 0) || 1;
  return `<span class="mix" title="${esc(Object.entries(c.groups).map(([k, n]) => `${groupLabel(M, k)} ${n} 件`).join(' ・ '))}">${
    M.groups.filter(g => c.groups[g.key]).map(g => `<i style="width:${(100 * c.groups[g.key] / total).toFixed(1)}%;background:${esc(g.color)}"></i>`).join('')}</span>`;
}
function renderMap() {
  const M = S.map, st = M.status, byGroup = hasGroups(M) && S.mapColor === 'group';
  const list = M.clusters.map(c => `<li data-cl="${c.id}" class="${S.mapSel === c.id ? 'sel' : ''}"><span><span class="dot" style="background:${esc(c.color)}"></span> ${esc(c.label)}${c.bridge ? ' <span class="chip bridge" title="2 つ以上のグループの資料が入っているまとまり">橋渡し</span>' : ''}${mixBar(M, c)}</span><span class="small muted">${num(c.size)}</span></li>`).join('');
  const colorSel = hasGroups(M) ? `<div class="row small" style="margin:8px 0">色:
      <label><input type="radio" name="mcolor" value="cluster" ${byGroup ? '' : 'checked'}> まとまり</label>
      <label><input type="radio" name="mcolor" value="group" ${byGroup ? 'checked' : ''}> グループ</label></div>` : '';
  const legend = byGroup
    ? `点 = 資料 ・ 色 = グループ（サブフォルダ）<br>${M.groups.map(g => `<span class="dot" style="background:${esc(g.color)}"></span> ${esc(g.label)} ${num(g.documents)} 件`).join('　')}<br>2 つの色が混ざっている所が、グループどうしの接点です`
    : '点 = 資料（近い点ほど中身が似ている） ・ 色 = まとまり<br>ホイールで拡大、ドラッグで移動、点を押すと資料';
  let empty = '';
  if (!M.points.length) empty = (st.building || (st.stale && st.documents_now)) ? '意味層を作っています…（資料が多いと数分かかります）'
    : st.documents_now ? 'まとまりを作れる語がありません。' : '「取り込み」でフォルダを登録すると、ここに資料の地図ができます。';
  $('#main').innerHTML = `<div class="mapcols">
    <div class="panel" style="max-height:calc(100vh - 110px);overflow:auto">
      <div style="margin-bottom:10px">${scopeSelect()}</div>${colorSel}
      <h2>まとまり <span class="small muted">${num(M.clusters.length)}</span></h2>
      <div class="small muted" style="margin-bottom:8px">資料の中身（語の使われ方）をベクトルにして、近いものをまとめました。名前はそのまとまりに特に多く出る語です。</div>
      <ul class="clist">${list}</ul>
      ${st.stale && !st.building ? '<div class="small" style="color:var(--warn);margin-top:8px">資料が変わりました。<button class="link" id="mrebuild">作り直す</button></div>' : ''}
      ${st.building ? '<div class="small muted" style="margin-top:8px">作り直しています…</div>' : ''}
    </div>
    <div>
      <div class="graphwrap">${M.points.length ? '<svg id="graph"></svg>' : `<div class="empty">${empty}</div>`}
        <div class="legend small">${legend}</div>
        <div class="side" id="gside" ${S.mapSel == null ? 'hidden' : ''}></div>
        <div id="tip" class="tip" hidden></div></div>
    </div></div>`;
  $$('[data-cl]').forEach(li => li.onclick = () => selectCluster(+li.dataset.cl));
  if ($('#mrebuild')) $('#mrebuild').onclick = () => guard(async () => { await post('/api/layer/rebuild', {scope: currentScope()}); loaders.map(); });
  bindScope(() => loaders.map());
  $$('[name=mcolor]').forEach(r => r.onchange = () => { S.mapColor = r.value; renderMap(); });
  if (M.points.length) drawMap(M);
  if (S.mapSel != null) selectCluster(S.mapSel);
}

function drawMap(M) {
  const svg = $('#graph'), NS = 'http://www.w3.org/2000/svg';
  const W = svg.clientWidth || 900, H = svg.clientHeight || 600, pad = 40;
  const color = Object.fromEntries(M.clusters.map(c => [c.id, c.color]));
  const byGroup = hasGroups(M) && S.mapColor === 'group';
  const root = document.createElementNS(NS, 'g'); svg.appendChild(root);
  const X = x => pad + x * (W - pad * 2), Y = y => pad + y * (H - pad * 2);
  const r = M.points.length > 2000 ? 3 : M.points.length > 500 ? 4 : 6;
  const dots = M.points.map(p => {
    const c = document.createElementNS(NS, 'circle');
    c.setAttribute('cx', X(p.x)); c.setAttribute('cy', Y(p.y)); c.setAttribute('r', r);
    c.setAttribute('fill', byGroup ? groupColor(M, p.g) : (color[p.c] || '#6b7280')); c.setAttribute('fill-opacity', .75);
    c.dataset.c = p.c; c.style.cursor = 'pointer';
    c.addEventListener('mouseenter', ev => { const t = $('#tip'); t.textContent = p.title; t.hidden = false; t.style.left = (ev.offsetX + 12) + 'px'; t.style.top = (ev.offsetY + 12) + 'px'; });
    c.addEventListener('mouseleave', () => ($('#tip').hidden = true));
    c.addEventListener('click', ev => { ev.stopPropagation(); openDoc(p.id); });
    root.appendChild(c); return c;
  });
  M.clusters.forEach(c => {
    const g = document.createElementNS(NS, 'g'); g.style.cursor = 'pointer';
    const t = document.createElementNS(NS, 'text');
    t.textContent = c.label.length > 18 ? c.label.slice(0, 17) + '…' : c.label;
    t.setAttribute('x', X(c.x)); t.setAttribute('y', Y(c.y)); t.setAttribute('text-anchor', 'middle'); t.setAttribute('class', 'maplabel');
    t.style.fill = byGroup ? 'var(--ink)' : c.color;
    g.appendChild(t); g.addEventListener('click', ev => { ev.stopPropagation(); selectCluster(c.id); });
    root.appendChild(g);
  });
  S.mapDots = dots;
  let view = {x: 0, y: 0, k: 1}, pan = null;
  const apply = () => root.setAttribute('transform', `translate(${view.x},${view.y}) scale(${view.k})`);
  svg.addEventListener('pointerdown', ev => { pan = {x: ev.clientX - view.x, y: ev.clientY - view.y, moved: false}; });
  svg.addEventListener('pointermove', ev => { if (pan) { view.x = ev.clientX - pan.x; view.y = ev.clientY - pan.y; pan.moved = true; apply(); } });
  svg.addEventListener('pointerup', () => { pan = null; });
  svg.addEventListener('wheel', ev => {
    ev.preventDefault();
    const b = svg.getBoundingClientRect(), mx = ev.clientX - b.left, my = ev.clientY - b.top, k = Math.min(12, Math.max(.5, view.k * (ev.deltaY < 0 ? 1.15 : .87)));
    view.x = mx - (mx - view.x) * k / view.k; view.y = my - (my - view.y) * k / view.k; view.k = k; apply();
    root.querySelectorAll('circle').forEach(c => c.setAttribute('r', r / Math.sqrt(view.k)));
    root.querySelectorAll('text').forEach(t => t.style.fontSize = (13 / Math.sqrt(view.k)) + 'px');
  }, {passive: false});
}

async function selectCluster(id) {
  S.mapSel = id;
  $$('[data-cl]').forEach(li => li.classList.toggle('sel', +li.dataset.cl === id));
  (S.mapDots || []).forEach(d => d.setAttribute('fill-opacity', +d.dataset.c === id ? .95 : .12));
  const side = $('#gside'); if (!side) return; side.hidden = false;
  await guard(async () => {
    const scope = currentScope();
    const c = await api(`/api/clusters/${id}?` + new URLSearchParams({scope}));
    const where = scope === 'central' ? 'フォルダの外の資料' : `フォルダ「${(scopes().find(x => x.scope === scope) || {}).label || scope}」`;
    const prompt = `LeXWeft Lite の${where}のまとまり ${c.id}「${c.label}」の資料を読んで、課題と解決手段を根拠の段落つきで書いて。解決手段が課題を解く関係も結んで。`;
    side.innerHTML = `<div class="row" style="justify-content:space-between"><b><span class="dot" style="background:${esc(c.color)}"></span> ${esc(c.label)}</b><button class="link" id="gclose">×</button></div>
      <div class="small muted">${num(c.size)} 件の資料</div>
      <h3>どんな集まりか（特に多く出る語）</h3><div>${c.terms.map(([t]) => `<span class="chip">${esc(t)}</span>`).join('')}</div>
      <h3>代表的な段落</h3>${c.paragraphs.map(p => `<div class="para small"><span class="pid">¶${p.paragraph_id}</span><button class="link" data-opendoc="${p.document_id}">${esc(p.title)}</button><div>${esc(p.text)}</div></div>`).join('') || '<div class="small muted">なし</div>'}
      <h3>中心に近い資料</h3>${c.documents.slice(0, 15).map(d => `<div class="small"><button class="link" data-opendoc="${d.id}">${esc(d.title)}</button></div>`).join('')}
      <h3>Claude で深める</h3><pre class="code">${esc(prompt)}</pre><button class="btn" id="copyprompt">頼み方をコピー</button>`;
    $('#gclose').onclick = () => { side.hidden = true; S.mapSel = null; (S.mapDots || []).forEach(d => d.setAttribute('fill-opacity', .75)); $$('[data-cl]').forEach(li => li.classList.remove('sel')); };
    $$('#gside [data-opendoc]').forEach(b => b.onclick = () => openDoc(+b.dataset.opendoc));
    $('#copyprompt').onclick = () => guard(async () => { await navigator.clipboard.writeText(prompt); toast('コピーしました。Claude Desktop に貼って頼んでください'); });
  });
}

// ---------------- グループのつながり ----------------
loaders.groups = async () => {
  const scope = currentScope();
  S.glinks = await api('/api/groups/links?' + new URLSearchParams({scope, ...(S.ga ? {a: S.ga} : {}), ...(S.gb ? {b: S.gb} : {})}));
  S.gedge = null;
  renderGroups();
};

function renderGroups() {
  const L = S.glinks, gs = L.groups || [];
  const gname = k => gs.find(g => g.key === k)?.label || k, gcol = k => gs.find(g => g.key === k)?.color || '#6b7280';
  const sel = (id, cur, other) => `<select id="${id}">${gs.filter(g => g.key !== other).map(g => `<option value="${esc(g.key)}" ${g.key === cur ? 'selected' : ''}>${esc(g.label)} (${num(g.documents)} 件)</option>`).join('')}</select>`;
  const names = gs.map(g => `<div class="row small" style="margin:4px 0"><span class="dot" style="background:${esc(g.color)}"></span>
      <span class="muted" style="min-width:9em">${esc(g.key || '（フォルダの直下）')}</span>
      <input type="text" data-gkey="${esc(g.key)}" value="${esc(g.label)}" style="width:14em"> <button class="btn" data-grename="${esc(g.key)}">名前を変える</button></div>`).join('');
  $('#main').innerHTML = `<div class="mapcols">
    <div class="panel" style="max-height:calc(100vh - 110px);overflow:auto">
      <div style="margin-bottom:10px">${scopeSelect()}</div>
      <h2>グループのつながり</h2>
      <div class="small muted" style="margin-bottom:8px">グループ = 登録したフォルダの中のサブフォルダ。片方のグループの資料ごとに、もう片方で中身が近い資料を探し、近い組を「まとまり ↔ まとまり」の線にまとめました。線が太いほど、近い組が多い。</div>
      ${gs.length >= 2 ? `<div class="small">左</div>${sel('ga', L.a, null)}<div class="small" style="margin-top:6px">右</div>${sel('gb', L.b, L.a)}
        <div class="small muted" style="margin-top:8px">近い組 ${num(L.pairs)} ・ 線 ${num(L.edges.length)}</div>` : ''}
      <h3>グループの名前</h3>${names || '<div class="small muted">なし</div>'}
    </div>
    <div><div class="graphwrap">${L.edges.length ? '<svg id="bip"></svg>' : `<div class="empty">${esc(L.note || 'つながりは見つかりませんでした。')}</div>`}
      <div class="side" id="gside" hidden></div><div id="tip" class="tip" hidden></div></div>
      ${L.edges.length ? `<div class="small muted" style="margin-top:6px">${esc(L.note)}</div>` : ''}</div></div>`;
  bindScope(() => { S.ga = S.gb = null; loaders.groups(); });
  if ($('#ga')) $('#ga').onchange = e => { S.ga = e.target.value; if (S.gb === S.ga) S.gb = null; loaders.groups(); };
  if ($('#gb')) $('#gb').onchange = e => { S.gb = e.target.value; loaders.groups(); };
  $$('[data-grename]').forEach(b => b.onclick = () => guard(async () => {
    const key = b.dataset.grename, label = $(`[data-gkey="${CSS.escape(key)}"]`).value;
    await post('/api/groups/rename', {scope: currentScope(), key, label}); toast('グループの名前を変えました'); loaders.groups();
  }));
  if (L.edges.length) drawGroups(L, gname, gcol);
}

function drawGroups(L, gname, gcol) {
  const svg = $('#bip'), NS = 'http://www.w3.org/2000/svg', wrap = svg.parentElement;
  const rows = Math.max(L.left.length, L.right.length);
  const H = Math.max(wrap.clientHeight, 90 + rows * 46), W = wrap.clientWidth || 900;
  svg.setAttribute('width', W); svg.setAttribute('height', H); svg.style.height = H + 'px'; wrap.style.overflow = 'auto';
  // 名前を置く幅を画面の幅から決める (狭い画面でも名前が切れないように)
  const room = Math.min(320, Math.max(120, W * 0.36)), chars = Math.max(6, Math.floor((room - 50) / 12.5));
  const xl = room, xr = W - room, top = 70;
  const step = n => (H - top - 30) / Math.max(1, n);
  const pos = {}; L.left.forEach((c, i) => pos['a' + c.id] = [xl, top + step(L.left.length) * (i + 0.5)]);
  L.right.forEach((c, i) => pos['b' + c.id] = [xr, top + step(L.right.length) * (i + 0.5)]);
  const maxPairs = Math.max(...L.edges.map(e => e.pairs)), maxDocs = Math.max(...L.left.concat(L.right).map(c => c.documents), 1);
  const el = (tag, attrs, parent = svg) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); parent.appendChild(e); return e; };
  const head = (x, anchor, key) => { const t = el('text', {x, y: 34, 'text-anchor': anchor, class: 'grouphead'}); t.textContent = gname(key); t.style.fill = gcol(key); };
  head(xl, 'end', L.a); head(xr, 'start', L.b);
  const paths = L.edges.map((e, i) => {
    const [x1, y1] = pos['a' + e.a], [x2, y2] = pos['b' + e.b], mx = (x1 + x2) / 2;
    const p = el('path', {d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, fill: 'none', stroke: 'var(--accent)',
                          'stroke-width': (1.5 + 12 * e.pairs / maxPairs).toFixed(1), 'stroke-opacity': .28, 'stroke-linecap': 'round'});
    p.style.cursor = 'pointer'; p.dataset.a = e.a; p.dataset.b = e.b;
    p.addEventListener('mouseenter', ev => { p.setAttribute('stroke-opacity', .75); const t = $('#tip'); t.textContent = `近い組 ${e.pairs} ・ 共通の言葉: ${e.terms.slice(0, 4).join('、')}`; t.hidden = false; t.style.left = (ev.offsetX + 12) + 'px'; t.style.top = (ev.offsetY + 12) + 'px'; });
    p.addEventListener('mouseleave', () => { if (S.gedge !== i) p.setAttribute('stroke-opacity', .28); $('#tip').hidden = true; });
    p.addEventListener('click', () => showGroupEdge(L, i, gname));
    return p;
  });
  S.gpaths = paths;
  const node = (c, side) => {
    const [x, y] = pos[side + c.id], r = 5 + 9 * Math.sqrt(c.documents / maxDocs);
    const g = el('g', {}); g.style.cursor = 'pointer';
    el('circle', {cx: x, cy: y, r, fill: c.color, stroke: gcol(side === 'a' ? L.a : L.b), 'stroke-width': 3}, g);
    const t = el('text', {x: side === 'a' ? x - r - 8 : x + r + 8, y: y + 4, 'text-anchor': side === 'a' ? 'end' : 'start', class: 'gnode'}, g);
    t.textContent = `${c.label.length > chars ? c.label.slice(0, chars - 1) + '…' : c.label} (${c.documents})`;
    const full = document.createElementNS(NS, 'title'); full.textContent = `${c.label} (${c.documents} 件)`; g.appendChild(full);
    g.addEventListener('click', () => paths.forEach(p => p.setAttribute('stroke-opacity', (side === 'a' ? +p.dataset.a : +p.dataset.b) === c.id ? .8 : .08)));
  };
  L.left.forEach(c => node(c, 'a')); L.right.forEach(c => node(c, 'b'));
}

function showGroupEdge(L, i, gname) {
  S.gedge = i;
  (S.gpaths || []).forEach((p, k) => p.setAttribute('stroke-opacity', k === i ? .85 : .12));
  const e = L.edges[i], la = L.left.find(c => c.id === e.a), lb = L.right.find(c => c.id === e.b);
  const where = (scopes().find(x => x.scope === currentScope()) || {}).label || '';
  const prompt = `LeXWeft Lite のフォルダ「${where}」で、グループ「${gname(L.a)}」のまとまり「${la.label}」とグループ「${gname(L.b)}」のまとまり「${lb.label}」の関わりを調べて。lw_group_links で近い資料の組を出し、両方を読んで、どの技術がどう生かせそうかを根拠の段落番号つきでまとめて。`;
  const side = $('#gside'); side.hidden = false;
  side.innerHTML = `<div class="row" style="justify-content:space-between"><b>${esc(la.label)}<br>↔ ${esc(lb.label)}</b><button class="link" id="gclose">×</button></div>
    <div class="small muted">近い組 ${num(e.pairs)} ・ 近さの平均 ${e.similarity.toFixed(2)}</div>
    <h3>共通の言葉</h3><div>${e.terms.map(t => `<span class="chip">${esc(t)}</span>`).join('') || '<span class="small muted">なし</span>'}</div>
    <h3>近い資料の組</h3>${e.examples.map(x => `<div class="pair small">
      <div><span class="dot" style="background:${esc(L.groups.find(g => g.key === L.a)?.color)}"></span> <button class="link" data-opendoc="${x.a.id}">${esc(x.a.title)}</button></div>
      <div><span class="dot" style="background:${esc(L.groups.find(g => g.key === L.b)?.color)}"></span> <button class="link" data-opendoc="${x.b.id}">${esc(x.b.title)}</button></div>
      <div class="muted">近さ ${x.similarity.toFixed(2)}${x.terms.length ? ' ・ ' + x.terms.map(esc).join('、') : ''}</div></div>`).join('')}
    <h3>Claude で深める</h3><pre class="code">${esc(prompt)}</pre><button class="btn" id="copyprompt">頼み方をコピー</button>`;
  $('#gclose').onclick = () => { side.hidden = true; S.gedge = null; (S.gpaths || []).forEach(p => p.setAttribute('stroke-opacity', .28)); };
  $$('#gside [data-opendoc]').forEach(b => b.onclick = () => openDoc(+b.dataset.opendoc));
  $('#copyprompt').onclick = () => guard(async () => { await navigator.clipboard.writeText(prompt); toast('コピーしました。Claude Desktop に貼って頼んでください'); });
}

// ---------------- つながり ----------------
loaders.graph = async () => {
  if (!S.gopts.mode) S.gopts.mode = S.ov.stats.concepts ? 'layer' : 'keywords';
  if (S.gopts.mode === 'keywords') {
    S.graph = await api('/api/keywords/graph?' + new URLSearchParams({limit: S.gopts.limit || 80, scope: currentScope(), ...(S.gopts.q ? {q: S.gopts.q} : {})}));
  } else {
    S.graph = await api('/api/graph?' + new URLSearchParams({documents: S.gopts.documents, ...(S.gopts.type ? {type: S.gopts.type} : {}), ...(S.scopeAll ? {scope: S.scopeAll} : {})}));
  }
  renderGraph();
};

function renderGraph() {
  const g = S.graph, kw = S.gopts.mode === 'keywords';
  const modeSel = `<select id="gmode"><option value="keywords" ${kw ? 'selected' : ''}>キーワードのつながり (自動)</option><option value="layer" ${kw ? '' : 'selected'}>意味層 (課題・解決手段)</option></select>`;
  const controls = kw
    ? `${scopeSelect()}<input type="text" id="gq" placeholder="テーマで絞る (例: 熱暴走 | 冷却)" value="${esc(S.gopts.q || '')}" style="width:240px">
       <select id="glimit">${[40, 80, 120].map(n => `<option ${+(S.gopts.limit || 80) === n ? 'selected' : ''}>${n}</option>`).join('')}</select><span class="small muted">語</span>`
    : `${scopeSelect(true, 'gscope')}<label><input type="checkbox" id="gdocs" ${S.gopts.documents ? 'checked' : ''}> 資料も出す</label>
       <select id="gtype"><option value="">すべての型</option>${typeOptions(S.gopts.type)}</select>`;
  let empty;
  if (kw) {
    empty = g.pending ? `資料の語を数えています (残り ${num(g.pending)} 件)。少し待ってから開き直してください。`
      : (S.gopts.q ? '絞り込んだ資料に、共通して出る語がありません。別の言葉で試してください。' : 'まだ資料がありません。「資料」タブから入れてください。');
  } else {
    empty = 'まだ意味層がありません。「LLM と接続」の手順で Claude に課題と解決手段を書かせると、ここに出ます。<br>それまでは「キーワードのつながり (自動)」で全体を見られます。';
  }
  const legend = kw
    ? `${(g.clusters || []).slice(0, 12).map(c => `<div class="small"><span class="dot" style="background:${esc(c.color)}"></span> ${esc(c.label)}</div>`).join('')}
       <div class="small muted">点 = 語（大きいほど多くの資料に出る） ・ 色 = いちばん多く出るまとまり ・ 線 = 同じ資料に一緒に出る</div>
       <div class="small muted">${num(g.documents)} 件の資料から${g.pending ? ` ・ 残り ${num(g.pending)} 件を数えています` : ''}</div>`
    : `${(g.types || []).map(t => `<div><span class="dot" style="background:${esc(t.color)}"></span> ${esc(t.name)}</div>`).join('')}${S.gopts.documents ? '<div><span class="dot" style="background:#9ca3af"></span> 資料</div>' : ''}<div class="small muted">実線: 関係 / 点線: 根拠</div>`;
  $('#main').innerHTML = `<div class="row" style="margin-bottom:10px">${modeSel}${controls}
      <span class="muted small">ドラッグで移動、ホイールで拡大、点を押すと詳細</span></div>
    <div class="graphwrap">${g.nodes.length ? '<svg id="graph"></svg>' : `<div class="empty">${empty}</div>`}
      <div class="legend">${legend}</div>
      <div class="side" id="gside" hidden></div></div>`;
  $('#gmode').onchange = e => { S.gopts.mode = e.target.value; loaders.graph(); };
  if (kw) {
    $('#gq').onchange = e => { S.gopts.q = e.target.value.trim(); loaders.graph(); };
    $('#glimit').onchange = e => { S.gopts.limit = +e.target.value; loaders.graph(); };
    bindScope(() => loaders.graph());
  } else {
    $('#gdocs').onchange = e => { S.gopts.documents = e.target.checked; loaders.graph(); };
    $('#gscope').onchange = e => { S.scopeAll = e.target.value; loaders.graph(); };
    $('#gtype').onchange = e => { S.gopts.type = e.target.value; loaders.graph(); };
  }
  if (g.nodes.length) drawGraph(g);
}

function drawGraph(g) {
  const svg = $('#graph'), NS = 'http://www.w3.org/2000/svg';
  const W = svg.clientWidth || 900, H = svg.clientHeight || 600;
  const nodes = g.nodes.map((n, i) => ({...n, x: W / 2 + Math.cos(i) * (80 + i * 3), y: H / 2 + Math.sin(i) * (80 + i * 3), vx: 0, vy: 0}));
  const idx = Object.fromEntries(nodes.map((n, i) => [n.id, i]));
  const edges = g.edges.filter(e => e.source in idx && e.target in idx).map(e => ({...e, s: idx[e.source], t: idx[e.target]}));
  const root = document.createElementNS(NS, 'g'); svg.appendChild(root);
  const defs = document.createElementNS(NS, 'defs');
  defs.innerHTML = '<marker id="arr" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="currentColor"/></marker>';
  svg.appendChild(defs);
  const lineEls = edges.map(e => {
    const l = document.createElementNS(NS, 'line');
    l.setAttribute('stroke', e.relation ? 'var(--ink)' : 'var(--muted)');
    l.setAttribute('stroke-opacity', e.relation ? .55 : .35);
    l.setAttribute('stroke-width', e.relation ? 1.4 : 1);
    if (!e.relation) l.setAttribute('stroke-dasharray', '3 3'); else { l.setAttribute('marker-end', 'url(#arr)'); l.style.color = 'var(--muted)'; }
    root.appendChild(l); return l;
  });
  const r = n => n.kind === 'document' ? 5 : Math.min(18, 6 + Math.sqrt(n.size) * 3);
  const nodeEls = nodes.map((n, i) => {
    const grp = document.createElementNS(NS, 'g'); grp.style.cursor = 'pointer';
    const shape = document.createElementNS(NS, n.kind === 'document' ? 'rect' : 'circle');
    if (n.kind === 'document') { shape.setAttribute('width', 10); shape.setAttribute('height', 10); shape.setAttribute('x', -5); shape.setAttribute('y', -5); shape.setAttribute('rx', 2); }
    else shape.setAttribute('r', r(n));
    shape.setAttribute('fill', n.color);
    const label = document.createElementNS(NS, 'text');
    label.textContent = n.label.length > 22 ? n.label.slice(0, 21) + '…' : n.label;
    label.setAttribute('x', r(n) + 4); label.setAttribute('y', 4);
    grp.append(shape, label); root.appendChild(grp);
    grp.addEventListener('pointerdown', ev => { ev.stopPropagation(); drag = {i, moved: false}; svg.setPointerCapture(ev.pointerId); });
    return grp;
  });
  let view = {x: 0, y: 0, k: 1}, drag = null, pan = null, alpha = 1;
  // つながっていない集まりどうしが重ならないよう、集まりごとに中心を置く
  const comp = new Array(nodes.length).fill(-1), adj = nodes.map(() => []);
  edges.forEach(e => { adj[e.s].push(e.t); adj[e.t].push(e.s); });
  let nc = 0;
  nodes.forEach((_, i) => {
    if (comp[i] >= 0) return;
    const stack = [i]; comp[i] = nc;
    while (stack.length) { const v = stack.pop(); adj[v].forEach(u => { if (comp[u] < 0) { comp[u] = nc; stack.push(u); } }); }
    nc++;
  });
  const sizes = Array.from({length: nc}, (_, c) => comp.filter(x => x === c).length);
  const order = sizes.map((n, c) => [n, c]).sort((p, q) => q[0] - p[0]).map(([, c]) => c);
  const cols = Math.ceil(Math.sqrt(nc)), rows = Math.ceil(nc / cols), centers = {};
  order.forEach((c, k) => { centers[c] = nc === 1 ? [W / 2, H / 2] : [W * ((k % cols) + .5) / cols, H * (Math.floor(k / cols) + .5) / rows]; });
  nodes.forEach((p, i) => { const [cx, cy] = centers[comp[i]]; p.x = cx + Math.cos(i) * (20 + i % 7 * 12); p.y = cy + Math.sin(i) * (20 + i % 7 * 12); });
  const rep = nodes.length < 30 ? 9000 : 3200, link = nodes.length < 30 ? 1.4 : 1;
  const apply = () => root.setAttribute('transform', `translate(${view.x},${view.y}) scale(${view.k})`);
  const toWorld = (cx, cy) => { const b = svg.getBoundingClientRect(); return [(cx - b.left - view.x) / view.k, (cy - b.top - view.y) / view.k]; };
  function tick() {
    const n = nodes.length;
    for (let a = 0; a < n; a++) for (let b = a + 1; b < n; b++) {
      let dx = nodes[b].x - nodes[a].x, dy = nodes[b].y - nodes[a].y, d2 = dx * dx + dy * dy + .01;
      if (d2 > 640000) continue;
      const f = rep / d2 * alpha, d = Math.sqrt(d2);
      dx /= d; dy /= d; nodes[a].vx -= dx * f; nodes[a].vy -= dy * f; nodes[b].vx += dx * f; nodes[b].vy += dy * f;
    }
    edges.forEach(e => {
      const a = nodes[e.s], b = nodes[e.t], dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
      const L = (e.relation ? 170 : 110) * link, f = (d - L) * .02 * alpha;
      a.vx += dx / d * f; a.vy += dy / d * f; b.vx -= dx / d * f; b.vy -= dy / d * f;
    });
    nodes.forEach((p, i) => {
      const [gx, gy] = centers[comp[i]];
      p.vx += (gx - p.x) * (nc > 1 ? .004 : .0015) * alpha; p.vy += (gy - p.y) * (nc > 1 ? .004 : .0015) * alpha;
      if (drag && drag.i === i) { p.vx = p.vy = 0; return; }
      p.vx *= .6; p.vy *= .6; p.x += p.vx; p.y += p.vy;
    });
  }
  function paint() {
    edges.forEach((e, k) => {
      const a = nodes[e.s], b = nodes[e.t], dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 1, rb = r(b) + 2;
      lineEls[k].setAttribute('x1', a.x); lineEls[k].setAttribute('y1', a.y);
      lineEls[k].setAttribute('x2', b.x - dx / d * rb); lineEls[k].setAttribute('y2', b.y - dy / d * rb);
    });
    nodes.forEach((p, i) => nodeEls[i].setAttribute('transform', `translate(${p.x},${p.y})`));
  }
  function loop() { if (!document.body.contains(svg)) return; if (alpha > .02) { tick(); alpha *= .985; paint(); } requestAnimationFrame(loop); }
  for (let i = 0; i < 400; i++) { tick(); alpha = Math.max(.05, alpha * .992); }   // 落ち着かせてから表示の大きさを合わせる
  // 最初は全体が入るように縮尺を合わせる
  const xs = nodes.map(p => p.x), ys = nodes.map(p => p.y);
  const bw = Math.max(...xs) - Math.min(...xs) + 360, bh = Math.max(...ys) - Math.min(...ys) + 100;   // 名前の文字が切れない余白
  view.k = Math.max(.25, Math.min(1.4, W / bw, H / bh));
  view.x = W / 2 - (Math.min(...xs) + Math.max(...xs)) / 2 * view.k;
  view.y = H / 2 - (Math.min(...ys) + Math.max(...ys)) / 2 * view.k;
  alpha = .04;
  paint(); apply(); loop();
  svg.addEventListener('pointerdown', ev => { pan = {x: ev.clientX - view.x, y: ev.clientY - view.y}; svg.setPointerCapture(ev.pointerId); });
  svg.addEventListener('pointermove', ev => {
    if (drag) { const [x, y] = toWorld(ev.clientX, ev.clientY); Object.assign(nodes[drag.i], {x, y}); drag.moved = true; alpha = Math.max(alpha, .3); paint(); }
    else if (pan) { view.x = ev.clientX - pan.x; view.y = ev.clientY - pan.y; apply(); }
  });
  svg.addEventListener('pointerup', () => { if (drag && !drag.moved) showNode(nodes[drag.i]); drag = null; pan = null; });
  svg.addEventListener('wheel', ev => {
    ev.preventDefault();
    const b = svg.getBoundingClientRect(), mx = ev.clientX - b.left, my = ev.clientY - b.top, k = Math.min(4, Math.max(.2, view.k * (ev.deltaY < 0 ? 1.1 : .9)));
    view.x = mx - (mx - view.x) * k / view.k; view.y = my - (my - view.y) * k / view.k; view.k = k; apply();
  }, {passive: false});
}

async function showNode(n) {
  const side = $('#gside'); side.hidden = false;
  await guard(async () => {
    if (n.kind === 'keyword') {
      const r = await api('/api/keywords/documents?' + new URLSearchParams({term: n.label, limit: 30}));
      side.innerHTML = `<div class="row" style="justify-content:space-between"><b>${esc(n.label)}</b><button class="link" id="gclose" title="閉じる">×</button></div>
        <div class="small muted">${num(r.total)} 件の資料に出る語</div>
        <div class="row" style="margin:8px 0"><button class="btn" id="gsearch">この語で段落を探す</button></div>
        ${r.documents.map(d => `<div class="small"><button class="link" data-opendoc="${d.id}">${esc(d.title)}</button> <span class="muted">${num(d.n)} 回</span></div>`).join('')}`;
      $('#gclose').onclick = () => (side.hidden = true);
      $('#gsearch').onclick = () => guard(async () => { S.searchQ = n.label; S.search = await api('/api/search?' + new URLSearchParams({q: n.label})); setTab('search'); });
      $$('#gside [data-opendoc]').forEach(b => b.onclick = () => openDoc(+b.dataset.opendoc));
      return;
    }
    if (n.kind === 'document') {
      side.innerHTML = `<div class="row" style="justify-content:space-between"><b>${esc(n.label)}</b><button class="link" id="gclose" title="閉じる">×</button></div><div class="small muted">資料</div><div style="margin-top:8px"><button class="btn" id="gopen">資料を開く</button></div>`;
      $('#gopen').onclick = () => openDoc(+n.id.slice(1));
      $('#gclose').onclick = () => (side.hidden = true);
      return;
    }
    const c = await api(`/api/concepts/${n.id.slice(1)}`);
    side.innerHTML = `<div class="row" style="justify-content:space-between"><b>${esc(c.name)}</b><span>${typeChip(c.type)}<button class="link" id="gclose" title="閉じる">×</button></span></div>
      ${c.description ? `<div class="small" style="margin-top:6px">${esc(c.description)}</div>` : ''}
      ${c.aliases.length ? `<div class="small muted" style="margin-top:6px">別名: ${c.aliases.map(esc).join(', ')}</div>` : ''}
      <div style="margin-top:8px">${c.relations.map(r => `<div class="small">${r.direction === 'out' ? `→ ${esc(r.kind)} ${esc(r.dst)}` : `← ${esc(r.src)} が${esc(r.kind)}`}</div>`).join('')}</div>
      <div class="small muted" style="margin-top:8px">根拠 ${num(c.evidence_total)} 段落</div>
      ${c.evidence.slice(0, 3).map(e => `<div class="para linked small"><span class="pid">¶${e.paragraph_id}</span>${esc(e.title)}<div>${esc(e.text.slice(0, 160))}</div></div>`).join('')}
      <button class="btn" id="gconcept">意味層で開く</button>`;
    $('#gconcept').onclick = () => openConcept(c.id);
    $('#gclose').onclick = () => (side.hidden = true);
  });
}

// ---------------- 検索 ----------------
loaders.search = async () => { renderSearch(); };

function highlight(text, queries) {
  const terms = [...new Set(queries.flatMap(q => q.split(/[\s\u3000]+/)).filter(t => t.length))].sort((a, b) => b.length - a.length);
  if (!terms.length) return esc(text);
  const re = new RegExp('(' + terms.map(t => esc(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'gi');
  return esc(text).replace(re, '<mark>$1</mark>');
}

function routeHtml(R) {
  const docs = R.route.documents || [];
  if (!docs.length) return '<div class="panel empty">関係する資料が見つかりませんでした。言い換えを | で並べて足してみてください。意味層ができていないフォルダは「取り込み」で作れます。</div>';
  const badge = w => `<span class="chip ${w.startsWith('意味') ? 'why-sem' : 'why-txt'}">${esc(w)}</span>`;
  const folderTag = d => R.route.across_folders ? `<span class="chip">${esc(d.folder)}</span>` : '';
  const clusters = (R.route.folders || []).filter(f => f.clusters.length).map(f => `
      <div class="small" style="margin-top:4px">${R.route.folders.length > 1 ? `<b>${esc(f.label)}</b>: ` : ''}${f.clusters.map(c => `<span class="chip"><span class="dot" style="background:${esc(c.color)}"></span> ${esc(c.label)} <span class="muted">${Math.round(c.share * 100)}%</span></span>`).join('')}</div>`).join('');
  const cards = docs.map(d => `<div class="panel"><div class="row" style="justify-content:space-between">
        <span><button class="link" data-opendoc="${d.document_id}"><b>${esc(d.title)}</b></button> ${folderTag(d)}</span>
        <span class="small muted"><span class="dot" style="background:${esc(d.cluster_color)}"></span> ${esc(d.cluster_label)}</span></div>
      ${d.paragraphs.map(p => `<div class="para"><span class="pid">¶${p.paragraph_id}</span>${esc(p.text)}<div class="tags">${p.why.map(badge).join('')}</div></div>`).join('')}</div>`).join('');
  const un = R.unread.unread || [];
  const unread = un.length ? `<div class="panel" style="border-color:var(--accent)"><h3 style="margin-top:0">あわせて確かめたい資料 <span class="small muted">（関係が強いのに、上の一覧には出ていない資料）</span></h3>
      ${un.map(d => `<div class="para"><button class="link" data-opendoc="${d.document_id}"><b>${esc(d.title)}</b></button> ${R.route.across_folders ? `<span class="chip">${esc(d.folder)}</span>` : ''}
        <span class="small muted"> ・ <span class="dot" style="background:${esc(d.cluster_color)}"></span> ${esc(d.cluster_label)}</span>
        <div class="small">${esc(d.check_paragraph.text)}</div><div class="tags">${d.why.map(badge).join('')}</div></div>`).join('')}</div>` : '';
  return `<div class="panel"><h3 style="margin-top:0">関係するまとまり</h3>${clusters}</div>` + cards + unread;
}

function renderSearch() {
  const mode = S.searchMode || 'route';
  const r = S.search;
  let body = '';
  if (mode === 'route' && S.route) body = routeHtml(S.route);
  if (mode === 'text' && r) {
    const concepts = r.concepts?.length ? `<div class="panel"><h3 style="margin-top:0">概念</h3>${r.concepts.map(c => `<button class="link chip" data-c="${c.id}" style="border-color:${esc(typeColor(c.type))}">${esc(c.name)} <span class="muted">${esc(c.type)}</span></button>`).join('')}</div>` : '';
    body = concepts + (r.paragraphs.length ? r.paragraphs.map(p => `<div class="panel"><div class="row" style="justify-content:space-between">
        <span><button class="link" data-opendoc="${p.document_id}"><b>${esc(p.title)}</b></button> <span class="chip">${esc(p.folder || '')}</span></span><span class="small muted">${p.heading ? esc(p.heading) + ' ・ ' : ''}¶${p.paragraph_id} ・ 一致: ${p.matched.map(esc).join(' / ')}</span></div>
        <div class="para">${highlight(p.text, r.queries)}</div>${p.concepts.map(c => `<button class="link chip" data-c="${c.id}">${esc(c.name)}</button>`).join('')}</div>`).join('')
      : '<div class="panel empty">見つかりませんでした。言い換えや別の表記を | で区切って足してみてください。</div>');
  }
  const hint = mode === 'route'
    ? '質問を文で書けます。意味層をたどって、言い方が違っても関係する資料を探し、まだ見ていない関連資料も示します。'
    : '空白で区切った語はすべて含む段落を探します。| で区切ると言い換えとして別々に探し、順位をまとめます。';
  $('#main').innerHTML = `<div class="panel">
      <div class="row" style="margin-bottom:8px"><button class="btn ${mode === 'route' ? 'primary' : ''}" id="m-route">意味層でたどる</button><button class="btn ${mode === 'text' ? 'primary' : ''}" id="m-text">文字で探す</button>
        <span style="margin-left:auto">${scopeSelect(true, 'searchscope')}</span></div>
      <div class="row"><input type="text" id="q" value="${esc(S.searchQ)}" placeholder="${mode === 'route' ? '例: 電池の熱暴走を防ぐ方法' : '例: 熱暴走 | 熱連鎖 | thermal runaway'}" style="flex:1"><button class="btn primary" id="go">探す</button></div>
      <div class="small muted" style="margin-top:6px">${hint}</div></div>${body}`;
  const go = () => guard(async () => {
    S.searchQ = $('#q').value.trim(); if (!S.searchQ) return;
    const sc = S.scopeAll ? {scope: S.scopeAll} : {};
    if (mode === 'route') S.route = await api('/api/route?' + new URLSearchParams({q: S.searchQ, ...sc}));
    else S.search = await api('/api/search?' + new URLSearchParams({q: S.searchQ, ...sc}));
    renderSearch();
  });
  $('#go').onclick = go;
  $('#q').onkeydown = e => { if (e.key === 'Enter' && !e.isComposing) go(); };
  $('#searchscope').onchange = e => { S.scopeAll = e.target.value; };
  $('#m-route').onclick = () => { S.searchMode = 'route'; renderSearch(); };
  $('#m-text').onclick = () => { S.searchMode = 'text'; renderSearch(); };
  $$('[data-c]').forEach(b => b.onclick = () => openConcept(+b.dataset.c));
  $$('[data-opendoc]').forEach(b => b.onclick = () => openDoc(+b.dataset.opendoc));
}

// ---------------- LLM と接続 ----------------
loaders.connect = async () => {
  S.mcp = await api('/api/mcp-config');
  const json = JSON.stringify(S.mcp, null, 2);
  const pending = S.ov.documents_without_concepts || [];
  $('#main').innerHTML = `<div class="panel"><h2>Claude Desktop につなぐ</h2>
    <ol class="steps">
      <li>ターミナルで次を実行すると、Claude Desktop の設定に LeXWeft Lite を足します (元の設定は .bak に残ります)。<pre class="code">${esc(S.mcp.mcpServers['lexweft-lite'].command)} mcp-config --write</pre></li>
      <li>手で足す場合は、Claude Desktop の設定 → 開発者 → 設定を編集 で開くファイルの <code>mcpServers</code> に次を入れます。
        <pre class="code" id="mcpjson">${esc(json)}</pre><button class="btn" id="copyjson">コピー</button></li>
      <li>Claude Desktop をいったん終了して、開き直します（Mac は ⌘Q。Windows はタスクトレイの Claude アイコンを右クリックして「終了」。× で閉じるだけでは終了しません）。</li>
      <li>新しいチャットで「<b>LeXWeft Lite に入っている資料は何件？</b>」と聞きます。件数が返ってくれば、つながっています。「LeXWeft Lite を使ってよいか」と聞かれたら「許可」を押してください。</li>
    </ol></div>
    <div class="panel"><h2>意味層を書かせる</h2>
      <p>Claude Desktop のチャットで、次のように頼みます。</p>
      <pre class="code">LeXWeft Lite の、まだ意味層が無い資料を読んで、課題と解決手段を根拠の段落つきで書いて。解決手段が課題を解く関係も結んで。</pre>
      <p class="small muted">まだ意味層が無い資料: ${pending.length ? pending.map(d => esc(d.title)).join('、') : 'ありません'}</p>
      <h3>調べるときの頼み方</h3>
      <p class="small">次のように頼むと、Claude は意味層をたどって関係する資料を探し、答える前に読み残しを確かめます。</p>
      <pre class="code">LeXWeft Lite で「セル間の熱伝播」を解決する方法を調べて。関係する資料を読み残しがないように確かめてから、根拠の段落番号をつけて答えて。</pre>
      <p class="small muted">Claude 以外でも、MCP に対応したクライアントなら同じ設定でつなげます。アプリ自身は LLM を呼ばず、API キーも使いません。</p></div>
    <div class="panel"><h2>データの扱い</h2>
      <p class="small">資料と意味層は、この PC の保存先にだけ保存します。アプリ自身は外部に送りません。</p>
      <p class="small">ただし、Claude Desktop などの LLM につないで資料を読ませると、読ませた部分はその LLM サービスに送られます。社外秘の資料を扱うときは、お使いの LLM サービスの規約と社内の決まりを確かめてください。</p>
      ${S.ov.distribution?.feedback_url ? `<p><a class="btn" href="${esc(S.ov.distribution.feedback_url)}" target="_blank" rel="noopener">感想を送る</a></p>` : ''}</div>
    <div class="panel"><h2>保存先</h2><div class="small">${esc(S.ov.home)}</div>
      <div class="small muted">資料ごとの Markdown は markdown/ に、取り込んだファイルの写しは files/ にあります。<code>lexweft export</code> で意味層 (layer.md) と資料をまとめて書き出せます。</div></div>`;
  $('#copyjson').onclick = () => guard(async () => { await navigator.clipboard.writeText(json); toast('コピーしました'); });
};

// ---------------- 起動 ----------------
$$('#tabs button').forEach(b => b.onclick = () => setTab(b.dataset.tab));
// 表示 (自動 / ライト / ダーク)
(() => {
  const sel = $('#theme');
  let cur = 'auto';
  try { cur = localStorage.getItem('lw-theme') || 'auto'; } catch (e) { /* 既定のまま */ }
  sel.value = cur;
  sel.onchange = () => {
    const v = sel.value;
    if (v === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = v;
    try { localStorage.setItem('lw-theme', v); } catch (e) { /* 保存できなくても動く */ }
    if (S.tab === 'map' || S.tab === 'graph') loaders[S.tab]();
  };
})();
try { S.scope = localStorage.getItem('lw-scope') || null; } catch (e) { /* 既定のまま */ }
let first = 'home';
try { first = localStorage.getItem('lw-tab') || 'home'; } catch (e) { /* 既定のまま */ }
setTab(loaders[first] ? first : 'home');
