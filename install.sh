#!/usr/bin/env bash
# LeXWeft Lite のセットアップ (macOS / Linux)
#   1. Python の環境管理ツール uv が無ければ入れる (https://docs.astral.sh/uv/)
#   2. このフォルダの .venv に Python 3.12 と必要なライブラリを入れる
#   3. 起動用の「LeXWeft Lite.command」を作り、Swift のコンパイラがあれば Mac アプリ (~/Applications/LeXWeft Lite.app) も作る
#   4. 希望すれば Claude Desktop に MCP サーバーとして登録する
set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"

if ! command -v uv >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/uv" ]; then
    export PATH="$HOME/.local/bin:$PATH"
  else
    echo "uv (Python の環境管理ツール) を入れます: https://docs.astral.sh/uv/"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi

echo "Python と必要なライブラリを .venv に入れます (初回は数分かかります)"
# Python は uv が用意するもの (この Mac の CPU 向け) を使う. 古い Intel 用の Python が入っていても、それには頼らない
UV_PYTHON_PREFERENCE=only-managed uv sync --python 3.12 --no-dev

LAUNCHER="$HERE/LeXWeft Lite.command"
cat > "$LAUNCHER" <<LAUNCH
#!/usr/bin/env bash
exec "$HERE/.venv/bin/lexweft" serve
LAUNCH
chmod +x "$LAUNCHER"

# Mac アプリ: Xcode の Command Line Tools があるときだけ作る (無いときは .command で起動する)
APP=""
if [ "$(uname)" = "Darwin" ] && xcode-select -p >/dev/null 2>&1 && command -v swiftc >/dev/null 2>&1; then
  mkdir -p "$HOME/Applications"
  if bash "$HERE/packaging/macos/build_app.sh" "$HERE/.venv/bin/lexweft" "$HOME/Applications/LeXWeft Lite.app"; then
    APP="$HOME/Applications/LeXWeft Lite.app"
  else
    echo "Mac アプリは作れませんでした。「LeXWeft Lite.command」で起動できます。"
  fi
fi

"$HERE/.venv/bin/lexweft" status
echo
read -r -p "Claude Desktop に LeXWeft Lite を登録しますか (元の設定は .bak に残します) [y/N]: " ans || ans=""
case "$ans" in
  y|Y|yes|YES) "$HERE/.venv/bin/lexweft" mcp-config --write --yes ;;
  *) echo "あとで登録するときは: \"$HERE/.venv/bin/lexweft\" mcp-config --write" ;;
esac

echo
echo "準備ができました。"
if [ -n "$APP" ]; then
  echo "  起動: Launchpad か Spotlight で「LeXWeft Lite」を開く ($APP)"
else
  echo "  起動: 「LeXWeft Lite.command」をダブルクリック (またはターミナルで \"$HERE/.venv/bin/lexweft\" serve)"
  echo "  Mac アプリとして使いたいときは、ターミナルで xcode-select --install を実行してから、このセットアップをもう一度実行してください"
fi
