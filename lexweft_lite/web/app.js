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

function typeColor(name) { return (S.ov?.types || []).find(t => t.name === name)?.color || '#6b7280'; }
function typeChip(name) { return `<span class="chip type" style="background:${esc(typeColor(name))}">${esc(name)}</span>`; }
function typeOptions(sel) { return (S.ov?.types || []).map(t => `<option ${t.name === sel ? 'selected' : ''}>${esc(t.name)}</option>`).join(''); }

async function refreshOverview() {
  S.ov = await api('/api/overview');
  const s = S.ov.stats;
  $('#stats').textContent = `資料 ${num(s.documents)} ・ 概念 ${num(s.concepts)} ・ 関係 ${num(s.relations)}`;
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
  S.docs = await api('/api/documents');
  if (S.doc) S.doc = await api(`/api/documents/${S.doc.id}`).catch(() => null);
  renderDocs();
};

function renderDocs() {
  const rows = S.docs.map(d => `<tr class="click ${S.doc?.id === d.id ? 'sel' : ''}" data-doc="${d.id}">
      <td>${esc(d.title)}<div class="small muted">${esc(d.kind)} ・ ${num(d.paragraphs)} 段落</div></td>
      <td class="small">${d.concepts ? num(d.concepts) + ' 概念' : '<span class="muted">未記述</span>'}</td></tr>`).join('');
  $('#main').innerHTML = `<div class="cols">
    <div>
      <div class="panel">
        <h2>資料を入れる</h2>
        <div class="drop" id="drop">ここにファイルを落とす、または <button class="link" id="pick">選ぶ</button><div class="small">md / txt / html / pdf / docx / csv</div></div>
        <input type="file" id="file" multiple hidden accept=".md,.markdown,.txt,.html,.htm,.pdf,.docx,.csv">
        <h3>フォルダや URL から</h3>
        <div class="row"><input type="text" id="path" placeholder="/Users/.../資料フォルダ または https://..."><button class="btn" id="addpath">取り込む</button></div>
        <details style="margin-top:10px"><summary class="small">文章を貼り付ける</summary>
          <input type="text" id="ttitle" placeholder="題名" style="margin:8px 0"><textarea id="ttext" placeholder="本文"></textarea>
          <button class="btn" id="addtext" style="margin-top:6px">保存</button></details>
      </div>
      <div class="panel"><h2>資料 <span class="muted small">${num(S.docs.length)} 件</span></h2>
        ${S.docs.length ? `<table>${rows}</table>` : '<div class="empty">まだ資料がありません</div>'}</div>
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
    <div class="panel" style="margin-top:12px;background:var(--bg)">
      <div class="small muted">段落にチェックを入れて、その段落を根拠に概念を書く</div>
      <div class="row" style="margin-top:6px"><input type="text" id="cname" placeholder="概念の名前 (例: セル間の熱伝播)" style="flex:1;min-width:180px">
        <select id="ctype">${typeOptions('課題')}</select><button class="btn primary" id="cadd">書く</button></div>
      <div class="small muted" id="pickinfo" style="margin-top:4px">選んだ段落: ${S.picked.size ? [...S.picked].map(x => '¶' + x).join(', ') : 'なし'}</div>
    </div>
    ${paras}</div>`;
}

