#!/usr/bin/env bash
# =============================================================================
# hermes_plugin/setup.sh
#
# 在你已经有的 hermes-agent（`~/.hermes/`）之上：
#   1) 创建一个隔离的 hermes profile（默认名 `quant`），不影响你现有 profile
#   2) 把本仓库的 hermes_plugin/ symlink 到 profile 的 plugins/quant/
#   3) 写入 QUANT_REPO_PATH 到 profile 的 .env
#   4) 提示你启用 quant toolset 与切换 profile 的方法
#
# 前提：
#   - `hermes` 命令已在 PATH（`which hermes` 有输出）
#   - DeepSeek 已配好（key 在当前 profile 的 .env，profile --clone 会一并复制）
#
# 用法：
#   bash hermes_plugin/setup.sh                    # 默认 profile 名 = quant
#   bash hermes_plugin/setup.sh my_profile_name    # 自定义 profile 名
#
# 幂等：反复执行安全；已有 profile 会跳过创建，symlink 会 --force 修正。
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_SRC="$REPO_ROOT/hermes_plugin"
PROFILE_NAME="${1:-quant}"
HERMES_ROOT="${HERMES_ROOT:-$HOME/.hermes}"
PROFILE_DIR="$HERMES_ROOT/profiles/$PROFILE_NAME"
PLUGIN_LINK="$PROFILE_DIR/plugins/quant"
PROFILE_ENV="$PROFILE_DIR/.env"

echo "==> hermes_plugin 安装 (profile-based)"
echo "    REPO_ROOT     = $REPO_ROOT"
echo "    HERMES_ROOT   = $HERMES_ROOT"
echo "    PROFILE_NAME  = $PROFILE_NAME"
echo "    PROFILE_DIR   = $PROFILE_DIR"

# ---------- Step 0: hermes CLI 检查 ----------
if ! command -v hermes >/dev/null 2>&1; then
  echo "!! 找不到 hermes 命令。请先装 hermes-agent 并把它加到 PATH。" >&2
  echo "   官方安装：curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash" >&2
  exit 1
fi

# ---------- Step 1: 创建 / 沿用 profile ----------
if [[ -d "$PROFILE_DIR" ]]; then
  echo "==> profile [$PROFILE_NAME] 已存在，跳过创建"
else
  echo "==> 创建 profile [$PROFILE_NAME]（--clone 会继承当前 profile 的 config + .env + skills）"
  hermes profile "$PROFILE_NAME" --clone --description "A-share quant research & 4 agent roles"
fi

# ---------- Step 2: symlink plugin 进 profile ----------
mkdir -p "$PROFILE_DIR/plugins"

if [[ -L "$PLUGIN_LINK" ]]; then
  current="$(readlink "$PLUGIN_LINK")"
  if [[ "$current" == "$PLUGIN_SRC" ]]; then
    echo "==> plugin symlink 已就位：$PLUGIN_LINK -> $PLUGIN_SRC"
  else
    echo "==> 修正 plugin symlink：$PLUGIN_LINK -> $PLUGIN_SRC（原指向 $current）"
    ln -sfn "$PLUGIN_SRC" "$PLUGIN_LINK"
  fi
elif [[ -e "$PLUGIN_LINK" ]]; then
  echo "!! $PLUGIN_LINK 已存在且不是 symlink。请手动检查/备份后重试。" >&2
  exit 1
else
  echo "==> 创建 plugin symlink：$PLUGIN_LINK -> $PLUGIN_SRC"
  ln -s "$PLUGIN_SRC" "$PLUGIN_LINK"
fi

# ---------- Step 3: 注入 QUANT_REPO_PATH 到 profile 的 .env ----------
touch "$PROFILE_ENV"
if grep -q "^QUANT_REPO_PATH=" "$PROFILE_ENV" 2>/dev/null; then
  # 用 python 就地覆盖，避免 sed 在 macOS/Linux 差异
  python3 - "$PROFILE_ENV" "$REPO_ROOT" <<'PY'
import sys, pathlib
env_path, repo = sys.argv[1], sys.argv[2]
p = pathlib.Path(env_path)
lines = p.read_text().splitlines()
out = []
for ln in lines:
    if ln.startswith("QUANT_REPO_PATH="):
        out.append(f"QUANT_REPO_PATH={repo}")
    else:
        out.append(ln)
p.write_text("\n".join(out) + "\n")
PY
  echo "==> 更新 profile .env 里的 QUANT_REPO_PATH → $REPO_ROOT"
else
  {
    echo ""
    echo "# quant plugin (hermes_plugin/setup.sh)"
    echo "QUANT_REPO_PATH=$REPO_ROOT"
    echo "# QUANT_RPC_TIMEOUT_SEC=180   # 可选：调大后 run_backtest_slice 才不容易超时"
  } >> "$PROFILE_ENV"
  echo "==> 已追加 QUANT_REPO_PATH 到 $PROFILE_ENV"
fi

# ---------- Step 4: 提示 ----------
cat <<EOF

======================================================================
hermes profile [$PROFILE_NAME] + quant plugin 就绪。

下一步：

  1) 打开 profile 的 config.yaml，把 quant 加到 enabled_toolsets：
        hermes config set --profile $PROFILE_NAME agent.enabled_toolsets '["core","quant"]'
     或手动编辑：
        \$EDITOR $PROFILE_DIR/config.yaml

     参考模板：$PLUGIN_SRC/configs/deepseek_profile.example.yaml

  2) 检查健康：
        hermes profile show $PROFILE_NAME
        HERMES_HOME=$PROFILE_DIR hermes doctor

  3) 从 quant 仓库跑：
        cd $REPO_ROOT
        uv run python main.py --mode hermes-chat --profile $PROFILE_NAME --prompt "hi"
        uv run python main.py --mode hermes-chat --profile $PROFILE_NAME --skill analyst_zoe \\
                              --code 000001.XSHE --date 2025-08-01

  4) 若创建了 wrapper alias（默认会在 ~/.local/bin/$PROFILE_NAME）：
        $PROFILE_NAME -z "hi"                    # 直接以该 profile 起手
        # 这条等价于：HERMES_HOME=$PROFILE_DIR hermes -z "hi"

  5) 卸载：
        rm $PLUGIN_LINK
        # 保守：不删 profile 本身，你可能还想留用
        # hermes profile remove $PROFILE_NAME    # 若确定要删

======================================================================
EOF
