# LeXWeft Lite

資料を入れると、LLM が読みやすい Markdown に変換して手元に蓄積し、資料に書かれた「課題」と「解決手段」のつながり（意味層）を作って見られるようにする、ローカルで動くアプリです。意味層は Claude Desktop などの LLM が MCP 経由で書き、同じ経路で検索にも使います。

- データは自分の PC の中だけに保存します（既定は `~/LeXWeftLite`）
- アプリ自身は LLM を呼ばず、API キーも使いません
- 画面はブラウザで開きます（`http://127.0.0.1:8765/`。自分の PC からだけ開けます）

## できること

| 機能 | 内容 |
|---|---|
| 保存 | md / txt / html / pdf / docx / csv、フォルダ、URL を取り込み、段落に分けて保存 |
| Markdown 変換 | 資料ごとに、書誌（front matter）と段落番号 `[¶n]` 付きの Markdown を書き出す |
| 意味層 | 型（既定は「課題」「解決手段」。自分で足せる）ごとの概念、別名、根拠の段落、概念どうしの関係 |
| 可視化 | 概念と資料のつながりを図で見る。点を押すと根拠の段落が出る |
| 検索 | 段落の全文検索。言い換えを `\|` で区切って並べると、まとめて探す |
| LLM と接続 | MCP サーバー。LLM が資料を読み、根拠の段落番号付きで意味層を書き、探す |

## 入れ方

### macOS

1. このページの「Code」→「Download ZIP」で落として展開する（または `git clone`）
2. ターミナルで展開したフォルダに移り、次を実行する

```bash
bash install.sh
```

Python の環境管理ツール [uv](https://docs.astral.sh/uv/) が無ければ入れ、フォルダ内の `.venv` に Python 3.12 と必要なライブラリを入れます。最後に Claude Desktop へ登録するか聞きます。

3. 起動は、フォルダにできた「LeXWeft Lite.command」をダブルクリック

### Windows

1. ZIP を落として展開する
2. 展開したフォルダで PowerShell を開き、次を実行する

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

3. 起動は、フォルダにできた「LeXWeft Lite.bat」をダブルクリック

## 使い方

1. 「資料」タブで、ファイルを落とすか、フォルダのパス・URL を入れて取り込む
2. Claude Desktop で次のように頼む（またはプロンプト一覧から `build_layer` を選ぶ）

   > LeXWeft Lite の、まだ意味層が無い資料を読んで、課題と解決手段を根拠の段落つきで書いて。解決手段が課題を解く関係も結んで。

3. 「意味層」「つながり」タブで結果を見て、名前・型・別名・関係を直す。同じ意味の概念は「まとめる」で 1 つにする
4. 探すときは Claude Desktop に頼むか、「検索」タブを使う

   > LeXWeft Lite で「セル間の熱伝播」を解決する手段を、根拠の段落番号つきで一覧にして。言い換えも使って探して。

意味層は画面からも書けます。資料の段落にチェックを入れ、概念の名前と型を選んで「書く」を押します。

## Claude Desktop への登録

`install.sh` / `install.ps1` で登録しなかった場合は、次で登録できます（元の設定は `.bak` に残ります）。

```bash
./.venv/bin/lexweft mcp-config --write
```

手で書く場合は `./.venv/bin/lexweft mcp-config` で表示される内容を、Claude Desktop の設定ファイルの `mcpServers` に足します。登録後は Claude Desktop を一度終了して開き直してください。

## コマンド

| コマンド | 内容 |
|---|---|
| `lexweft serve` | 画面を開く |
| `lexweft add <ファイル/フォルダ/URL>` | 取り込む |
| `lexweft status` | 件数を見る |
| `lexweft export [--out DIR]` | 資料ごとの Markdown と意味層（`layer.md`）を書き出す |
| `lexweft mcp-config [--write]` | Claude Desktop に登録する設定を表示・書き込み |
| `lexweft mcp` | MCP サーバー（stdio）として動く（Claude Desktop が呼ぶ） |

## 保存先

既定は `~/LeXWeftLite`。環境変数 `LEXWEFT_HOME` で変えられます。

| 場所 | 中身 |
|---|---|
| `lexweft.sqlite3` | 資料・段落・意味層 |
| `markdown/` | 資料ごとの Markdown |
| `files/` | 画面から取り込んだファイルの写し |

消すときは、このフォルダと保存先のフォルダを削除し、Claude Desktop の設定から `lexweft-lite` を外します。

## 注意

- PDF は文字を取り出せるものだけに対応します（スキャン画像の PDF は文字になりません）
- 同じファイルを内容を変えて取り込み直すと置き換えます。本文が変わっていない段落に付けた根拠と関係は引き継ぎます
- 検索は文字の一致で探します。意味の近い言い換えは、LLM に複数の言い換えを作らせて探すと取りこぼしが減ります
