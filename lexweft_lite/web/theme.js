'use strict';
// 画面を描く前に、選んだ表示 (ライト/ダーク) を当てる (ちらつかないように、本体より先に読む)
try { const t = localStorage.getItem('lw-theme'); if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t; } catch (e) { /* 保存できなくても動く */ }
