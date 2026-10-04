// LeXWeft Lite の説明資料 2 本を作る (テスター向け / 紹介・募集向け).
//   NODE_PATH=$(npm root -g) node marketing/build_decks.js
// 画面写真は marketing/img/ (架空の資料 48 件で撮ったもの)。
const path = require("path");
const pptxgen = require("pptxgenjs");
const { applyTheme } = require(process.env.PPTX_SKILL + "/scripts/apply_theme.js");

const IMG = path.join(__dirname, "img");
const OUT = path.join(__dirname);
const THEME = {
  name: "LeXWeft Lite",
  headFontFace: "Yu Gothic",
  bodyFontFace: "Yu Gothic",
  colors: {
    dk1: "1F2328", lt1: "FFFFFF", dk2: "0F1A1E", lt2: "F3F5F4",
    accent1: "0E7490", accent2: "C2410C", accent3: "7C3AED", accent4: "15803D", accent5: "6B7280", accent6: "E7F1F3",
    hlink: "0E7490", folHlink: "0E7490",
  },
};
const W = 13.333, H = 7.5;
const SHOT = { w: 2880, h: 1800 };   // 画面写真の画素数 (16:10)

function makeDeck(title) {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";
  pres.title = title;
  pres.author = "LeXI/Vent";
  pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
  const C = pres.SchemeColor;
  pres.defineSlideMaster({
    title: "COVER", background: { color: THEME.colors.dk2 },
    objects: [
      { placeholder: { options: { name: "kicker", type: "body", x: 0.7, y: 1.5, w: 5.6, h: 0.5, fontSize: 16, color: "7DD3E4", bold: true, margin: 0 }, text: "" } },
      { placeholder: { options: { name: "title", type: "title", x: 0.7, y: 2.1, w: 5.8, h: 1.9, fontSize: 40, bold: true, color: C.background1, valign: "top", align: "left", margin: 0 }, text: "" } },
      { placeholder: { options: { name: "body", type: "body", x: 0.7, y: 4.15, w: 5.6, h: 1.4, fontSize: 20, color: "D6E4E8", valign: "top", margin: 0 }, text: "" } },
    ],
  });
  pres.defineSlideMaster({
    title: "CONTENT", background: { color: "FFFFFF" },
    margin: [0.5, 0.6, 0.6, 0.6],
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.4, w: 12.1, h: 0.95, fontSize: 28, bold: true, color: C.text1, valign: "middle", align: "left", margin: 0 }, text: "" } },
      { text: { text: "LeXWeft Lite", options: { x: 0.6, y: 7.0, w: 3, h: 0.3, fontSize: 10, color: "6B7280", margin: 0 } } },
    ],
    slideNumber: { x: 12.3, y: 7.0, w: 0.4, h: 0.3, fontSize: 10, color: "6B7280", align: "right" },
  });
  pres.defineSlideMaster({
    title: "CLOSING", background: { color: THEME.colors.dk2 },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: 0.7, y: 0.6, w: 11.9, h: 1.0, fontSize: 32, bold: true, color: C.background1, align: "left", margin: 0 }, text: "" } },
    ],
  });
  return pres;
}

// 画面写真: 枠と影を付けて、縦横比を保って置く
function shot(slide, name, x, y, w, alt) {
  const h = w * SHOT.h / SHOT.w;
  slide.addShape("rect", { x: x - 0.02, y: y - 0.02, w: w + 0.04, h: h + 0.04, fill: { color: "FFFFFF" }, line: { color: "D7DCDF", width: 0.75 },
    shadow: { type: "outer", color: "000000", opacity: 0.12, blur: 8, offset: 2, angle: 90 }, objectName: "shot-frame" });
  slide.addImage({ path: path.join(IMG, name), x, y, w, h, altText: alt, objectName: "shot" });
  return h;
}

