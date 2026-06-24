# RomaLab — lab research orchestrator setup guide

**RomaLab** is a multi-agent research orchestrator (paper writing, data
analysis, idea/paper critique) that delegates to **Claude + GPT (Codex) +
Gemini** sub-agents — all on **subscription-login CLIs with NO API-token cost**.

Because the `gemini-native` harness is not yet in upstream/PyPI omnigent, each
person installs from **this fork**. omnigent is Apache-2.0 licensed, so
lab-internal sharing and running the fork is permitted (keep the LICENSE/NOTICE).

## Each lab member, on their own machine

```bash
# 1) Install omnigent FROM THE FORK (has gemini-native). Requires Python >=3.12.
OMNIGENT_SKIP_WEB_UI=true uv tool install --python 3.12 \
  "git+https://github.com/7-in-days/omnigent@feat/gemini-native"
# (or with pip in a venv:)
# python3.12 -m venv .venv && source .venv/bin/activate
# OMNIGENT_SKIP_WEB_UI=true pip install "git+https://github.com/7-in-days/omnigent@feat/gemini-native"

# 2) Install the three worker CLIs.
npm install -g @anthropic-ai/claude-code @openai/codex @google/gemini-cli

# 3) Log in with YOUR OWN subscriptions (no shared/API keys, no per-token cost).
claude auth login --claudeai
codex login
gemini login

# 4) Sanity check — all three must resolve.
command -v claude codex gemini

# 5) Get the bundle and run it.
mkdir -p ~/omnigent-agents
tar -xzf RomaLab.tar.gz -C ~/omnigent-agents
omnigent run ~/omnigent-agents/RomaLab
```

## Notes
- **No API cost:** workers use each user's subscription CLI login
  (`~/.claude/.credentials.json`, `~/.codex/auth.json`,
  `~/.gemini/oauth_creds.json`). Never set `GEMINI_API_KEY` / `OPENAI_API_KEY`
  / `ANTHROPIC_API_KEY` / Vertex creds — that would incur billing.
- **gemini one-time prompt:** if Claude Code shows a bypass-permissions warning
  on first run, accept it once (or set
  `~/.claude/settings.json` -> `{"skipDangerousModePermissionPrompt": true}`).
- **Updating:** to pull upstream omnigent changes into the fork later,
  rebase `feat/gemini-native` onto upstream `main` and re-tag/re-share.
- **Per-bundle skills:** merge-materials, find-material, cross-validate,
  paper-critique (see `skills/`).
