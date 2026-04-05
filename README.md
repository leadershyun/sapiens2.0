# Sapiens 2.0

Sapiens2.0 is an AI agent prototype powered by GitHub Copilot.  
It features computer control, long-term memory, a polished CLI experience,
a **thinking indicator**, **force-cancel support**, and a **Discord bot mode**.

## Requirements

- Python 3.8+
- Active GitHub Copilot subscription
- [requests](https://pypi.org/project/requests/) package
- [discord.py](https://pypi.org/project/discord.py/) *(optional — required only for Discord bot mode)*

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

## Thinking / Responding Indicator

While Sapiens2.0 is preparing a response, a lightweight ASCII spinner is shown:

```
  [Sapiens2.0] Thinking |
```

The indicator animates on a single line and is erased the moment the response
starts printing — it never interferes with command output or tool execution results.

No extra setup is required; the indicator works in PowerShell and any terminal
that supports carriage-return (`\r`) cursor positioning.

## Cancelling a Response (Force-Stop)

Press **Ctrl+C** at any time while Sapiens2.0 is generating a response to cancel it.

```
[You] explain quantum computing in detail
  [Sapiens2.0] Thinking \
^C

  [Cancelled] Response generation stopped.  (Ctrl+C)

[You]
```

The application recovers cleanly — conversation history is unchanged and the
next prompt appears immediately. Ctrl+C during any other input (e.g. while
typing your message) exits the session as usual.

## Discord Bot Mode

Sapiens2.0 can be used through Discord, similar in spirit to OpenClaw.

### Setup

1. **Create a Discord bot** at <https://discord.com/developers/applications>
   - Click *New Application* → *Bot* → *Reset Token* and copy the token.
   - Under *Privileged Gateway Intents*, enable **Message Content Intent**.

2. **Install discord.py** (one-time):
   ```powershell
   pip install "discord.py>=2.0.0"
   # or use the extras shortcut:
   pip install "sapiens2[discord]"
   ```

3. **Start the bot**:
   ```powershell
   sapiens wakeup --discord --discord-token YOUR_BOT_TOKEN
   ```
   Or set the environment variable so you don't have to type the token each time:
   ```powershell
   $env:DISCORD_BOT_TOKEN = "YOUR_BOT_TOKEN"   # PowerShell
   sapiens wakeup --discord
   ```
   ```bash
   export DISCORD_BOT_TOKEN="YOUR_BOT_TOKEN"   # bash / macOS
   sapiens wakeup --discord
   ```

4. **Invite the bot to your server** — the invite URL is printed when the bot starts:
   ```
   [Discord] Sapiens2.0 online as Sapiens2.0#1234
   [Discord] Add the bot to a server:
     https://discord.com/api/oauth2/authorize?client_id=...&permissions=2147483648&scope=bot
   ```

### Using the Discord bot

The bot responds to:
- **Direct messages (DMs)** — always.
- **Server channel messages** — only when the bot is @mentioned.

Each channel maintains its own independent short-term memory (conversation context)
so conversations in different channels don't interfere with each other.
Long-term memory and model settings are shared across all channels.

```
# DM example
You: what Python version am I running?
Sapiens2.0: ...

# Server channel example
You: @Sapiens2.0 list the files in my project
Sapiens2.0: ...
```

The bot token is saved to `~/.sapiens2/config.json` on first use so you don't need
to pass `--discord-token` again on subsequent runs.

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
[Copilot] GitHub authentication successful!
[Copilot] Your account has been saved -- no need to re-authenticate next time.
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
| `/update` | Update Sapiens2.0 to the latest code automatically |
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
[Sapiens2.0] Model changed to 'gpt-4o-mini'.
```

The selected model is saved in `sapiens_state.json` and persists across sessions.

## Stored Files

| File | Contents | How to clear |
|------|----------|--------------|
| `~/.sapiens2/config.json` | Saved GitHub auth token + Discord bot token | `/logout` or delete the file |
| `sapiens_memory.json` | Long-term memory | `/reset` or delete the file |
| `sapiens_state.json` | Agent state (model selection) | `/reset` or delete the file |

`sapiens_memory.json` and `sapiens_state.json` are in `.gitignore`.

> **Security note:** `~/.sapiens2/config.json` stores your GitHub OAuth token and Discord bot
> token and is created with owner-only read/write permissions (`0o600` on Unix/macOS). On
> Windows, access is controlled by NTFS user permissions on your home directory. Do not share
> this file. Run `/logout` to remove it at any time.

## Troubleshooting

### Authentication succeeded but Copilot fails

Run `/status` to check the current token state.

| Error | Cause | Fix |
|-------|-------|-----|
| `HTTP 403` on token exchange | No active Copilot subscription or insufficient OAuth scope | Check https://github.com/settings/copilot then run `/auth` again |
| `HTTP 401` | GitHub token expired | Run `/auth` again |
| `HTTP 404` | API endpoint or model name changed | Update the code or try a different model with `/models` |
| Organization SSO blocking | App not approved for SSO org | Approve the app at your GitHub organization's SSO settings |

### Discord bot not responding

| Symptom | Fix |
|---------|-----|
| `Login failed` | Check the bot token at https://discord.com/developers/applications |
| Bot online but not responding to server messages | Make sure **Message Content Intent** is enabled in the bot settings, and that the bot is @mentioned |
| `discord.py not installed` | Run `pip install "discord.py>=2.0.0"` |

## Advanced: Using Your Own OAuth App

```powershell
sapiens wakeup --client-id <OAuth_App_Client_ID>
```

Register an OAuth App at https://github.com/settings/developers
