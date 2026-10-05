// LeXWeft Lite のプレイ動画の台本. 画面の上に字幕・カーソル・タイトルを重ね、操作を順に再生する.
(async () => {
  const DEMO = window.__DEMO_PATH__;
  const MASK = [[DEMO.replace(/\/調べ物$/, '/'), '~/Documents/'], ['video/調べ物', 'Documents/調べ物']];
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // ---- 見た目の部品 ----
  const st = document.createElement('style');
  st.textContent = `
  #v-cap{position:fixed;left:50%;bottom:38px;transform:translateX(-50%);background:rgba(15,26,30,.88);color:#fff;font:600 26px/1.5 "Hiragino Sans",sans-serif;
    padding:14px 30px;border-radius:14px;z-index:9999;max-width:80vw;text-align:center;box-shadow:0 8px 30px rgba(0,0,0,.25);transition:opacity .3s}
  #v-cap small{display:block;font-weight:400;font-size:18px;color:#bfe7ef}
  #v-cur{position:fixed;left:0;top:0;width:30px;height:30px;z-index:10000;pointer-events:none;transition:transform .7s cubic-bezier(.4,.1,.2,1)}
  #v-ring{position:fixed;width:46px;height:46px;border-radius:50%;border:3px solid #0e7490;z-index:9998;pointer-events:none;opacity:0;transform:scale(.4);transition:transform .35s,opacity .35s}
  #v-title{position:fixed;inset:0;background:#0f1a1e;color:#fff;z-index:10001;display:flex;flex-direction:column;align-items:center;justify-content:center;
    font-family:"Hiragino Sans",sans-serif;transition:opacity .6s}
  #v-title h1{font-size:64px;margin:0 0 18px;letter-spacing:.02em} #v-title h1 span{color:#22a3c4}
  #v-title p{font-size:28px;color:#cfe3e8;margin:0} #v-title .k{font-size:18px;color:#7dd3e4;margin-bottom:22px;letter-spacing:.1em}
  #v-dlg{position:fixed;left:50%;top:46%;transform:translate(-50%,-50%);width:520px;background:#fff;border-radius:12px;box-shadow:0 20px 60px rgba(0,0,0,.35);
    z-index:9997;font:15px "Hiragino Sans",sans-serif;color:#1f2328;overflow:hidden}
  #v-dlg .h{padding:12px 16px;background:#f3f5f4;font-weight:600} #v-dlg .r{padding:10px 16px;border-bottom:1px solid #eee}
  #v-dlg .sel{background:#0e7490;color:#fff} #v-dlg .f{padding:12px 16px;text-align:right}
  #v-dlg button{font:inherit;padding:6px 16px;border-radius:6px;border:1px solid #0e7490;background:#0e7490;color:#fff}`;
  document.head.appendChild(st);
  const cap = Object.assign(document.createElement('div'), {id: 'v-cap'}); cap.style.opacity = 0;
  const cur = Object.assign(document.createElement('div'), {id: 'v-cur'});
  cur.innerHTML = '<svg viewBox="0 0 24 24" width="30" height="30"><path d="M4 2l15 9-6.5 1.6L10 19z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  const ring = Object.assign(document.createElement('div'), {id: 'v-ring'});
  document.body.append(cap, cur, ring);
  let cx = innerWidth * 0.55, cy = innerHeight * 0.6;
  cur.style.transform = `translate(${cx}px,${cy}px)`;

  // 画面に出るパスを伏せる (画面が書き換わるたびに)
  const mask = root => {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); let n;
    while ((n = w.nextNode())) { let v = n.nodeValue, o = v; for (const [a, b] of MASK) v = v.split(a).join(b); if (v !== o) n.nodeValue = v; }
    document.querySelectorAll('input').forEach(i => { for (const [a, b] of MASK) if (i.value.includes(a)) i.value = i.value.split(a).join(b); });
  };
  new MutationObserver(() => mask(document.body)).observe(document.body, {childList: true, subtree: true, characterData: true});

  const caption = async (text, sub = '', hold = 0) => {
    cap.style.opacity = 0; await sleep(250);
    cap.innerHTML = text + (sub ? `<small>${sub}</small>` : '');
    cap.style.opacity = 1; if (hold) await sleep(hold);
  };
  const $ = (q) => document.querySelector(q);
  const waitFor = async (fn, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { const v = fn(); if (v) return v; await sleep(150); } throw new Error('timeout'); };
  const moveTo = async (el, dx = 0.5, dy = 0.5) => {
    if (typeof el === 'string') el = await waitFor(() => $(el));
    el.scrollIntoView({block: 'nearest', inline: 'nearest'}); await sleep(120);
    const r = el.getBoundingClientRect();
    cx = r.left + r.width * dx; cy = r.top + r.height * dy;
    cur.style.transform = `translate(${cx - 4}px,${cy - 2}px)`; await sleep(800);
    return el;
  };
  const click = async (el, dx, dy, pretend = false) => {
    el = await moveTo(el, dx, dy);
    ring.style.left = (cx - 23) + 'px'; ring.style.top = (cy - 23) + 'px';
    ring.style.opacity = 1; ring.style.transform = 'scale(1)'; await sleep(180);
    if (!pretend) el.click();   // pretend: 押したように見せるだけ (この PC の窓を開かない)
    await sleep(220); ring.style.opacity = 0; ring.style.transform = 'scale(.4)';
    return el;
  };
  // 念のため、本物のフォルダ選択の窓は開かないようにする
  window.pickFolder = async () => null;
  const type = async (el, text) => {
    el = await click(el);
    el.value = ''; for (const ch of text) { el.value += ch; el.dispatchEvent(new Event('input')); await sleep(90); }
    return el;
  };
  const title = async (kicker, head, sub, ms) => {
    const t = Object.assign(document.createElement('div'), {id: 'v-title'});
    t.innerHTML = `<div class="k">${kicker}</div><h1>${head}</h1><p>${sub}</p>`;
    document.body.appendChild(t); await sleep(ms); t.style.opacity = 0; await sleep(650); t.remove();
  };

  // ---- 台本 ----
  await title('PLAY MOVIE', 'LeXWeft <span>Lite</span>', 'フォルダを登録すると、資料の地図ができる', 3200);

  // 1. フォルダを登録して取り込む
  await caption('① 資料の入ったフォルダを登録します', 'テーマごとのメモや報告書が 48 件入ったフォルダ（架空の資料）');
  await sleep(1400);
  await click('#pickdir', 0.5, 0.5, true);
  // フォルダ選択の窓 (動画用の見本)
  const dlg = Object.assign(document.createElement('div'), {id: 'v-dlg'});
  dlg.innerHTML = '<div class="h">取り込むフォルダを選ぶ</div><div class="r">📁 仕事</div><div class="r" id="v-pick">📁 調べ物</div><div class="r">📁 写真</div><div class="f"><button id="v-ok">このフォルダを選ぶ</button></div>';
  document.body.appendChild(dlg); await sleep(500);
  await click('#v-pick'); $('#v-pick').classList.add('sel'); await sleep(400);
  await click('#v-ok'); dlg.remove();
  await prepare(DEMO);
  await caption('取り込む前に、何件あるかを確かめられます', '隠しフォルダや開発用のフォルダは読みません');
  await sleep(1800);
  await click('#scango');
  await caption('② 取り込むと、意味層が自動でできます', '資料をベクトルにして、似た資料をまとまりに分け、名前を付けます');
  await waitFor(() => S.job && S.job.state === 'done', 30000);
  await sleep(800);
  await refreshOverview(); renderHome();
  // 意味層ができるまで待つ
  for (let i = 0; i < 80; i++) { await refreshOverview(); if (scopes().some(x => x.scope !== '*' && x.built_at && !x.building)) break; await sleep(500); }
  renderHome(); await sleep(1600);

  // 2. 地図
  await caption('③ 地図: 点が資料、色がまとまりです', '中身が似ている資料ほど近くに集まります');
  await click('#tabs button[data-tab=map]');
  await waitFor(() => document.querySelectorAll('#graph circle').length > 10);
  await sleep(2200);
  await caption('まとまりを押すと、何の集まりかが分かります', '特によく出る言葉・代表的な段落・中心に近い資料');
  await click('[data-cl="0"]'); await sleep(3200);
  await click('[data-cl="2"]'); await sleep(2800);
  await click('[data-cl="1"]'); await sleep(2400);

  // 3. つながり
  await caption('つながり: よく一緒に出てくる言葉を線で結んだ図', '色は、その言葉がいちばん多く出るまとまり');
  await click('#tabs button[data-tab=graph]');
  await waitFor(() => document.querySelectorAll('#graph circle').length > 10);
  await sleep(3000);
  await caption('テーマの言葉で絞り込めます');
  const gq = await type('#gq', '熱暴走 | 冷却板');
  gq.dispatchEvent(new Event('change')); await sleep(3200);

  // 4. 資料と似た資料
  await caption('資料を開くと、中身が似ている資料が並びます', '本文には段落番号（¶）が付きます');
  await click('#tabs button[data-tab=docs]'); await sleep(900);
  const dq = await type('#docq', '電池の熱対策');
  dq.dispatchEvent(new Event('change'));
  const row = await waitFor(() => [...document.querySelectorAll('[data-doc]')].find(tr => tr.textContent.includes('電池の熱対策メモ 02')));
  await click(row); window.scrollTo({top: 0}); await waitFor(() => $('#similar [data-opendoc]')); await sleep(800);
  await moveTo('#similar'); await sleep(2600);

  // 5. 検索
  await caption('検索: 言い換えを | で並べて、まとめて探せます');
  await click('#tabs button[data-tab=search]');
  await type('#q', '熱伝播 | 延焼');
  await click('#go'); await sleep(3000);

  // 6. Claude で深める (Claude が書き込む様子の再現)
  await caption('Claude とつなぐと、課題と解決手段を根拠つきで書けます', '例: 「まとまり『セル・相変化材料』の課題と解決手段を書いて」');
  await click('#tabs button[data-tab=connect]'); await sleep(2600);
  const pid = async q => (await api('/api/search?' + new URLSearchParams({q}))).paragraphs.slice(0, 2).map(p => p.paragraph_id);
  const write = async (name, type, q) => post('/api/concepts', {name, type, paragraph_ids: await pid(q)});
  await click('#tabs button[data-tab=graph]');
  S.gopts.mode = 'layer'; S.gopts.documents = false; await loaders.graph(); await sleep(800);
  await caption('（Claude が書き込んでいくところ）', '課題と解決手段を、根拠の段落番号といっしょに保存します');
  const steps = [['セル間の熱伝播', '課題', '熱伝播を抑え'], ['相変化材料シート', '解決手段', '相変化材料のシート'], ['断熱材との複合シート', '解決手段', '複合シート'],
                 ['セル温度のばらつき', '課題', 'セル温度がばらつき'], ['対向流の並列流路', '解決手段', '対向流の並列流路'], ['出口側流路の狭幅化', '解決手段', '出口側の流路幅']];
  for (const [n, t, q] of steps) { await write(n, t, q); await refreshOverview(); await loaders.graph(); await sleep(700); }
  for (const [a, b] of [['相変化材料シート', 'セル間の熱伝播'], ['断熱材との複合シート', 'セル間の熱伝播'], ['対向流の並列流路', 'セル温度のばらつき'], ['出口側流路の狭幅化', 'セル温度のばらつき']]) {
    await post('/api/relations', {source: a, target: b, kind: '解決する'}); await loaders.graph(); await sleep(700);
  }
  await sleep(2600);

  // おわり
  cap.style.opacity = 0;
  await title('LeXWeft Lite', '溜めた資料が、地図になる', '資料はこの PC の中に。API キーは不要です。', 3800);
  return 'done';
})();