function bindDocs() {
  const drop = $('#drop'), file = $('#file');
  $('#pick').onclick = () => file.click();
  file.onchange = () => upload(file.files);
  drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = () => drop.classList.remove('over');
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over'); upload(e.dataTransfer.files); };
  $('#addpath').onclick = () => guard(async () => {
    const path = $('#path').value.trim(); if (!path) return;
    const r = await post('/api/documents/path', {path});
    toast(summary(r)); load();
  });
  $('#addtext').onclick = () => guard(async () => {
    const r = await post('/api/documents/text', {title: $('#ttitle').value, text: $('#ttext').value});
    toast(summary([r])); load();
  });
  $$('[data-doc]').forEach(tr => tr.onclick = () => guard(async () => { S.picked.clear(); S.doc = await api(`/api/documents/${tr.dataset.doc}`); renderDocs(); }));
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
  S.concepts = await api('/api/concepts?' + new URLSearchParams(S.layerQ ? {q: S.layerQ} : {}));
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
      <ul class="clist">${items.map(c => `<li data-c="${c.id}" class="${S.concept?.id === c.id ? 'sel' : ''}"><span>${esc(c.name)}</span><span class="small muted">${num(c.documents)} 資料</span></li>`).join('') || '<li class="muted small">なし</li>'}</ul></div>`;
  }).join('');
  $('#main').innerHTML = `<div class="row" style="margin-bottom:12px"><input type="text" id="lq" placeholder="名前・別名・説明で絞る" value="${esc(S.layerQ)}" style="flex:1;max-width:320px">
      <a class="btn" style="margin-left:auto;text-decoration:none" href="/api/export/layer.md" download="layer.md">意味層を Markdown で保存</a></div>
    <div class="cols"><div><div class="typecols" style="grid-template-columns:1fr">${cols}</div>
      <details class="panel"><summary class="small">型を足す (例: 効果、材料、評価指標)</summary>
        <div class="row" style="margin-top:8px"><input type="text" id="tname" placeholder="型の名前" style="flex:1"><button class="btn" id="tadd">足す</button></div>
        <input type="text" id="tdesc" placeholder="説明 (任意)" style="margin-top:6px"></details></div>
      <div>${conceptHtml()}</div></div>`;
  $('#lq').onchange = e => { S.layerQ = e.target.value.trim(); loaders.layer(); };
  $('#tadd').onclick = () => guard(async () => { await post('/api/types', {name: $('#tname').value, description: $('#tdesc').value}); toast('型を足しました'); load(); });
  $$('[data-c]').forEach(li => li.onclick = () => openConcept(+li.dataset.c));
  bindConcept();
}

function conceptHtml() {
  const c = S.concept;
  if (!c) return `<div class="panel empty">概念を選ぶと、別名・根拠の段落・関係が出ます。${S.ov.stats.concepts ? '' : '<br>まだ概念がありません。「LLM と接続」から Claude に書かせるか、資料の段落を選んで書けます。'}</div>`;
  const others = S.concepts.filter(x => x.id !== c.id);
  const rel = c.relations.map(r => `<tr><td>${r.direction === 'out' ? `<b>${esc(c.name)}</b> ─${esc(r.kind)}→ <button class="link" data-c2="${r.dst_id}">${esc(r.dst)}</button>` : `<button class="link" data-c2="${r.src_id}">${esc(r.src)}</button> ─${esc(r.kind)}→ <b>${esc(c.name)}</b>`}
      ${r.paragraph_id ? `<span class="small muted">¶${r.paragraph_id}</span>` : ''}</td><td style="width:48px;white-space:nowrap"><button class="link" data-delrel="${r.id}">外す</button></td></tr>`).join('');
  const ev = c.evidence.map(e => `<div class="para linked"><span class="pid">¶${e.paragraph_id}</span><button class="link" data-opendoc="${e.document_id}">${esc(e.title)}</button>
      <button class="link small" style="float:right" data-delev="${e.paragraph_id}">外す</button><div>${esc(e.text)}</div></div>`).join('');
  return `<div class="panel">
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
  await guard(async () => { S.picked.clear(); S.doc = await api(`/api/documents/${id}`); setTab('docs'); });
}

// ---------------- つながり ----------------
loaders.graph = async () => {
  S.graph = await api('/api/graph?' + new URLSearchParams({documents: S.gopts.documents, ...(S.gopts.type ? {type: S.gopts.type} : {})}));
  renderGraph();
};

