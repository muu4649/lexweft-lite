#!/usr/bin/env bash
# LeXWeft Lite のセットアップ (macOS / Linux)
#   1. Python の環境管理ツール uv が無ければ入れる (https://docs.astral.sh/uv/)
#   2. このフォルダの .venv に Python 3.12 と必要なライブラリを入れる
#   3. 起動用の「LeXWeft Lite.command」を作る
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
uv sync --python 3.12 --no-dev

LAUNCHER="$HERE/LeXWeft Lite.command"
cat > "$LAUNCHER" <<LAUNCH
#!/usr/bin/env bash
exec "$HERE/.venv/bin/lexweft" serve
LAUNCH
chmod +x "$LAUNCHER"

"$HERE/.venv/bin/lexweft" status
echo
read -r -p "Claude Desktop に LeXWeft Lite を登録しますか (元の設定は .bak に残します) [y/N]: " ans || ans=""
case "$ans" in
  y|Y|yes|YES) "$HERE/.venv/bin/lexweft" mcp-config --write --yes ;;
  *) echo "あとで登録するときは: \"$HERE/.venv/bin/lexweft\" mcp-config --write" ;;
esac

echo
echo "準備ができました。"
echo "  起動: 「LeXWeft Lite.command」をダブルクリック (またはターミナルで \"$HERE/.venv/bin/lexweft\" serve)"
echo "  画面: http://127.0.0.1:8765/"