// 番号の丸と見出し・説明 (アプリの手順の丸と同じ形)
function step(slide, n, x, y, w, head, body, color = "0E7490") {
  slide.addShape("ellipse", { x, y, w: 0.46, h: 0.46, fill: { color }, line: { color }, objectName: `step${n}` });
  slide.addText(String(n), { x, y, w: 0.46, h: 0.46, fontSize: 16, bold: true, color: "FFFFFF", align: "center", valign: "middle", margin: 0, isTextBox: true });
  slide.addText(head, { x: x + 0.62, y: y - 0.04, w: w - 0.62, h: 0.42, fontSize: 18, bold: true, color: "1F2328", margin: 0, valign: "middle", isTextBox: true });
  if (body) slide.addText(body, { x: x + 0.62, y: y + 0.4, w: w - 0.62, h: 0.8, fontSize: 14, color: "4B5563", margin: 0, valign: "top", isTextBox: true });
}

function points(slide, items, x, y, w, h, size = 16) {
  slide.addText(items.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < items.length - 1, paraSpaceAfter: 10 } })),
    { x, y, w, h, fontSize: size, color: "1F2328", valign: "top", margin: 0, isTextBox: true });
}

function card(slide, x, y, w, h, head, body, accent = "0E7490", size = 14) {
  slide.addShape("roundRect", { x, y, w, h, rectRadius: 0.12, fill: { color: "F3F5F4" }, line: { color: "F3F5F4" }, objectName: "card" });
  slide.addShape("ellipse", { x: x + 0.3, y: y + 0.32, w: 0.22, h: 0.22, fill: { color: accent }, line: { color: accent }, objectName: "card-dot" });
  const long = head.length * 0.25 > w - 0.9;   // 18pt で 1 行に収まらない見出しは小さくする
  slide.addText(head, { x: x + 0.65, y: y + 0.2, w: w - 0.9, h: 0.5, fontSize: long ? 15 : 18, bold: true, color: "1F2328", margin: 0, valign: "middle", fit: "shrink", isTextBox: true });
  slide.addText(body, { x: x + 0.3, y: y + 0.85, w: w - 0.6, h: h - 1.05, fontSize: size, color: "374151", margin: 0, valign: "top", lineSpacingMultiple: 1.3, isTextBox: true });
}

function promptBox(slide, x, y, w, h, text, label = "Claude への頼み方の例") {
  slide.addShape("roundRect", { x, y, w, h, rectRadius: 0.1, fill: { color: "E7F1F3" }, line: { color: "E7F1F3" }, objectName: "prompt" });
  slide.addText([{ text: label, options: { fontSize: 11, color: "0E7490", bold: true, breakLine: true } }, { text, options: { fontSize: 14, color: "1F2328" } }],
    { x: x + 0.25, y: y + 0.15, w: w - 0.5, h: h - 0.3, valign: "top", margin: 0, isTextBox: true });
}

function table(slide, rows, x, y, w, colW, fontSize = 14, rowH = 0.7) {
  const head = rows[0].map(t => ({ text: t, options: { bold: true, color: "FFFFFF", fill: { color: "0E7490" } } }));
  const body = rows.slice(1).map((r, i) => r.map(t => ({ text: t, options: { color: "1F2328", fill: { color: i % 2 ? "FFFFFF" : "F3F5F4" } } })));
  slide.addTable([head, ...body], { x, y, w, colW, rowH, fontSize, border: { type: "solid", color: "E5E7EB", pt: 0.75 }, valign: "middle", margin: 0.12 });
}