function renderGraph() {
  const g = S.graph;
  $('#main').innerHTML = `<div class="row" style="margin-bottom:10px">
      <label><input type="checkbox" id="gdocs" ${S.gopts.documents ? 'checked' : ''}> 資料も出す</label>
      <select id="gtype"><option value="">すべての型</option>${typeOptions(S.gopts.type)}</select>
      <span class="muted small">ドラッグで移動、ホイールで拡大、点を押すと詳細</span></div>
    <div class="graphwrap">${g.nodes.length ? '<svg id="graph"></svg>' : '<div class="empty">まだつながりがありません。概念を書くとここに出ます。</div>'}
      <div class="legend">${(g.types || []).map(t => `<div><span class="dot" style="background:${esc(t.color)}"></span> ${esc(t.name)}</div>`).join('')}${S.gopts.documents ? '<div><span class="dot" style="background:#9ca3af"></span> 資料</div>' : ''}<div class="small muted">実線: 関係 / 点線: 根拠</div></div>
      <div class="side" id="gside" hidden></div></div>`;
  $('#gdocs').onchange = e => { S.gopts.documents = e.target.checked; loaders.graph(); };
  $('#gtype').onchange = e => { S.gopts.type = e.target.value; loaders.graph(); };
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
  const apply = () => root.setAttribute('transform', `translate(${view.x},${view.y}) scale(${view.k})`);
  const toWorld = (cx, cy) => { const b = svg.getBoundingClientRect(); return [(cx - b.left - view.x) / view.k, (cy - b.top - view.y) / view.k]; };
  function tick() {
    const n = nodes.length;
    for (let a = 0; a < n; a++) for (let b = a + 1; b < n; b++) {
      let dx = nodes[b].x - nodes[a].x, dy = nodes[b].y - nodes[a].y, d2 = dx * dx + dy * dy + .01;
      if (d2 > 640000) continue;
      const f = 3200 / d2 * alpha, d = Math.sqrt(d2);
      dx /= d; dy /= d; nodes[a].vx -= dx * f; nodes[a].vy -= dy * f; nodes[b].vx += dx * f; nodes[b].vy += dy * f;
    }
    edges.forEach(e => {
      const a = nodes[e.s], b = nodes[e.t], dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
      const L = e.relation ? 170 : 110, f = (d - L) * .02 * alpha;
      a.vx += dx / d * f; a.vy += dy / d * f; b.vx -= dx / d * f; b.vy -= dy / d * f;
    });
    nodes.forEach((p, i) => {
      p.vx += (W / 2 - p.x) * .0015 * alpha; p.vy += (H / 2 - p.y) * .0015 * alpha;
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
  for (let i = 0; i < 200; i++) tick();
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

function renderSearch() {
  const r = S.search;
  const concepts = r?.concepts?.length ? `<div class="panel"><h3 style="margin-top:0">概念</h3>${r.concepts.map(c => `<button class="link chip" data-c="${c.id}" style="border-color:${esc(typeColor(c.type))}">${esc(c.name)} <span class="muted">${esc(c.type)}</span></button>`).join('')}</div>` : '';
  const paras = r ? (r.paragraphs.length ? r.paragraphs.map(p => `<div class="panel"><div class="row" style="justify-content:space-between">
        <button class="link" data-opendoc="${p.document_id}"><b>${esc(p.title)}</b></button><span class="small muted">${p.heading ? esc(p.heading) + ' ・ ' : ''}¶${p.paragraph_id} ・ 一致: ${p.matched.map(esc).join(' / ')}</span></div>
        <div class="para">${highlight(p.text, r.queries)}</div>${p.concepts.map(c => `<button class="link chip" data-c="${c.id}">${esc(c.name)}</button>`).join('')}</div>`).join('')
      : '<div class="panel empty">見つかりませんでした。言い換えや別の表記を | で区切って足してみてください。</div>') : '';
  $('#main').innerHTML = `<div class="panel"><div class="row"><input type="text" id="q" value="${esc(S.searchQ)}" placeholder="例: 熱暴走 | 熱連鎖 | thermal runaway" style="flex:1"><button class="btn primary" id="go">探す</button></div>
      <div class="small muted" style="margin-top:6px">空白で区切った語はすべて含む段落を探します。| で区切ると言い換えとして別々に探し、順位をまとめます。</div></div>
    ${concepts}${paras}`;
  const go = () => guard(async () => {
    S.searchQ = $('#q').value.trim(); if (!S.searchQ) return;
    S.search = await api('/api/search?' + new URLSearchParams({q: S.searchQ}));
    renderSearch();
  });
  $('#go').onclick = go;
  $('#q').onkeydown = e => { if (e.key === 'Enter' && !e.isComposing) go(); };
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
      <li>Claude Desktop を一度終了して開き直します。道具の一覧に <b>lexweft-lite</b> が出れば接続できています。</li>
    </ol></div>
    <div class="panel"><h2>意味層を書かせる</h2>
      <p>Claude Desktop で、プロンプトの一覧から <b>build_layer</b> を選ぶか、次のように頼みます。</p>
      <pre class="code">LeXWeft Lite の、まだ意味層が無い資料を読んで、課題と解決手段を根拠の段落つきで書いて。解決手段が課題を解く関係も結んで。</pre>
      <p class="small muted">まだ意味層が無い資料: ${pending.length ? pending.map(d => esc(d.title)).join('、') : 'ありません'}</p>
      <h3>探すときの頼み方</h3>
      <pre class="code">LeXWeft Lite で「セル間の熱伝播」を解決する手段を、根拠の段落番号つきで一覧にして。言い換えも使って探して。</pre>
      <p class="small muted">Claude 以外でも、MCP に対応したクライアントなら同じ設定でつなげます。アプリ自身は LLM を呼ばず、API キーも使いません。</p></div>
    <div class="panel"><h2>保存先</h2><div class="small">${esc(S.ov.home)}</div>
      <div class="small muted">資料ごとの Markdown は markdown/ に、取り込んだファイルの写しは files/ にあります。<code>lexweft export</code> で意味層 (layer.md) と資料をまとめて書き出せます。</div></div>`;
  $('#copyjson').onclick = () => guard(async () => { await navigator.clipboard.writeText(json); toast('コピーしました'); });
};

// ---------------- 起動 ----------------
$$('#tabs button').forEach(b => b.onclick = () => setTab(b.dataset.tab));
let first = 'docs';
try { first = localStorage.getItem('lw-tab') || 'docs'; } catch (e) { /* 既定のまま */ }
setTab(loaders[first] ? first : 'docs');
