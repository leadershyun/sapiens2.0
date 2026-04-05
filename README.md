# Sapiens 2.0

Sapiens2.0 is an AI agent prototype powered by GitHub Copilot.  
It features computer control, long-term memory, and a polished CLI experience.

## Requirements

- Python 3.8+
- Active GitHub Copilot subscription
- [requests](https://pypi.org/project/requests/) package

## Installation (PowerShell / terminal)

```powershell
git clone https://github.com/leadershyun/sapiens2.0.git
cd sapiens2.0
pip install -e .
```

The `pip install -e .` step installs the `sapiens` command globally so you can start
Sapiens2.0 from any directory.

## Running Sapiens2.0

```powershell
sapiens wakeup
```

That's it. No need to navigate to the project folder.

You can also run it directly from the project folder:

```powershell
python .\main.py
```

Or pass a GitHub token directly:

```powershell
sapiens wakeup --token ghp_xxxxxxxxxxxx
```

## GitHub Authentication (OpenClaw style — device flow)

Sapiens2.0 uses the same **GitHub device flow** as OpenClaw.  
No need to register an OAuth App or generate a PAT — just run `/auth`.

### Authentication steps

```powershell
# 1. Start Sapiens2.0
sapiens wakeup

# 2. Start authentication
[You] /auth

# 3. A code and URL appear in the terminal
[Copilot] Starting GitHub device flow authentication...

  ┌─────────────────────────────────────────────────────┐
  │  Open the URL below in your browser and enter the   │
  │  code to authorize Sapiens2.0.                      │
  │                                                     │
  │  URL  : https://github.com/login/device             │
  │  Code : ABCD-1234                                   │
  └─────────────────────────────────────────────────────┘

# 4. Open https://github.com/login/device in your browser
# 5. Enter the code shown in the terminal (e.g. ABCD-1234) and approve
# 6. Terminal shows success:
[Copilot] ✅ GitHub authentication successful!
[Copilot] ℹ️  Your account has been saved — no need to re-authenticate next time.
```

> **Note:** Your account is automatically saved to `~/.sapiens2/config.json`.  
> The next time you run `sapiens wakeup`, you are logged in automatically.  
> Run `/logout` to unlink your account.

## Computer Control

The agent can control your computer directly — just ask naturally:

```
[You] what files are in this folder?
  ⚙  /ls .
     📁 /home/user/sapiens2.0
       📄 main.py
       📄 README.md
       📄 pyproject.toml
[Sapiens2.0] Your project contains 3 files: main.py, README.md, and pyproject.toml.

[You] what Python version am I running?
  ⚙  /exec python --version
     Python 3.11.4
[Sapiens2.0] You're running Python 3.11.4.
```

You can also use slash commands directly:

| Command | Description | Safety |
|---------|-------------|--------|
| `/pwd` | Show current working directory | Read-only |
| `/ls [path]` | List directory contents | Read-only |
| `/cat <file>` | Read a file | Read-only |
| `/cd <path>` | Change directory | Safe |
| `/write <file> [text]` | Write to a file | Confirm if file exists |
| `/rm <file>` | Delete a file | Always confirms |
| `/run <file>` | Execute a Python script | Warns for dangerous types |
| `/exec <cmd>` | Run a shell command | Warns for dangerous commands |

## Commands

| Command | Description |
|---------|-------------|
| `/auth` | Start GitHub device flow authentication (recommended) |
| `/auth <token>` | Provide a GitHub PAT/OAuth token directly |
| `/logout` | Unlink the saved GitHub account |
| `/status` | Show current auth status and selected model |
| `/models` | List available Copilot models |
| `/models <number\|name>` | Select model (e.g. `/models 2` or `/models gpt-4o-mini`) |
| `/new` | Start a new conversation (clears short-term memory) |
| `/reset` | Full reset: clears long-term memory + model settings |
| `/memory` | View current long-term memory contents |
| `/codegen <desc>` | Generate code with Copilot |
| `/help` | Show all commands |
| `/exit` | Exit |

Any plain text is sent to Copilot as a conversation message (authentication required).

## Memory System

Sapiens2.0 uses a two-tier memory system inspired by OpenClaw.

### Short-term memory
- Stores the current session's conversation history.
- Cleared on `/new` or exit.
- Included as context in every Copilot API call (most recent 20 messages).

### Long-term memory
- Stored in `sapiens_memory.json` in the project folder.
- Persists across sessions; cleared only on `/reset`.
- **The agent automatically extracts and saves important facts from conversations.**
- View with `/memory`.

```
[You] /memory
[Sapiens2.0] [Long-Term Memory]
  • preferred_language: Python
  • project_name: my-web-app
```

## Persistent GitHub Account

After your first `/auth`, your GitHub account is saved to `~/.sapiens2/config.json`  
(outside the project folder, so it's never committed).

The next time you run `sapiens wakeup`, you are automatically logged in.  
No need to re-authenticate unless you run `/logout` or `/reset`.

## Model Selection

```
[You] /models
[Sapiens2.0] Available Copilot models:
  1. gpt-4o  ◀ current
  2. gpt-4o-mini
  3. gpt-4-turbo
  4. claude-3.5-sonnet

[You] /models 2
[Sapiens2.0] ✅ Model changed to 'gpt-4o-mini'.
```

The selected model is saved in `sapiens_state.json` and persists across sessions.

## Stored Files

| File | Contents | How to clear |
|------|----------|--------------|
| `~/.sapiens2/config.json` | Saved GitHub auth token | `/logout` or delete the file |
| `sapiens_memory.json` | Long-term memory | `/reset` or delete the file |
| `sapiens_state.json` | Agent state (model selection) | `/reset` or delete the file |

`sapiens_memory.json` and `sapiens_state.json` are in `.gitignore`.

## Troubleshooting

### Authentication succeeded but Copilot fails

Run `/status` to check the current token state.

| Error | Cause | Fix |
|-------|-------|-----|
| `HTTP 403` on token exchange | No active Copilot subscription or insufficient OAuth scope | Check https://github.com/settings/copilot then run `/auth` again |
| `HTTP 401` | GitHub token expired | Run `/auth` again |
| `HTTP 404` | API endpoint or model name changed | Update the code or try a different model with `/models` |
| Organization SSO blocking | App not approved for SSO org | Approve the app at your GitHub organization's SSO settings |

## Advanced: Using Your Own OAuth App

```powershell
sapiens wakeup --client-id <OAuth_App_Client_ID>
```

Register an OAuth App at https://github.com/settings/developers