// ---------------- テスター向け ----------------
async function testerDeck() {
  const pres = makeDeck("LeXWeft Lite テスト版のご案内");
  pres.addSection({ title: "はじめに" });
  let s = pres.addSlide({ masterName: "COVER", sectionTitle: "はじめに" });
  s.addText("テスト版のご案内", { placeholder: "kicker" });
  s.addText("LeXWeft Lite", { placeholder: "title" });
  s.addText("フォルダを登録すると、資料の地図ができる", { placeholder: "body" });
  shot(s, "d_map_dark.png", 6.6, 1.55, 6.1, "資料のまとまりを色分けした地図の画面");
  s.addNotes("LeXWeft Lite は、フォルダを登録するだけで、中の資料を似たものどうしのまとまりに分け、地図にして見せるアプリです。データはこの PC の中に保存します。");

  s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "はじめに" });
  s.addText("フォルダを登録するだけで、資料の地図ができる", { placeholder: "title" });
  step(s, 1, 0.6, 1.75, 4.4, "フォルダを登録する", "資料の入ったフォルダを選ぶ。md / txt / html / pdf / docx / csv を読む");
  step(s, 2, 0.6, 3.25, 4.4, "取り込む", "件数を確かめてから取り込む。資料を足したら「取り込み直す」");
  step(s, 3, 0.6, 4.75, 4.4, "意味層ができる", "フォルダごとに、似た資料のまとまりと、その説明が自動でできる");
  shot(s, "d_home.png", 5.35, 1.65, 7.35, "取り込みタブの 3 つの手順の画面");

  pres.addSection({ title: "見る" });
  s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "見る" });
  s.addText("地図で、資料がどんなまとまりに分かれるかが見える", { placeholder: "title" });
  shot(s, "d_map.png", 0.6, 1.6, 7.9, "地図の画面。左にまとまりの一覧、右に選んだまとまりの詳細");
  points(s, ["点が資料。近い点ほど中身が似ている", "色がまとまり。名前はそのまとまりに特に多く出る語", "まとまりを押すと、代表的な段落と中心に近い資料が出る", "点を押すと、その資料が開く"], 8.9, 1.75, 3.8, 4.8);

  s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "見る" });
  s.addText("語のつながりで、テーマの広がりが見える", { placeholder: "title" });
  points(s, ["よく出る語を、同じ資料に一緒に出るものどうしで結ぶ", "色は、その語がいちばん多く出るまとまり", "テーマの言葉で絞り込める（例: 熱暴走 | 冷却）", "語を押すと、その語が出る資料が並ぶ"], 0.6, 1.75, 3.8, 4.8);
  shot(s, "d_graph.png", 4.8, 1.6, 7.9, "キーワードのつながりの画面");

  s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "見る" });
  s.addText("資料を開くと、似た資料と段落番号つきの本文が出る", { placeholder: "title" });
  shot(s, "d_doc.png", 0.6, 1.6, 7.9, "資料の画面。似た資料と、段落番号つきの本文");
  points(s, ["似た資料: 同じフォルダの中で、中身のベクトルが近い順", "段落番号（¶）は、Claude が根拠を示すときの番号", "段落を選んで、課題や解決手段を手で書き足せる", "「検索」タブでは、言い換えを | で並べて段落を探せる"], 8.9, 1.75, 3.8, 4.8);

  pres.addSection({ title: "深める" });
  s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "深める" });
  s.addText("Claude とつなぐと、課題と解決手段を根拠つきで書ける", { placeholder: "title" });
  step(s, 1, 0.6, 1.75, 5.2, "セットアップの最後で y を押す", "Claude Desktop に LeXWeft Lite が登録される");
  step(s, 2, 0.6, 2.95, 5.2, "Claude Desktop を開き直す", "道具の一覧に lexweft-lite が出れば接続できている");
  step(s, 3, 0.6, 4.15, 5.2, "まとまりを選んで頼む", null);
  promptBox(s, 1.22, 4.7, 4.6, 1.5, "LeXWeft Lite のまとまり「セル・相変化材料・異常発熱」の資料を読んで、課題と解決手段を根拠の段落つきで書いて。");
  shot(s, "d_layer.png", 6.2, 1.6, 6.5, "課題と解決手段のつながりの画面");
  s.addText("アプリ自身は LLM を呼ばないので、API キーは要りません。", { x: 6.2, y: 5.85, w: 6.5, h: 0.4, fontSize: 12, color: "4B5563", margin: 0, isTextBox: true });

  pres.addSection({ title: "入れ方とお願い" });
  s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "入れ方とお願い" });
  s.addText("入れ方は 5 分、Mac と Windows で同じ流れ", { placeholder: "title" });
  s.addText("Mac", { x: 0.6, y: 1.6, w: 5.8, h: 0.5, fontSize: 20, bold: true, color: "0E7490", margin: 0, isTextBox: true });
  step(s, 1, 0.6, 2.25, 5.8, "ZIP を展開し、フォルダを「書類」へ移す", null);
  step(s, 2, 0.6, 3.05, 5.8, "ターミナルで bash と打ち、install.sh をドラッグ", null);
  step(s, 3, 0.6, 3.85, 5.8, "最後の質問に y（Claude とつなぐ）", null);
  step(s, 4, 0.6, 4.65, 5.8, "Launchpad で「LeXWeft Lite」を開く", null);
  s.addText("Windows", { x: 6.9, y: 1.6, w: 5.8, h: 0.5, fontSize: 20, bold: true, color: "0E7490", margin: 0, isTextBox: true });
  step(s, 1, 6.9, 2.25, 5.8, "ZIP を「すべて展開」し「ドキュメント」へ", null);
  step(s, 2, 6.9, 3.05, 5.8, "フォルダのアドレス欄で powershell と入力", null);
  step(s, 3, 6.9, 3.85, 5.8, "install.ps1 を実行（コマンドは手順書に）", null);
  step(s, 4, 6.9, 4.65, 5.8, "「LeXWeft Lite.bat」をダブルクリック", null);
  s.addText("詳しい手順と困ったときの対処は、ZIP の中の START_HERE.html にあります。", { x: 0.6, y: 5.75, w: 12.1, h: 0.5, fontSize: 14, color: "4B5563", margin: 0, isTextBox: true });

  s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "入れ方とお願い" });
  s.addText("資料はこの PC の中に保存し、アプリからは外に送らない", { placeholder: "title" });
  table(s, [["何が", "どこに・どうなる"],
            ["取り込んだ資料と意味層", "ホームフォルダの LeXWeftLite に保存する（アプリのフォルダとは別）"],
            ["元のファイル", "読むだけで、変えたり消したりしない"],
            ["アプリからの送信", "しない。画面との通信もこの PC の中だけ"],
            ["Claude に読ませた部分", "Claude（Anthropic）に送られる。社外秘の資料は所属先の決まりを確かめてから"]],
        0.6, 1.9, 12.1, [3.6, 8.5], 17, 0.85);

  s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "入れ方とお願い" });
  s.addText("2 週間使って、感想を聞かせてください", { placeholder: "title" });
  card(s, 0.6, 1.7, 5.9, 4.6, "試してほしいこと", "1. テーマごとのフォルダを 1〜3 個登録する\n2. 地図のまとまりの名前が、中身と合っているか見る\n3. 似た資料やつながりから、忘れていた資料が見つかるか\n4. Claude で 1 つのまとまりの課題と解決手段を書かせる", "0E7490", 17);
  card(s, 6.8, 1.7, 5.9, 4.6, "教えてほしいこと", "・役に立った場面と、役に立たなかった場面\n・分かりにくかった画面や言葉\n・続けて使うなら、いくらまでなら払うか\n・困ったこと、ほしい機能\n\n画面右上の「感想を送る」から送れます", "C2410C", 17);

  s = pres.addSlide({ masterName: "CLOSING", sectionTitle: "入れ方とお願い" });
  s.addText("困ったときは", { placeholder: "title" });
  s.addTable([
    [{ text: "こんなとき", options: { bold: true, color: "7DD3E4" } }, { text: "こうしてください", options: { bold: true, color: "7DD3E4" } }],
    ["Claude に lexweft-lite が出ない", "Claude Desktop を完全に終了して開き直す。出なければセットアップをもう一度実行して最後に y"],
    ["地図が「作っています」のまま", "資料が多いと数分かかる。終わると自動で出る"],
    ["まとまりの名前がしっくりこない", "テーマごとにフォルダを分けて登録すると、まとまりがはっきりする"],
    ["PDF の本文が出ない", "スキャン画像の PDF は文字を取り出せない。文字を選択できる PDF を使う"],
  ].map((r, i) => i === 0 ? r : r.map(t => ({ text: t, options: { color: "E6EEF0" } }))),
  { x: 0.7, y: 1.9, w: 11.9, colW: [4.2, 7.7], rowH: 0.75, fontSize: 16, border: { type: "solid", color: "2B3A40", pt: 0.75 }, margin: 0.12, valign: "middle" });
  s.addText("利用条件は ZIP の中の TERMS.txt をご覧ください。ご協力ありがとうございます。", { x: 0.7, y: 6.3, w: 11.9, h: 0.5, fontSize: 14, color: "B8C7CC", margin: 0, isTextBox: true });

  const file = path.join(OUT, "LeXWeftLite_テスト版のご案内.pptx");
  await pres.writeFile({ fileName: file });
  await applyTheme(file, THEME);
  return file;
}

