#!/usr/bin/env bash
# =============================================================================
# hermes_plugin/setup.sh
#
# On top of an existing hermes-agent (`~/.hermes/`):
#   1) create an isolated hermes profile (default name `quant`) that does not touch your existing profiles
#   2) symlink this repo's hermes_plugin/ into the profile's plugins/quant/
#   3) write QUANT_REPO_PATH into the profile's .env
#   4) tell you how to enable the quant toolset and switch profiles
#
# Prerequisites:
#   - the `hermes` command is on PATH (`which hermes` prints something)
#   - DeepSeek is configured (the key is in the current profile's .env; `profile --clone` copies it along)
#
# Usage:
#   bash hermes_plugin/setup.sh                    # default profile name = quant
#   bash hermes_plugin/setup.sh my_profile_name    # custom profile name
#
# Idempotent: safe to run repeatedly; an existing profile is not recreated and the symlink is fixed with --force.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_SRC="$REPO_ROOT/hermes_plugin"
PROFILE_NAME="${1:-quant}"
HERMES_ROOT="${HERMES_ROOT:-$HOME/.hermes}"
PROFILE_DIR="$HERMES_ROOT/profiles/$PROFILE_NAME"
PLUGIN_LINK="$PROFILE_DIR/plugins/quant"
PROFILE_ENV="$PROFILE_DIR/.env"

echo "==> hermes_plugin install (profile-based)"
echo "    REPO_ROOT     = $REPO_ROOT"
echo "    HERMES_ROOT   = $HERMES_ROOT"
echo "    PROFILE_NAME  = $PROFILE_NAME"
echo "    PROFILE_DIR   = $PROFILE_DIR"

# ---------- Step 0: check the hermes CLI ----------
if ! command -v hermes >/dev/null 2>&1; then
    echo "!! hermes command not found. Install hermes-agent first and add it to PATH." >&2
    echo "   Official install: curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash" >&2
  exit 1
fi

# ---------- Step 1: create / reuse the profile ----------
if [[ -d "$PROFILE_DIR" ]]; then
    echo "==> profile [$PROFILE_NAME] already exists, skipping creation"
else
    echo "==> creating profile [$PROFILE_NAME] (--clone inherits the current profile's config + .env + skills)"
  hermes profile "$PROFILE_NAME" --clone --description "A-share quant research & 4 agent roles"
fi

# ---------- Step 2: symlink the plugin into the profile ----------
mkdir -p "$PROFILE_DIR/plugins"

if [[ -L "$PLUGIN_LINK" ]]; then
  current="$(readlink "$PLUGIN_LINK")"
  if [[ "$current" == "$PLUGIN_SRC" ]]; then
        echo "==> plugin symlink already in place: $PLUGIN_LINK -> $PLUGIN_SRC"
  else
        echo "==> fixing plugin symlink: $PLUGIN_LINK -> $PLUGIN_SRC (previously pointed to $current)"
    ln -sfn "$PLUGIN_SRC" "$PLUGIN_LINK"
  fi
elif [[ -e "$PLUGIN_LINK" ]]; then
    echo "!! $PLUGIN_LINK already exists and is not a symlink. Check/back it up manually and retry." >&2
  exit 1
else
    echo "==> creating plugin symlink: $PLUGIN_LINK -> $PLUGIN_SRC"
  ln -s "$PLUGIN_SRC" "$PLUGIN_LINK"
fi

# ---------- Step 3: inject QUANT_REPO_PATH into the profile's .env ----------
touch "$PROFILE_ENV"
if grep -q "^QUANT_REPO_PATH=" "$PROFILE_ENV" 2>/dev/null; then
  # overwrite in place with python to avoid sed differences between macOS and Linux
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
    echo "==> updating QUANT_REPO_PATH in the profile .env -> $REPO_ROOT"
else
  {
    echo ""
    echo "# quant plugin (hermes_plugin/setup.sh)"
    echo "QUANT_REPO_PATH=$REPO_ROOT"
        echo "# QUANT_RPC_TIMEOUT_SEC=180   # optional: raise it so run_backtest_slice is less likely to time out"
  } >> "$PROFILE_ENV"
    echo "==> appended QUANT_REPO_PATH to $PROFILE_ENV"
fi

# ---------- Step 4: hints ----------
cat <<EOF

======================================================================
hermes profile [$PROFILE_NAME] + quant plugin are ready.

Next steps:

    1) open the profile's config.yaml and add quant to enabled_toolsets:
        hermes config set --profile $PROFILE_NAME agent.enabled_toolsets '["core","quant"]'
          or edit it by hand:
        \$EDITOR $PROFILE_DIR/config.yaml

          reference template: $PLUGIN_SRC/configs/deepseek_profile.example.yaml

    2) check health:
        hermes profile show $PROFILE_NAME
        HERMES_HOME=$PROFILE_DIR hermes doctor

    3) run from the quant repo:
        cd $REPO_ROOT
        uv run python main.py --mode hermes-chat --profile $PROFILE_NAME --prompt "hi"
        uv run python main.py --mode hermes-chat --profile $PROFILE_NAME --skill analyst_zoe \\
                              --code 000001.XSHE --date 2025-08-01

    4) if a wrapper alias was created (by default at ~/.local/bin/$PROFILE_NAME):
                $PROFILE_NAME -z "hi"                    # start directly with that profile
                # equivalent to: HERMES_HOME=$PROFILE_DIR hermes -z "hi"

    5) uninstall:
        rm $PLUGIN_LINK
                # conservative: the profile itself is not deleted, you may want to keep it
                # hermes profile remove $PROFILE_NAME    # only if you are sure

======================================================================
EOF
