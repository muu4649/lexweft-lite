// LeXWeft Lite のプレイ動画の台本. 画面の上に字幕・カーソル・タイトルを重ね、操作を順に再生する.
(async () => {
  const DEMO = window.__DEMO_PATH__;
  const MASK = [[DEMO.replace(/\/調べ物$/, '/home'), '~/LeXWeftLite'], [DEMO.replace(/\/調べ物$/, '/'), '~/Documents/'], ['video_pub/調べ物', 'Documents/調べ物'], ['video2/調べ物', 'Documents/調べ物'], ['video/調べ物', 'Documents/調べ物'],
                [/\/Users\/[^\/\s"]+\/[^\s"]*?lexweft-lite/g, '~/Documents/LeXWeftLite'], [/\/Users\/[^\/\s"]+/g, '~']];
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
    const rep = v => { for (const [a, b] of MASK) v = typeof a === 'string' ? v.split(a).join(b) : v.replace(a, b); return v; };
    while ((n = w.nextNode())) { const o = n.nodeValue, v = rep(o); if (v !== o) n.nodeValue = v; }
    document.querySelectorAll('input').forEach(i => { const v = rep(i.value); if (v !== i.value) i.value = v; });
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
  await title('PLAY MOVIE', 'LeXWeft <span>Lite</span>', '資料を入れるフォルダを決めると、資料の地図ができる', 3200);

  // 1. 資料を入れるフォルダを決める → 資料を入れる → 意味層ができる
  await caption('① まず、資料を入れるフォルダを決めます', '決めたフォルダに入れた資料は、自動で取り込まれます（ここでは架空の資料 60 件が入ったフォルダ）');
  await sleep(1800);
  {
    const inp = await moveTo('#inpath');
    const shown = '~/Documents/調べ物';
    inp.value = ''; for (const ch of shown) { inp.value += ch; await sleep(55); }
    await sleep(600);
    await click('#inset', 0.5, 0.5, true);
    inp.value = DEMO; $('#inset').click();   // 押す直前に本物の場所へ (画面のパスは伏せたまま見せる)
  }
  await waitFor(() => S.inbox && S.inbox.path, 20000);
  await caption('フォルダの中のファイルを、そのまま取り込みます', 'サブフォルダは、そのまま「グループ」になります');
  await waitFor(() => S.job && S.job.state === 'done', 300000).catch(() => null);
  for (let i = 0; i < 300; i++) { await refreshOverview(); if (scopes().some(x => x.scope !== 'central' && x.built_at && !x.building && !x.stale)) break; await sleep(500); }
  await loaders.home(); await sleep(1600);
  await caption('② 資料を足すときは、画面にファイルを落とすだけ', 'Finder でフォルダに入れても、自動で取り込みます');
  if ($('#ingroup')) { await moveTo('#ingroup'); $('#ingroup').value = '自社のメモ'; S.inGroup = '自社のメモ'; await sleep(900); }
  {
    const drop = await moveTo('#drop');
    drop.classList.add('over'); await sleep(1300); drop.classList.remove('over');
    await upload([new File(['# 電池の熱対策の打ち合わせメモ（架空）\n\nセルの熱暴走を、相変化材料のシートと断熱材を重ねて抑える案を話し合った。'], '電池の熱対策の打ち合わせメモ.md', {type: 'text/markdown'})]);
  }
  await sleep(1200);
  await waitFor(() => !S.job || S.job.state !== 'running', 120000).catch(() => null);
  for (let i = 0; i < 120; i++) { await refreshOverview(); if (!scopes().some(x => x.building || x.stale)) break; await sleep(500); }
  await loaders.home(); await sleep(1200);
  await caption('③ 意味層は自動でできます', '資料をベクトルにして、似たものをまとまりに分け、名前を付けます');
  await moveTo('#layerbox'); await sleep(3000);
  await caption('すでに資料が入っているフォルダからも作れます（追加の機能）', 'ファイルは動かさずに読みます');
  { const ex = await waitFor(() => $('#pickdir')); ex.scrollIntoView({behavior: 'smooth', block: 'center'}); await sleep(900); await moveTo(ex); await sleep(2600); window.scrollTo({top: 0, behavior: 'smooth'}); await sleep(700); }

  // 2. 地図
  await caption('④ 地図: 点が資料、色がまとまりです', '中身が似ている資料ほど近くに集まります');
  await click('#tabs button[data-tab=map]');
  await waitFor(() => document.querySelectorAll('#graph circle').length > 10);
  await sleep(2200);
  await caption('まとまりを押すと、何の集まりかが分かります', '特によく出る言葉・代表的な段落・中心に近い資料');
  await click('[data-cl="0"]'); await sleep(3200);
  await click('[data-cl="2"]'); await sleep(2800);
  await click('[data-cl="1"]'); await sleep(2400);
  if ($('#gclose')) { await click('#gclose'); await sleep(700); }

  // 2b. グループ (サブフォルダ)
  await caption('サブフォルダは、そのまま「グループ」になります', '色を「グループ」に切り替えると、自社のメモと他社の資料がどこで重なっているかが見えます');
  await click('input[name=mcolor][value=group]'); await sleep(3600);
  await caption('グループのつながり図', '左 = 自社のメモのまとまり、右 = 他社の資料のまとまり。中身が近い資料の組が多いほど太い線');
  await click('#tabs button[data-tab=groups]');
  await waitFor(() => document.querySelectorAll('#bip path').length > 0);
  await sleep(3000);
  {
    const p = S.gpaths[0], r = p.getBoundingClientRect();
    cx = r.left + r.width / 2; cy = r.top + r.height / 2; cur.style.transform = `translate(${cx - 4}px,${cy - 2}px)`; await sleep(800);
    p.dispatchEvent(new MouseEvent('click', {bubbles: true}));
  }
  await caption('線を押すと、近い資料の組と共通の言葉が出ます', '同じテーマを、自社と他社がそれぞれどう書いているかを並べて読めます');
  await sleep(2400); await moveTo('#gside .pair'); await sleep(3200);

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
  const row = await waitFor(() => [...document.querySelectorAll('[data-doc]')].find(tr => tr.textContent.includes('電池の熱対策の検討メモ 02')));
  await click(row); window.scrollTo({top: 0}); await waitFor(() => $('#similar [data-opendoc]')); await sleep(800);
  await moveTo('#similar'); await sleep(2600);

  // 5. 検索
  await caption('検索: 文字で探すと、同じ言葉が入った資料だけが見つかります', '「熱暴走」で探すと、同じ話題でも「延焼」「類焼」と書いた資料は出てきません');
  await click('#tabs button[data-tab=search]'); await sleep(600);
  await click('#m-text'); await sleep(400);
  await type('#q', '熱暴走');
  await click('#go'); await sleep(3200);
  await caption('意味層でたどると、言い方が違う関連資料にも届きます', '質問は文で OK。関係するまとまり → 資料 → 段落の順に案内します');
  await click('#m-route'); await sleep(500);
  await type('#q', '熱暴走を防ぐ方法');
  await click('#go'); await sleep(2600);
  await moveTo('.why-sem'); await sleep(1600);
  await caption('「あわせて確かめたい資料」で、読み残しも見えます', '関係が強いのに、一覧に出ていない資料を理由つきで示します');
  const box = await waitFor(() => [...document.querySelectorAll('.panel h3')].find(h => h.textContent.includes('あわせて確かめたい')));
  box.scrollIntoView({behavior: 'smooth', block: 'start'}); await sleep(1200);
  await moveTo(box); await sleep(3600);
  window.scrollTo({top: 0, behavior: 'smooth'}); await sleep(600);

  // 6. Claude で深める (Claude が書き込む様子の再現)
  await caption('⑤ Claude とつなぐ: セットアップの最後に y を押すだけ', 'あとは Claude Desktop を開き直します（Mac は ⌘Q で終了してから開く）');
  await click('#tabs button[data-tab=connect]'); await sleep(1200);
  const askLi = await waitFor(() => [...document.querySelectorAll('ol.steps li')].find(li => li.textContent.includes('何件')));
  await moveTo(askLi, 0.3, 0.5); await sleep(1800);
  await caption('つながったかは、Claude に「資料は何件？」と聞くだけで分かります', '件数が返ってくれば OK。「使ってよいか」と聞かれたら「許可」');
  await sleep(3400);
  const askPre = await waitFor(() => [...document.querySelectorAll('pre.code')].find(p => p.textContent.includes('読み残し')));
  askPre.scrollIntoView({behavior: 'smooth', block: 'center'}); await sleep(900);
  await moveTo(askPre, 0.4, 0.5);
  await caption('調べたいことは、チャットでふつうに頼むだけ', 'Claude が意味層をたどり、答える前に読み残しを確かめ、根拠の段落番号つきで答えます');
  await sleep(4200);
  window.scrollTo({top: 0, behavior: 'smooth'}); await sleep(500);
  await caption('Claude は、課題と解決手段を根拠つきで書き込むこともできます');
  await sleep(1200);
  const pid = async q => (await api('/api/search?' + new URLSearchParams({q}))).paragraphs.slice(0, 2).map(p => p.paragraph_id);
  const write = async (name, type, q) => post('/api/concepts', {name, type, paragraph_ids: await pid(q)});
  await click('#tabs button[data-tab=graph]');
  S.gopts.mode = 'layer'; S.gopts.documents = false; await loaders.graph(); await sleep(800);
  await caption('（Claude が書き込んでいくところ）', '課題と解決手段を、根拠の段落番号といっしょに保存します');
  const steps = [['セルの熱暴走の広がり', '課題', '熱暴走を抑えたい'], ['相変化材料による吸熱', '解決手段', '相変化材料を'], ['断熱材でセル間を遮る', '解決手段', '断熱材を'],
                 ['冷却板の温度ばらつき', '課題', '温度ばらつきを小さく'], ['対向流の並列流路', '解決手段', '対向流の並列'], ['ピンフィンで熱を伝える', '解決手段', 'ピンフィンを並べ']];
  for (const [n, t, q] of steps) { await write(n, t, q); await refreshOverview(); await loaders.graph(); await sleep(700); }
  for (const [a, b] of [['相変化材料による吸熱', 'セルの熱暴走の広がり'], ['断熱材でセル間を遮る', 'セルの熱暴走の広がり'], ['対向流の並列流路', '冷却板の温度ばらつき'], ['ピンフィンで熱を伝える', '冷却板の温度ばらつき']]) {
    await post('/api/relations', {source: a, target: b, kind: '解決する'}); await loaders.graph(); await sleep(700);
  }
  await sleep(2600);

  // おわり
  cap.style.opacity = 0;
  await title('LeXWeft Lite', '溜めた資料が、地図になる', '資料はこの PC の中に。API キーは不要です。', 3800);
  return 'done';
})();