// ---------------- 紹介・募集向け ----------------
async function introDeck() {
  const pres = makeDeck("溜めた資料が、地図になる - LeXWeft Lite");
  pres.addSection({ title: "課題" });
  let s = pres.addSlide({ masterName: "COVER", sectionTitle: "課題" });
  s.addText("LeXWeft Lite テスト参加者募集", { placeholder: "kicker" });
  s.addText("溜めた資料が、地図になる", { placeholder: "title" });
  s.addText("フォルダを登録するだけで、技術資料を中身ごとに整理し、手元の Claude で深掘りできる", { placeholder: "body" });
  shot(s, "d_map_dark.png", 6.6, 1.55, 6.1, "資料のまとまりを色分けした地図の画面");

  s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "課題" });
  s.addText("資料は溜まるほど、見返せなくなる", { placeholder: "title" });
  card(s, 0.6, 1.8, 3.85, 2.9, "どこに何があるか分からない", "フォルダ名とファイル名だけでは、中身の重なりや抜けが見えない。", "C2410C", 17);
  card(s, 4.74, 1.8, 3.85, 2.9, "似た資料にたどり着けない", "言い回しや表記が違うと、キーワード検索に掛からない。", "C2410C", 17);
  card(s, 8.88, 1.8, 3.85, 2.9, "生成 AI に渡すのが不安", "調べ物の資料を、まとめてクラウドに上げるのはためらわれる。", "C2410C", 17);
  s.addText("LeXWeft Lite は、この 3 つを手元のフォルダから解きます。", { x: 0.6, y: 5.25, w: 12.1, h: 0.6, fontSize: 20, bold: true, color: "0E7490", margin: 0, isTextBox: true });

  pres.addSection({ title: "解決" });
  s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "解決" });
  s.addText("フォルダを登録するだけで、中身ごとに整理される", { placeholder: "title" });
  step(s, 1, 0.6, 1.9, 3.9, "フォルダを登録", "md / txt / html / pdf / docx / csv を読む");
  step(s, 2, 4.72, 1.9, 3.9, "自動でまとまりに分ける", "中身をベクトルにして、似た資料をまとめる");
  step(s, 3, 8.84, 1.9, 3.9, "まとまりに名前が付く", "そのまとまりに特に多く出る語で、何の集まりかが分かる");
  shot(s, "d_home.png", 3.87, 3.25, 5.6, "取り込みタブの画面");

  s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "解決" });
  s.addText("まとまりと説明が自動でできるので、全体像が一目でつかめる", { placeholder: "title" });
  shot(s, "d_map.png", 0.6, 1.6, 7.9, "地図の画面");
  points(s, ["4 つのテーマの資料 48 件が、テーマどおり 4 つにまとまった例（架空の資料）", "まとまりを押すと、代表的な段落と中心に近い資料", "資料を開くと、同じフォルダの似た資料"], 8.9, 1.75, 3.8, 4.8);

  s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "解決" });
  s.addText("いつもの Claude から、根拠の段落つきで深掘りできる", { placeholder: "title" });
  points(s, ["MCP で Claude Desktop とつなぐ", "まとまりを手がかりに、課題と解決手段を書かせる", "どの段落に書いてあるかを番号で残す", "アプリ自身は LLM を呼ばない。API キーは要らない"], 0.6, 1.75, 5.2, 3.0);
  promptBox(s, 0.6, 4.6, 5.2, 1.5, "LeXWeft Lite で「セル間の熱伝播」を解決する手段を、根拠の段落番号つきで一覧にして。");
  shot(s, "d_layer.png", 6.2, 1.6, 6.5, "課題と解決手段のつながりの画面");

  s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "解決" });
  s.addText("手元で動き、アプリから資料を外に送らない", { placeholder: "title" });
  card(s, 0.6, 1.75, 5.9, 2.15, "この PC に保存", "資料も意味層も、ホームフォルダの中だけに置く");
  card(s, 6.8, 1.75, 5.9, 2.15, "API キー不要", "計算はこの PC の中で行う。LLM はいま使っているものをつなぐ");
  card(s, 0.6, 4.15, 5.9, 2.15, "根拠の段落つき", "まとまりも Claude の答えも、元の段落に戻って確かめられる");
  card(s, 6.8, 4.15, 5.9, 2.15, "Mac と Windows", "ZIP を展開してセットアップを 1 回実行するだけ");
  s.addText("Claude に読ませた部分は、Claude（Anthropic）に送られます。", { x: 0.6, y: 6.45, w: 12.1, h: 0.35, fontSize: 11, color: "6B7280", margin: 0, isTextBox: true });

  pres.addSection({ title: "募集" });
  s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "募集" });
  s.addText("知財・技術調査で、資料を多く抱える方に向いている", { placeholder: "title" });
  card(s, 0.6, 1.8, 3.85, 4.2, "知財・特許調査", "先行技術調査や動向調査のメモ・報告書を、テーマごとに見渡す", "0E7490", 17);
  card(s, 4.74, 1.8, 3.85, 4.2, "研究開発の技術調査", "論文メモや実験報告から、似た取り組みや抜けているテーマを探す", "0E7490", 17);
  card(s, 8.88, 1.8, 3.85, 4.2, "調査レポートを書く人", "過去のレポートを根拠の段落つきで引き直し、次の調査に使う", "0E7490", 17);

  s = pres.addSlide({ masterName: "CLOSING", sectionTitle: "募集" });
  s.addText("テストに参加してください", { placeholder: "title" });
  const col = (x, n, head, body) => {
    s.addShape("ellipse", { x, y: 2.1, w: 0.6, h: 0.6, fill: { color: "0E7490" }, line: { color: "0E7490" }, objectName: `join${n}` });
    s.addText(String(n), { x, y: 2.1, w: 0.6, h: 0.6, fontSize: 20, bold: true, color: "FFFFFF", align: "center", valign: "middle", margin: 0, isTextBox: true });
    s.addText(head, { x, y: 2.9, w: 3.7, h: 0.5, fontSize: 20, bold: true, color: "FFFFFF", margin: 0, isTextBox: true });
    s.addText(body, { x, y: 3.45, w: 3.7, h: 1.4, fontSize: 15, color: "D6E4E8", margin: 0, valign: "top", isTextBox: true });
  };
  col(0.7, 1, "登録フォームに記入", "お名前・連絡先・使っている OS");
  col(4.75, 2, "ZIP と入れ方が届く", "5 分ほどでセットアップできます");
  col(8.8, 3, "2 週間使って感想を", "短いアンケートと、希望者にはお話を伺います");
  s.addText("登録フォーム: （フォームの URL を入れる）", { x: 0.7, y: 5.6, w: 11.9, h: 0.6, fontSize: 20, bold: true, color: "7DD3E4", margin: 0, isTextBox: true });

  const file = path.join(OUT, "LeXWeftLite_紹介と参加募集.pptx");
  await pres.writeFile({ fileName: file });
  await applyTheme(file, THEME);
  return file;
}

(async () => {
  console.log(await testerDeck());
  console.log(await introDeck());
})();
