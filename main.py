"""
Sapiens2.0 - AI Agent
======================
An AI agent prototype with computer control, long-term memory, GitHub Copilot integration,
automatic MCP (Model Context Protocol) discovery/installation/usage, a thinking indicator,
Ctrl+C force-cancel support, queued-input-while-working, a Discord bot mode, and
configurable goal-seeking retry behaviour via /will.

Features:
  1. Conversational AI powered by GitHub Copilot
  2. Computer control: inspect files, navigate directories, create/edit files, run commands
  3. Short-term memory (session) and long-term memory (persistent file)
  4. Model selection (/models), new conversation (/new), full reset (/reset)
  5. Persistent GitHub account linkage across runs
  6. Automatic MCP discovery: the agent reads GitHub MCP descriptions, selects the best
     server for your goal, installs it, and uses it — all without manual configuration.
  7. Thinking indicator: an ASCII spinner shows while Sapiens2.0 is preparing a response.
  8. Force-cancel: press Ctrl+C during response generation to stop it cleanly.
  9. Discord bot mode: run as a Discord bot with `sapiens wakeup --discord`.
 10. Queued input: type your next question while the agent is still responding — it is
     queued automatically and processed as soon as the current response finishes.
 11. Resilient error handling: subprocess and tool failures never crash the session;
     they are surfaced as structured error strings the model can analyze.
 12. /will command: configure how persistently the agent retries after a failure
     (off / low / medium / high / max).  Setting persists across restarts.

Installation & Usage:
  pip install -e .         # Install once — makes 'sapiens' command available globally
  sapiens wakeup           # Start Sapiens2.0 from any directory

  Or run directly:
  python main.py           # Run from project folder, then /auth to authenticate

Authentication (OpenClaw style — GitHub device flow):
  1. Run: sapiens wakeup
  2. Type: /auth
  3. Note the code shown in the terminal (e.g. ABCD-1234)
  4. Open https://github.com/login/device in your browser and enter the code
  5. Approve — Sapiens2.0 detects the approval automatically
  6. Your account is saved for future runs (no need to re-authenticate)

Thinking indicator:
  While Sapiens2.0 is preparing a reply, a spinning indicator is shown:
    [Sapiens2.0] Thinking |
  It animates on one line and is erased when the response begins printing.

Cancel a response:
  Press Ctrl+C while the response is being generated to cancel it.
  The app recovers cleanly and shows the next prompt immediately.

Queued input (responding while the agent works):
  You can type your next message at any time — even while the agent is generating a
  response.  The input is queued and processed immediately after the current response
  finishes.  A tip is shown at the start of each response so you know it is safe to type.

Discord bot mode:
  pip install "discord.py>=2.0.0"
  sapiens wakeup --discord --discord-token <BOT_TOKEN>
  (or set DISCORD_BOT_TOKEN env var and omit --discord-token)
  See README.md for full setup instructions.

Key Commands:
  /auth                 Start GitHub device flow authentication (recommended)
  /auth <token>         Provide a GitHub PAT/OAuth token directly
  /logout               Unlink the saved GitHub account
  /models [num|name]    List or select available Copilot models
  /think [level]        View or set reasoning intensity: off / low / medium / high
  /will [level]         View or set retry/persistence intensity: off / low / medium / high / max
  /new                  Start a new conversation (short-term memory cleared)
  /reset                Full reset: long-term memory + model settings cleared
  /update               Update Sapiens2.0 to the latest code automatically
  /memory               View long-term memory contents
  /pwd                  Print current working directory
  /ls [path]            List directory contents
  /cat <file>           Read a file
  /write <file> [text]  Write to a file (prompts if no text given)
  /rm <file>            Delete a file (requires confirmation)
  /cd <path>            Change working directory
  /run <file>           Run a Python script
  /exec <cmd>           Run a shell command
  /codegen <desc>       Generate code with Copilot
  /mcp                  Show installed MCP servers
  /mcp list             List curated + installed MCPs
  /mcp auto <goal>      Auto-discover, select, and install the best MCP for a goal
  /mcp install <name>   Install an MCP from the curated registry by name
  /mcp tools <name>     List tools available in an installed MCP server
  /mcp call <n> <t> [j] Call MCP tool <t> on server <n> with optional JSON args
  /help                 Show help text
  /exit or /quit        Exit

Memory system:
  Short-term: session conversation history. Cleared on /new or exit.
  Long-term:  ~/.sapiens2/sapiens_memory.json — persists across sessions. Cleared only on /reset.
              The agent automatically extracts and saves important facts from conversations.

Computer control:
  The agent can control your computer by issuing tool commands in its responses.
  Just ask naturally: "what files are in this folder?" or "run the tests" and the
  agent will use the appropriate tools automatically.

MCP auto-discovery:
  The agent automatically discovers, installs, and uses MCP servers when needed.
  - It searches GitHub for MCP repos, reads their README/description, and uses
    Copilot to pick the best match for the user's goal.
  - A curated baseline registry of well-known MCPs is always available.
  - All installation steps are logged transparently (repo URL, package name, command).
  - Installed MCPs are persisted to ~/.sapiens2/mcp_state.json across sessions.
  - The agent can call MCP tools automatically using <tool>/mcp-call ...</tool> tags.

Dependencies:
  pip install requests
  pip install "discord.py>=2.0.0"  # optional — required only for Discord bot mode
"""

import argparse
import asyncio
import base64
import itertools
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import json
import textwrap
from typing import Dict, List, Optional, Tuple, Union

try:
    import requests
except ImportError:
    print("[Error] 'requests' package is required. Run: pip install requests")
    sys.exit(1)

try:
    import discord
    from discord.ext import commands as discord_commands
    _DISCORD_AVAILABLE = True
except ImportError:
    _DISCORD_AVAILABLE = False

# ─────────────────────────────────────────────
#  Constants / Configuration
# ─────────────────────────────────────────────

# GitHub OAuth App Client ID (GitHub CLI public app — used for Copilot auth).
# Same approach as OpenClaw: no need to register your own OAuth App.
# Source: https://github.com/cli/cli (public OAuth App)
DEFAULT_CLIENT_ID = "Iv1.b507a08c87ecfe98"

# GitHub Copilot internal API endpoints
COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
COPILOT_CHAT_URL = "https://api.githubcopilot.com/chat/completions"

# GitHub device flow endpoints
GH_DEVICE_CODE_URL = "https://github.com/login/device/code"
GH_TOKEN_URL = "https://github.com/login/oauth/access_token"

# OAuth scopes required for Copilot token exchange
GH_DEVICE_FLOW_SCOPE = "read:user copilot"

# Default Copilot model
COPILOT_DEFAULT_MODEL = "gpt-4o"

# Copilot API token lifetime (seconds). Copilot tokens are valid for ~30 minutes.
COPILOT_TOKEN_LIFETIME_SECONDS = 1800

# Editor identification headers required by Copilot internal API
_EDITOR_VERSION = "vscode/1.95.0"
_PLUGIN_VERSION = "copilot-chat/0.22.3"
_USER_AGENT = "GitHubCopilotChat/0.22.3"
# GitHub API version for copilot_internal/v2/token endpoint
_GH_API_VERSION = "2022-11-28"
# GitHub API version for Copilot Chat Completions endpoint
_GH_CHAT_API_VERSION = "2023-07-07"

# Renew Copilot token this many seconds before it expires
COPILOT_TOKEN_EXPIRY_BUFFER_SECONDS = 60

# Dangerous file extensions and commands (require user confirmation before running)
DANGEROUS_EXTENSIONS = {".sh", ".bat", ".cmd", ".ps1", ".exe"}
CONFIRM_REQUIRED_COMMANDS = {"rm", "del", "rmdir", "rd", "format", "mkfs", "dd"}

# Banner width
BANNER_WIDTH = 60

# Short-term memory max messages (older messages are pruned when exceeded)
SHORT_TERM_MAX_MESSAGES = 20

# Fallback list of Copilot models (used when API query fails)
AVAILABLE_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "claude-3.5-sonnet",
    "o1-preview",
    "o1-mini",
]

# Copilot model list API endpoint
COPILOT_MODELS_URL = "https://api.githubcopilot.com/models"

# Persistent user-data directory — all user state lives here so it survives
# restarts and "sapiens update" (git pull in the install directory).
AUTH_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".sapiens2")
AUTH_CONFIG_FILE = os.path.join(AUTH_CONFIG_DIR, "config.json")

# Long-term memory file — stored in ~/.sapiens2/ alongside auth config so it
# survives restarts, working-directory changes, and "sapiens update" (git pull).
MEMORY_FILE = os.path.join(AUTH_CONFIG_DIR, "sapiens_memory.json")

# Agent state file (model selection, etc.) — stored in ~/.sapiens2/ for the same reason.
STATE_FILE = os.path.join(AUTH_CONFIG_DIR, "sapiens_state.json")

# Regex to detect agent tool calls embedded in model responses
# Format: <tool>/command args</tool>
TOOL_CALL_RE = re.compile(r'<tool>(.*?)</tool>', re.DOTALL | re.IGNORECASE)

# ── /think setting ───────────────────────────────────────────────────────────
# Valid reasoning-intensity levels and their corresponding max_tokens budgets.
# "off"    — fastest replies, minimal token budget.
# "low"    — concise answers.
# "medium" — balanced (default).
# "high"   — thorough, deep analysis; largest token budget.
THINK_LEVELS: List[str] = ["off", "low", "medium", "high"]
THINK_DEFAULT_LEVEL: str = "medium"
THINK_MAX_TOKENS: Dict[str, int] = {
    "off":    512,
    "low":    1024,
    "medium": 2048,
    "high":   4096,
}
# System-prompt suffix added to the agent prompt for each think level.
THINK_PROMPT_SUFFIX: Dict[str, str] = {
    "off":    "Be brief and direct. Skip unnecessary reasoning steps.",
    "low":    "Keep answers concise. Reason only as much as needed.",
    "medium": "",  # Default behaviour — no extra instruction
    "high": (
        "Think carefully and thoroughly before answering. "
        "Break down complex problems step-by-step, consider edge cases, "
        "and provide a well-reasoned, detailed response."
    ),
}

# ── /will setting ────────────────────────────────────────────────────────────
# Controls how persistently the agent retries after a failure before giving up.
# "off"    — no extra retries; fail immediately on first error.
# "low"    — 1 additional goal-seeking retry after a failure.
# "medium" — 2 additional retries (default).
# "high"   — 3 additional retries; agent analyzes errors and tries alternatives.
# "max"    — 5 additional retries; maximum persistence.
WILL_LEVELS: List[str] = ["off", "low", "medium", "high", "max"]
WILL_DEFAULT_LEVEL: str = "medium"
# Extra tool-call iterations allowed (on top of the base 5) when failures occur.
WILL_EXTRA_RETRIES: Dict[str, int] = {
    "off":    0,
    "low":    1,
    "medium": 2,
    "high":   3,
    "max":    5,
}

# ─────────────────────────────────────────────
#  MCP (Model Context Protocol) Constants
# ─────────────────────────────────────────────

# Persistent MCP state (installed servers, etc.) — stored next to auth config
MCP_STATE_FILE = os.path.join(AUTH_CONFIG_DIR, "mcp_state.json")

# GitHub REST API endpoints for MCP discovery
GITHUB_SEARCH_API = "https://api.github.com/search/repositories"
GITHUB_REPOS_API = "https://api.github.com/repos"

# Maximum GitHub search results to evaluate per auto-discovery run
MCP_MAX_CANDIDATES = 5

# MCP JSON-RPC protocol version
MCP_PROTOCOL_VERSION = "2024-11-05"

# Sapiens client version reported in the MCP initialize handshake
SAPIENS_VERSION = "2.0"

# Maximum lines to read from MCP server stdout when waiting for a JSON-RPC response
MCP_MAX_RESPONSE_LINES = 50

# Seconds to wait for a single line of output from the MCP server process.
# A per-line timeout prevents the agent from hanging indefinitely when an MCP
# server starts but never responds to the JSON-RPC initialize request.
MCP_READLINE_TIMEOUT_SECONDS = 15

# Regex for extracting npm package names from README install instructions
NPM_PACKAGE_RE = re.compile(r"npm install.*?([\w@][\w/.-]+)")

# Max characters to include from stderr in error messages
MCP_STDERR_MAX_CHARS = 300

# Max characters to read from a GitHub README for MCP description extraction
GITHUB_README_MAX_CHARS = 2000

# Stop words excluded from MCP keyword scoring (common English words with no discriminating value)
_MCP_SCORE_STOP_WORDS = {"", "a", "an", "the", "to", "for", "in", "of", "and", "or", "is", "on"}

# Curated baseline registry of well-known, safe MCP servers.
# Each entry is fully self-describing so the agent can install and run without
# any additional lookup.  Extended at runtime by GitHub discovery.
MCP_CURATED_REGISTRY: List[Dict] = [
    {
        "name": "filesystem",
        "package": "@modelcontextprotocol/server-filesystem",
        "install_type": "npm",
        "run_cmd": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "."],
        "description": (
            "File system access: read, write, list, and search files and directories "
            "on the local machine."
        ),
        "tags": ["files", "filesystem", "local", "read", "write", "directory", "folder"],
        "repo": "modelcontextprotocol/servers",
    },
    {
        "name": "github",
        "package": "@modelcontextprotocol/server-github",
        "install_type": "npm",
        "run_cmd": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "description": (
            "GitHub integration: search code/repos, read files, manage issues and pull requests."
        ),
        "tags": ["github", "git", "repositories", "issues", "pull requests", "code search"],
        "repo": "modelcontextprotocol/servers",
        "env_hints": {"GITHUB_PERSONAL_ACCESS_TOKEN": "GitHub PAT for private repo access"},
    },
    {
        "name": "brave-search",
        "package": "@modelcontextprotocol/server-brave-search",
        "install_type": "npm",
        "run_cmd": ["npx", "-y", "@modelcontextprotocol/server-brave-search"],
        "description": (
            "Web search using Brave Search API — finds current information from the internet."
        ),
        "tags": ["web search", "internet", "search", "browse", "online", "news", "lookup"],
        "repo": "modelcontextprotocol/servers",
        "env_hints": {"BRAVE_API_KEY": "Brave Search API key (get at https://api.search.brave.com)"},
    },
    {
        "name": "sqlite",
        "package": "@modelcontextprotocol/server-sqlite",
        "install_type": "npm",
        "run_cmd": ["npx", "-y", "@modelcontextprotocol/server-sqlite"],
        "description": (
            "SQLite database operations: query, insert, update, and manage SQLite databases."
        ),
        "tags": ["database", "sqlite", "sql", "query", "data", "db"],
        "repo": "modelcontextprotocol/servers",
    },
    {
        "name": "puppeteer",
        "package": "@modelcontextprotocol/server-puppeteer",
        "install_type": "npm",
        "run_cmd": ["npx", "-y", "@modelcontextprotocol/server-puppeteer"],
        "description": (
            "Browser automation: navigate web pages, click buttons, fill forms, take screenshots."
        ),
        "tags": [
            "browser", "automation", "web", "scraping", "screenshot",
            "puppeteer", "navigate", "click",
        ],
        "repo": "modelcontextprotocol/servers",
    },
    {
        "name": "fetch",
        "package": "@modelcontextprotocol/server-fetch",
        "install_type": "npm",
        "run_cmd": ["npx", "-y", "@modelcontextprotocol/server-fetch"],
        "description": "HTTP fetch: retrieve content from URLs and web APIs.",
        "tags": ["http", "fetch", "web", "api", "download", "url", "request"],
        "repo": "modelcontextprotocol/servers",
    },
]


# ─────────────────────────────────────────────
#  Node.js detection helper
# ─────────────────────────────────────────────

def _find_node_cmd(name: str) -> str:
    """
    Return the full path to a Node.js ecosystem command (node, npm, npx).

    Handles the common Windows installation quirk where npm and npx are
    installed as ``npm.cmd`` / ``npx.cmd`` wrapper scripts rather than
    plain executables, which means a bare ``subprocess.run(["npm", ...])``
    may raise FileNotFoundError even when Node.js is properly installed.

    Resolution order:
      1. ``shutil.which(name)``           — plain name via PATH
      2. ``shutil.which(name + ".cmd")``  — Windows .cmd wrapper via PATH
      3. Common Windows installation directories
         (``%ProgramFiles%\\nodejs``, ``%APPDATA%\\npm``, etc.)

    Falls back to returning *name* unchanged so the caller gets the original
    FileNotFoundError if Node.js genuinely is not installed.
    """
    # 1. Try the plain name first (works on Linux/macOS and on Windows when
    #    the Node.js bin directory is in PATH with the right extension).
    found = shutil.which(name)
    if found:
        return found

    if sys.platform == "win32":
        # 2. Try the .cmd wrapper that npm/npx ship as on Windows.
        found = shutil.which(name + ".cmd")
        if found:
            return found

        # 3. Probe common Windows installation directories.
        node_dirs = [
            os.path.join(
                os.environ.get("ProgramFiles", r"C:\Program Files"), "nodejs"
            ),
            os.path.join(
                os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                "nodejs",
            ),
            os.path.join(os.environ.get("APPDATA", ""), "npm"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "nodejs"),
        ]
        for node_dir in node_dirs:
            if not node_dir or not os.path.isdir(node_dir):
                continue
            for suffix in (".cmd", ".exe", ""):
                candidate = os.path.join(node_dir, name + suffix)
                if os.path.isfile(candidate):
                    return candidate

    # Fall back to the plain name — subprocess will raise FileNotFoundError if
    # Node.js really is not installed, and callers handle that explicitly.
    return name


# ─────────────────────────────────────────────
#  Stdin reader — queued input while working
# ─────────────────────────────────────────────

# Module-level queue populated by the background stdin reader thread.
# None until _start_stdin_reader() is called (i.e. in CLI mode only).
_stdin_queue: "Optional[queue.Queue[Optional[str]]]" = None


def _start_stdin_reader() -> "queue.Queue[Optional[str]]":
    """
    Start a daemon thread that continuously reads lines from stdin and puts
    them in a queue.  This allows user input to be captured even while the
    main thread is busy processing a previous request, enabling queued input.

    Returns the queue so the caller can read from it.
    """
    global _stdin_queue
    q: "queue.Queue[Optional[str]]" = queue.Queue()
    _stdin_queue = q

    def _reader() -> None:
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    q.put(None)  # EOF
                    break
                q.put(line.rstrip("\r\n"))
            except (EOFError, OSError, KeyboardInterrupt):
                q.put(None)
                break

    t = threading.Thread(target=_reader, daemon=True, name="stdin-reader")
    t.start()
    return q


def _safe_input(prompt: str = "") -> Optional[str]:
    """
    Read one line of user input.

    When the background stdin reader thread is active (CLI mode), this reads
    from the shared queue — which means input typed while the agent was working
    is returned immediately (queued input).

    Falls back to the built-in ``input()`` when called outside CLI mode
    (e.g. from tests or Discord mode).

    Args:
        prompt: Text to print before reading input (written to stdout, not
                passed to input() so it works correctly in both modes).

    Returns:
        The input line (without trailing newline), or None on EOF.
    """
    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()
    if _stdin_queue is not None:
        return _stdin_queue.get()
    # Fallback for non-CLI contexts
    try:
        return input()
    except EOFError:
        return None


# ─────────────────────────────────────────────
#  Spinner: thinking/responding indicator
# ─────────────────────────────────────────────

class Spinner:
    """
    Lightweight ASCII spinner for PowerShell/terminal.

    Displays an animated indicator on one line while Sapiens2.0 is
    preparing a response.  Stops and clears the line cleanly when
    done so that subsequent output is not disrupted.

    Usage (context manager — preferred):
        with Spinner("  [Sapiens2.0] Thinking"):
            result = some_blocking_call()

    Usage (manual):
        s = Spinner().start()
        result = some_blocking_call()
        s.stop()
    """

    _FRAMES = ["|", "/", "-", "\\"]
    _INTERVAL = 0.12  # seconds between frames

    def __init__(self, message: str = "  [Sapiens2.0] Thinking") -> None:
        self._message = message
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── Public API ───────────────────────────

    def start(self) -> "Spinner":
        """Start the spinner in a background daemon thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop the spinner and erase the indicator line."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        # Erase the spinner line so subsequent output starts cleanly
        clear_width = len(self._message) + 4
        sys.stdout.write(f"\r{' ' * clear_width}\r")
        sys.stdout.flush()

    # ── Context manager ──────────────────────

    def __enter__(self) -> "Spinner":
        return self.start()

    def __exit__(self, *_args: object) -> None:
        self.stop()

    # ── Private ──────────────────────────────

    def _spin(self) -> None:
        idx = 0
        while True:
            frame = self._FRAMES[idx % len(self._FRAMES)]
            sys.stdout.write(f"\r{self._message} {frame}")
            sys.stdout.flush()
            idx += 1
            if self._stop_event.wait(self._INTERVAL):
                break


# ─────────────────────────────────────────────
#  Auth Persistence Helpers
# ─────────────────────────────────────────────

def _save_auth_token(token: str) -> None:
    """Save GitHub token to ~/.sapiens2/config.json for persistence across runs.
    The file is created with owner-only read/write permissions (0o600).
    """
    try:
        os.makedirs(AUTH_CONFIG_DIR, exist_ok=True)
        # Use os.open to enforce restrictive permissions from creation time,
        # preventing other OS users from reading the OAuth token.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(AUTH_CONFIG_FILE, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"github_token": token}, f)
    except IOError:
        pass  # Non-fatal: session will still work without persistence


def _load_auth_token() -> Optional[str]:
    """Load saved GitHub token from ~/.sapiens2/config.json. Returns None if not found."""
    if not os.path.exists(AUTH_CONFIG_FILE):
        return None
    try:
        with open(AUTH_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        token = data.get("github_token")
        return token if isinstance(token, str) and token.strip() else None
    except (IOError, json.JSONDecodeError):
        return None


def _clear_auth_token() -> None:
    """Remove the saved GitHub token from disk."""
    if os.path.exists(AUTH_CONFIG_FILE):
        try:
            os.remove(AUTH_CONFIG_FILE)
        except OSError:
            pass


# ─────────────────────────────────────────────
#  Module 0: Memory Management (short/long term)
# ─────────────────────────────────────────────

class MemoryModule:
    """
    Short-term and long-term memory module.

    Short-term memory:
      - Current session conversation history (list of role/content messages)
      - Cleared on session exit or /new command

    Long-term memory:
      - Stored as JSON in ~/.sapiens2/sapiens_memory.json
      - Persists across sessions; cleared only on /reset
      - The agent automatically extracts important facts and stores them here
    """

    def __init__(self, memory_file: str = MEMORY_FILE):
        self._memory_file = memory_file
        self._long_term: Dict[str, str] = {}
        self._short_term: List[Dict[str, str]] = []
        self._load()

    # ── Short-term memory ───────────────────────

    def add_message(self, role: str, content: str) -> None:
        """Add a message to short-term memory (conversation history)."""
        self._short_term.append({"role": role, "content": content})
        self._short_term = self._short_term[-SHORT_TERM_MAX_MESSAGES:]

    def get_short_term(self) -> List[Dict[str, str]]:
        """Return the current short-term memory (conversation history)."""
        return list(self._short_term)

    def clear_short_term(self) -> None:
        """Clear short-term memory (session conversation)."""
        self._short_term = []

    # ── Long-term memory ────────────────────────

    def _load(self) -> None:
        """Load long-term memory from file."""
        if os.path.exists(self._memory_file):
            try:
                with open(self._memory_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._long_term = {str(k): str(v) for k, v in data.items()}
            except (json.JSONDecodeError, IOError):
                self._long_term = {}

    def _save(self) -> None:
        """Save long-term memory to file."""
        try:
            os.makedirs(os.path.dirname(self._memory_file), exist_ok=True)
            with open(self._memory_file, "w", encoding="utf-8") as f:
                json.dump(self._long_term, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"[Memory] Failed to save long-term memory: {e}")

    def update_long_term(self, updates: Dict[str, str]) -> None:
        """Update long-term memory and save to file."""
        if updates:
            self._long_term.update({str(k): str(v) for k, v in updates.items()})
            self._save()

    def get_long_term(self) -> Dict[str, str]:
        """Return all long-term memory entries."""
        return dict(self._long_term)

    def get_long_term_context(self) -> str:
        """Return long-term memory formatted for inclusion in the system prompt."""
        if not self._long_term:
            return ""
        parts = ["[Agent Long-Term Memory — facts remembered from previous sessions]"]
        for key, value in self._long_term.items():
            parts.append(f"  - {key}: {value}")
        return "\n".join(parts)

    def clear_long_term(self) -> None:
        """Clear long-term memory and delete the file."""
        self._long_term = {}
        if os.path.exists(self._memory_file):
            try:
                os.remove(self._memory_file)
            except OSError:
                pass

    def get_display(self) -> str:
        """Return long-term memory in a human-readable format."""
        if not self._long_term:
            return "(no long-term memory)"
        lines = []
        for key, value in self._long_term.items():
            lines.append(f"  • {key}: {value}")
        return "\n".join(lines)


# ─────────────────────────────────────────────
#  Module 1: GitHub Copilot Integration
# ─────────────────────────────────────────────

class CopilotModule:
    """
    GitHub Copilot integration module.

    Authentication methods:
      1. Direct token: set_token(github_token)
      2. GitHub device flow: authenticate_device_flow(client_id)

    Token types:
      - GitHub PAT (Personal Access Token) or OAuth token
      - Internally exchanged for a Copilot API token (30-minute lifetime)
    """

    def __init__(self):
        self._github_token: Optional[str] = None   # GitHub OAuth/PAT token
        self._copilot_token: Optional[str] = None  # Copilot API token (expires in 30 min)
        self._copilot_token_expires: float = 0  # Expiry timestamp (epoch seconds)
        self._model: str = COPILOT_DEFAULT_MODEL  # Selected model

    # ── Model selection ─────────────────────────

    def set_model(self, model: str) -> None:
        """Set the Copilot model to use."""
        self._model = model

    def get_model(self) -> str:
        """Return the currently selected model name."""
        return self._model

    def list_models(self) -> List[str]:
        """
        Return available Copilot models.
        Queries the API when authenticated; falls back to the built-in list on failure.
        """
        copilot_token = self._get_copilot_token()
        if copilot_token:
            try:
                resp = requests.get(
                    COPILOT_MODELS_URL,
                    headers={
                        "Authorization": f"Bearer {copilot_token}",
                        "Accept": "application/json",
                        "Editor-Version": _EDITOR_VERSION,
                        "Editor-Plugin-Version": _PLUGIN_VERSION,
                        "User-Agent": _USER_AGENT,
                        "X-GitHub-Api-Version": _GH_CHAT_API_VERSION,
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    models = [
                        m.get("id", "") if isinstance(m, dict) else str(m)
                        for m in data.get("data", [])
                    ]
                    models = [m for m in models if m]
                    if models:
                        return models
            except requests.RequestException:
                pass

        return list(AVAILABLE_MODELS)

    # ── Token management ────────────────────────

    def set_token(self, github_token: str) -> None:
        """Set the GitHub OAuth/PAT token directly."""
        self._github_token = github_token.strip()
        self._copilot_token = None  # Invalidate cached Copilot token

    def is_authenticated(self) -> bool:
        """Return True if a GitHub token has been set."""
        return bool(self._github_token)

    def get_status(self) -> str:
        """Return a human-readable authentication and token status string."""
        lines = []
        saved = os.path.exists(AUTH_CONFIG_FILE)
        if self._github_token:
            saved_note = "  (saved — will auto-login on next run)" if saved else ""
            lines.append(f"  GitHub token : ✅ set{saved_note}")
        else:
            lines.append("  GitHub token : ❌ not set  (run /auth to authenticate)")

        if self._copilot_token and time.time() < self._copilot_token_expires - COPILOT_TOKEN_EXPIRY_BUFFER_SECONDS:
            remaining = int(self._copilot_token_expires - time.time())
            lines.append(f"  Copilot token: ✅ valid ({remaining}s remaining)")
        elif self._github_token:
            lines.append("  Copilot token: ℹ️  will be exchanged on first message")
        else:
            lines.append("  Copilot token: ❌ not exchanged")

        lines.append(f"  Model        : {self._model}")

        return "\n" + "\n".join(lines)

    # ── Device flow authentication ──────────────

    def authenticate_device_flow(self, client_id: str = DEFAULT_CLIENT_ID) -> bool:
        """
        Authenticate using the GitHub device flow (same approach as OpenClaw).

        1. Request a device code from GitHub.
        2. User visits the verification URL and enters the code shown in the terminal.
        3. Poll until approval is detected, then store the OAuth token.

        Returns:
            True on success, False on failure.
        """
        print("[Copilot] Starting GitHub device flow authentication...")

        # Step 1: request device code
        try:
            resp = requests.post(
                GH_DEVICE_CODE_URL,
                headers={"Accept": "application/json"},
                data={"client_id": client_id, "scope": GH_DEVICE_FLOW_SCOPE},
                timeout=10,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[Copilot Error] Failed to request device code: {e}")
            return False

        data = resp.json()
        device_code = data.get("device_code")
        user_code = data.get("user_code")
        verification_uri = data.get("verification_uri", "https://github.com/login/device")
        interval = data.get("interval", 5)
        expires_in = data.get("expires_in", 900)

        print(f"\n  ┌─────────────────────────────────────────────────────┐")
        print(f"  │  Open the URL below in your browser and enter the   │")
        print(f"  │  code to authorize Sapiens2.0.                      │")
        print(f"  │                                                     │")
        print(f"  │  URL  : {verification_uri:<43} │")
        print(f"  │  Code : {user_code:<43} │")
        print(f"  │                                                     │")
        print(f"  │  Expires in: {expires_in}s{' ' * (39 - len(str(expires_in)))}│")
        print(f"  └─────────────────────────────────────────────────────┘\n")

        # Step 2: poll for approval
        deadline = time.time() + expires_in
        while time.time() < deadline:
            time.sleep(interval)
            try:
                poll_resp = requests.post(
                    GH_TOKEN_URL,
                    headers={"Accept": "application/json"},
                    data={
                        "client_id": client_id,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    timeout=10,
                )
                poll_resp.raise_for_status()
            except requests.RequestException as e:
                print(f"[Copilot Error] Token polling failed: {e}")
                return False

            poll_data = poll_resp.json()
            if "access_token" in poll_data:
                return self._finish_device_flow_success(poll_data["access_token"])

            error = poll_data.get("error", "")
            if error == "authorization_pending":
                print("[Copilot] Waiting for approval... (enter the code in your browser)")
            elif error == "slow_down":
                interval += 5
            elif error in ("expired_token", "access_denied"):
                print(f"[Copilot Error] {error}")
                return False

        print("[Copilot Error] Authentication timed out.")
        return False

    def _finish_device_flow_success(self, access_token: str) -> bool:
        """Store the token in memory and persist it to disk after successful device flow auth."""
        self._github_token = access_token
        self._copilot_token = None
        _save_auth_token(access_token)
        print("[Copilot] ✅ GitHub authentication successful!")
        print("[Copilot] ℹ️  Your account has been saved — no need to re-authenticate next time.")
        print("[Copilot] ℹ️  Copilot access will be verified on your first message.")
        return True

    # ── Copilot API token exchange ──────────────

    def _get_copilot_token(self) -> Optional[str]:
        """
        Exchange the GitHub OAuth token for a Copilot API token.
        The Copilot token is valid for 30 minutes and is automatically renewed when expired.

        Returns:
            Copilot API token string, or None on failure.
        """
        if not self._github_token:
            print("[Copilot Error] No GitHub token set. Run /auth to authenticate.")
            return None

        # Reuse existing token if still valid
        if self._copilot_token and time.time() < self._copilot_token_expires - COPILOT_TOKEN_EXPIRY_BUFFER_SECONDS:
            return self._copilot_token

        try:
            resp = requests.get(
                COPILOT_TOKEN_URL,
                headers={
                    "Authorization": f"token {self._github_token}",
                    "Accept": "application/json",
                    "Editor-Version": _EDITOR_VERSION,
                    "Editor-Plugin-Version": _PLUGIN_VERSION,
                    "User-Agent": _USER_AGENT,
                    "X-GitHub-Api-Version": _GH_API_VERSION,
                },
                timeout=10,
            )
            resp.raise_for_status()
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            if status == 401:
                print("[Copilot Error] GitHub token is invalid or expired. Run /auth again.")
            elif status == 403:
                print(
                    "[Copilot Error] Copilot token exchange denied (HTTP 403).\n"
                    "  Possible causes:\n"
                    "  1. No active Copilot subscription on this GitHub account.\n"
                    "     → Check subscription at https://github.com/settings/copilot\n"
                    "  2. Insufficient OAuth scopes — run /auth again to get a fresh token.\n"
                    "  3. Organization SSO policy blocking this app — approve at GitHub SSO settings."
                )
            elif status == 404:
                print(
                    "[Copilot Error] Copilot token exchange endpoint not found (HTTP 404).\n"
                    "  The Copilot internal API address may have changed."
                )
            else:
                print(f"[Copilot Error] Copilot token exchange failed (HTTP {status}): {e}")
            return None
        except requests.RequestException as e:
            print(f"[Copilot Error] Network error (Copilot token exchange): {e}")
            return None

        token_data = resp.json()
        self._copilot_token = token_data.get("token")
        expires_at = token_data.get("expires_at", 0)
        self._copilot_token_expires = (
            float(expires_at) if expires_at else time.time() + COPILOT_TOKEN_LIFETIME_SECONDS
        )
        return self._copilot_token

    # ── Code generation / chat ──────────────────

    def generate_code(self, prompt: str, language: str = "python") -> Optional[str]:
        """
        Generate code using Copilot.

        Args:
            prompt: Natural language description of the code to generate.
            language: Target programming language (default: python).

        Returns:
            Generated code string, or None on failure.
        """
        system_msg = (
            f"You are GitHub Copilot, an expert {language} programmer. "
            "Respond ONLY with clean, runnable code. "
            "No explanation, no markdown fences, just the code itself."
        )
        return self._call_copilot_api(system_msg, prompt)

    def chat(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        long_term_context: str = "",
    ) -> Optional[str]:
        """
        Send a message to Copilot and return the response.
        Includes short-term memory (conversation history) and long-term memory context.

        Args:
            message: User message.
            history: Previous conversation messages (short-term memory).
            long_term_context: Long-term memory text to include in system prompt.

        Returns:
            Copilot response string, or None on failure.
        """
        system_parts = [
            "You are Sapiens2.0, a helpful AI agent assistant. "
            "Answer concisely in the same language the user writes in."
        ]
        if long_term_context:
            system_parts.append(long_term_context)

        system_msg = "\n\n".join(system_parts)

        messages: List[Dict[str, str]] = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})

        return self._call_copilot_api_messages(system_msg, messages)

    def extract_memory_updates(self, user_msg: str, assistant_response: str) -> Dict[str, str]:
        """
        Extract important long-term facts from a conversation exchange.
        This is how the agent self-manages its long-term memory file.

        Args:
            user_msg: User message.
            assistant_response: Agent response.

        Returns:
            Dict of key-value pairs to add/update in long-term memory (empty if nothing noteworthy).
        """
        system_prompt = (
            "You are a memory manager for an AI agent called Sapiens2.0. "
            "Given a conversation exchange, extract important facts worth remembering long-term. "
            "Respond ONLY with a valid JSON object where keys are short descriptive labels "
            "and values are concise facts. "
            "If there is nothing important to remember, respond with exactly: {} "
            "Focus on: user preferences, important context, facts about the user, key topics. "
            "Keep entries concise. Max 5 new entries per exchange."
        )
        prompt = (
            f"User: {user_msg}\n"
            f"Assistant: {assistant_response}\n\n"
            "Extract memorable facts as a JSON object:"
        )

        result = self._call_copilot_api(system_prompt, prompt, max_tokens=256)
        if not result:
            return {}

        # Try parsing the full text as JSON first
        try:
            data = json.loads(result.strip())
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except json.JSONDecodeError:
            pass

        # Fall back to extracting the first complete JSON object by brace depth counting
        start = result.find("{")
        if start != -1:
            depth = 0
            for i, ch in enumerate(result[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            data = json.loads(result[start : i + 1])
                            if isinstance(data, dict):
                                return {str(k): str(v) for k, v in data.items()}
                        except json.JSONDecodeError:
                            pass
                        break
        return {}

    def _call_copilot_api(self, system_prompt: str, user_message: str, max_tokens: int = 1024) -> Optional[str]:
        """Call the Copilot Chat Completions API with a single user message."""
        messages = [{"role": "user", "content": user_message}]
        return self._call_copilot_api_messages(system_prompt, messages, max_tokens=max_tokens)

    def _call_copilot_api_messages(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 1024,
    ) -> Optional[str]:
        """
        Call the Copilot Chat Completions API with a conversation history.

        Args:
            system_prompt: System prompt content.
            messages: Conversation messages [{role, content}, ...].
            max_tokens: Maximum tokens to generate.

        Returns:
            Response text, or None on failure.
        """
        copilot_token = self._get_copilot_token()
        if not copilot_token:
            return None

        all_messages = [{"role": "system", "content": system_prompt}] + messages

        payload = {
            "model": self._model,
            "messages": all_messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }

        try:
            resp = requests.post(
                COPILOT_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {copilot_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Copilot-Integration-Id": "vscode-chat",
                    "Editor-Version": _EDITOR_VERSION,
                    "Editor-Plugin-Version": _PLUGIN_VERSION,
                    "User-Agent": _USER_AGENT,
                    "X-GitHub-Api-Version": _GH_CHAT_API_VERSION,
                    "openai-intent": "conversation-panel",
                },
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            if status == 401:
                print(
                    "[Copilot Error] Copilot API auth failed (HTTP 401). Token may have expired.\n"
                    "  The token will be renewed automatically. If this persists, run /auth again."
                )
                self._copilot_token = None  # Clear expired token
            elif status == 403:
                print(
                    "[Copilot Error] Copilot API access denied (HTTP 403).\n"
                    "  Check your Copilot subscription: https://github.com/settings/copilot"
                )
            elif status == 404:
                print(
                    "[Copilot Error] Copilot Chat API endpoint not found (HTTP 404).\n"
                    "  Verify the model name or API address."
                )
            elif status == 422:
                print(
                    "[Copilot Error] Invalid request format (HTTP 422).\n"
                    "  Check the model name and request parameters."
                )
            else:
                print(f"[Copilot Error] Copilot Chat API call failed (HTTP {status}): {e}")
            return None
        except requests.RequestException as e:
            print(f"[Copilot Error] Network error (Chat API): {e}")
            return None

        try:
            result = resp.json()
            return result["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError) as e:
            print(f"[Copilot Error] Failed to parse response: {e}")
            return None


# ─────────────────────────────────────────────
#  Module 2: Computer Control (System Commands)
# ─────────────────────────────────────────────

class SystemCommandModule:
    """
    Computer control module: file system navigation, file editing, command execution.

    Safety policy:
      - File deletion and sensitive commands require user confirmation.
      - Dangerous file extensions (.sh, .bat, .exe, etc.) trigger a warning before execution.
    """

    def __init__(self):
        self._cwd = os.getcwd()

    def get_cwd(self) -> str:
        """Return the current working directory."""
        return self._cwd

    def change_dir(self, path: str) -> str:
        """Change the working directory."""
        target = os.path.abspath(os.path.join(self._cwd, path))
        if not os.path.isdir(target):
            return f"[Error] Directory not found: {target}"
        self._cwd = target
        os.chdir(target)
        return f"Working directory: {self._cwd}"

    def list_dir(self, path: str = ".") -> str:
        """List the contents of a directory."""
        target = os.path.abspath(os.path.join(self._cwd, path))
        if not os.path.exists(target):
            return f"[Error] Path not found: {target}"

        try:
            entries = os.listdir(target)
        except PermissionError:
            return f"[Error] Permission denied: {target}"

        if not entries:
            return f"{target} (empty)"

        lines = [f"📁 {target}"]
        for entry in sorted(entries):
            full = os.path.join(target, entry)
            prefix = "📂" if os.path.isdir(full) else "📄"
            lines.append(f"  {prefix} {entry}")
        return "\n".join(lines)

    def read_file(self, path: str) -> str:
        """Read and return the contents of a file."""
        target = os.path.abspath(os.path.join(self._cwd, path))
        if not os.path.isfile(target):
            return f"[Error] File not found: {target}"

        try:
            with open(target, "r", encoding="utf-8") as f:
                content = f.read()
            return f"--- {target} ---\n{content}\n---"
        except UnicodeDecodeError:
            return f"[Error] Cannot read binary file: {target}"
        except PermissionError:
            return f"[Error] Permission denied: {target}"

    def write_file(self, path: str, content: str) -> str:
        """Write content to a file."""
        target = os.path.abspath(os.path.join(self._cwd, path))

        try:
            dirname = os.path.dirname(target)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            return f"✅ File written: {target}"
        except PermissionError:
            return f"[Error] Permission denied: {target}"

    def delete_file(self, path: str) -> str:
        """Delete a file. Always asks for confirmation."""
        target = os.path.abspath(os.path.join(self._cwd, path))
        if not os.path.exists(target):
            return f"[Error] File not found: {target}"

        if not _confirm(f"⚠️  Delete '{target}'? (cannot be undone)"):
            return "Cancelled."

        try:
            os.remove(target)
            return f"✅ File deleted: {target}"
        except PermissionError:
            return f"[Error] Permission denied: {target}"
        except IsADirectoryError:
            return "[Error] Cannot delete a directory with /rm. Use rmdir."

    def run_file(self, path: str) -> str:
        """Run a Python script or shell script."""
        target = os.path.abspath(os.path.join(self._cwd, path))
        if not os.path.isfile(target):
            return f"[Error] File not found: {target}"

        ext = os.path.splitext(target)[1].lower()

        if ext == ".py":
            cmd = [sys.executable, target]
        else:
            cmd = [target]

        return _run_subprocess(cmd, cwd=self._cwd)

    def exec_command(self, command: str) -> str:
        """Run a shell command. Asks for confirmation before executing dangerous commands."""
        first_word = command.strip().split()[0].lower() if command.strip() else ""
        if first_word in CONFIRM_REQUIRED_COMMANDS:
            if not _confirm(f"⚠️  '{command}' may be dangerous. Run anyway?"):
                return "Cancelled."

        return _run_subprocess(command, shell=True, cwd=self._cwd)


# ─────────────────────────────────────────────
#  Module 3: MCP Runner (JSON-RPC stdio client)
# ─────────────────────────────────────────────

class MCPRunner:
    """
    Manages a single MCP server subprocess and communicates with it via the
    Model Context Protocol JSON-RPC 2.0 stdio transport.

    Lifecycle:
      runner = MCPRunner(["npx", "-y", "@modelcontextprotocol/server-filesystem", "."])
      ok, msg = runner.start()     # launch server + initialize
      tools   = runner.get_tools() # list available tools
      result  = runner.call_tool("read_file", {"path": "README.md"})
      runner.stop()                # terminate server process
    """

    def __init__(self, run_cmd: List[str], env: Optional[Dict[str, str]] = None):
        self._cmd = run_cmd
        self._extra_env = env or {}
        self._process: Optional[subprocess.Popen] = None
        self._msg_id = 0
        self._tools: List[Dict] = []
        # Background thread that drains stdout into a queue so reads can time out.
        self._stdout_queue: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._stdout_reader: Optional[threading.Thread] = None

    # ── Internal helpers ────────────────────────

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _start_stdout_reader(self) -> None:
        """
        Launch a daemon thread that continuously reads stdout from the MCP
        server process and enqueues each line.  This decouples the read from
        the send so that _read_line() can impose a wall-clock timeout without
        blocking the whole process forever.
        """
        def _drain() -> None:
            assert self._process is not None
            try:
                while True:
                    line = self._process.stdout.readline()
                    self._stdout_queue.put(line if line else None)
                    if not line:
                        break
            except (OSError, ValueError):
                self._stdout_queue.put(None)

        self._stdout_reader = threading.Thread(target=_drain, daemon=True, name="mcp-stdout-drain")
        self._stdout_reader.start()

    def _read_line(self, timeout: float = MCP_READLINE_TIMEOUT_SECONDS) -> Optional[bytes]:
        """
        Return the next stdout line from the MCP process, or None on timeout / EOF.

        Because the actual I/O happens in the background drain thread, this call
        never blocks forever — it returns None after *timeout* seconds even if
        the server never responds.

        Args:
            timeout: Maximum seconds to wait for the next line.  Must be a
                     positive float.  Values <=0 are treated as an immediate
                     non-blocking check (returns None if no line is queued).
        """
        try:
            item = self._stdout_queue.get(timeout=timeout)
            return item  # may be None on EOF
        except queue.Empty:
            return None  # timeout

    def _send(self, method: str, params: Optional[dict] = None) -> Optional[dict]:
        """
        Send a JSON-RPC 2.0 request to the MCP server and return the response.
        Reads up to MCP_MAX_RESPONSE_LINES lines of stdout looking for the matching
        response ID.  Each readline attempt times out after MCP_READLINE_TIMEOUT_SECONDS
        so the call cannot block indefinitely.
        """
        if not self._process:
            return None
        msg_id = self._next_id()
        req: dict = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            req["params"] = params
        try:
            line = json.dumps(req) + "\n"
            self._process.stdin.write(line.encode("utf-8"))
            self._process.stdin.flush()
            for _ in range(MCP_MAX_RESPONSE_LINES):
                raw = self._read_line()
                if not raw:
                    break
                try:
                    resp = json.loads(raw.decode("utf-8", errors="replace"))
                    if resp.get("id") == msg_id:
                        return resp
                except json.JSONDecodeError:
                    continue  # skip non-JSON lines (e.g. startup messages)
        except (BrokenPipeError, OSError):
            pass
        return None

    def _notify(self, method: str, params: Optional[dict] = None) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        if not self._process:
            return
        notif: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            notif["params"] = params
        try:
            self._process.stdin.write((json.dumps(notif) + "\n").encode("utf-8"))
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    # ── Public API ──────────────────────────────

    def start(self) -> Tuple[bool, str]:
        """
        Launch the MCP server subprocess and complete the initialize handshake.

        Returns:
            (True, "OK") on success, (False, error_message) on failure.
        """
        full_env = os.environ.copy()
        full_env.update(self._extra_env)

        # Resolve the executable so Windows .cmd wrappers (npm.cmd, npx.cmd) and
        # non-PATH Node.js installations are found correctly.
        cmd = list(self._cmd)
        if cmd:
            cmd[0] = _find_node_cmd(cmd[0])

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=full_env,
            )
        except FileNotFoundError as exc:
            executable = cmd[0] if cmd else "(unknown)"
            return False, (
                f"Command not found: {exc}\n"
                f"  Could not start '{executable}'.\n"
                "  If this is an npm/npx command, make sure Node.js is installed: https://nodejs.org\n"
                "  After installing Node.js, restart your terminal and try again."
            )
        except OSError as exc:
            return False, f"Failed to start MCP server: {exc}"

        # Start the background stdout reader so _send() can use timed reads
        # instead of blocking forever on readline().
        self._start_stdout_reader()

        # MCP initialize handshake
        init_resp = self._send("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "sapiens2", "version": SAPIENS_VERSION},
        })
        if init_resp is None:
            self.stop()
            return False, "No response to initialize request."
        if "error" in init_resp:
            self.stop()
            err = init_resp["error"]
            return False, f"initialize error: {err.get('message', str(err))}"

        # Send the required initialized notification
        self._notify("notifications/initialized")

        # Fetch tool list
        tools_resp = self._send("tools/list")
        if tools_resp and "result" in tools_resp:
            self._tools = tools_resp["result"].get("tools", [])

        return True, "OK"

    def get_tools(self) -> List[Dict]:
        """Return the list of tools exposed by this MCP server."""
        return list(self._tools)

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """
        Call a named tool on the MCP server and return the result as plain text.

        Args:
            tool_name:  Name of the tool (as reported by tools/list).
            arguments:  Dict of tool arguments (must match the tool's input schema).

        Returns:
            Human-readable result string, or an error message prefixed with [MCP Error].
        """
        resp = self._send("tools/call", {"name": tool_name, "arguments": arguments})
        if resp is None:
            return "[MCP Error] No response from server (timeout or connection lost)."
        if "error" in resp:
            err = resp["error"]
            return f"[MCP Error] {err.get('message', str(err))}"

        result = resp.get("result", {})
        content = result.get("content", [])
        parts: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")
            if item_type == "text":
                parts.append(item.get("text", ""))
            elif item_type == "resource":
                resource = item.get("resource", {})
                text = resource.get("text") or resource.get("uri", "")
                if text:
                    parts.append(str(text))
        return "\n".join(parts) if parts else "(no output)"

    def stop(self) -> None:
        """Terminate the MCP server process and stop the stdout reader thread."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self._process.kill()
                except OSError:
                    pass
            self._process = None
        # Sentinel is pushed AFTER _process is set to None so that any caller
        # that picks it up and checks self._process will see the cleared state.
        # The stdout reader daemon exits on its own once the pipe closes, but
        # the sentinel ensures a blocked _read_line() returns immediately without
        # waiting for the full MCP_READLINE_TIMEOUT_SECONDS to expire.
        self._stdout_queue.put(None)


# ─────────────────────────────────────────────
#  Module 4: MCP Discovery / Selection / Install
# ─────────────────────────────────────────────

class MCPModule:
    """
    MCP (Model Context Protocol) auto-discovery, selection, installation, and usage.

    Discovery:
      Searches GitHub for MCP server repos matching the user's goal, reads their
      README/description, and combines them with a curated baseline registry.

    Selection:
      Uses Copilot to pick the best MCP candidate based on the user's stated goal.
      Falls back to keyword matching when Copilot is not authenticated.

    Installation:
      Supports npm (via npx) and pip-based MCP servers.
      All installation steps are logged so the user can see exactly what is happening.

    Usage:
      Starts the MCP server as a subprocess and calls its tools via the JSON-RPC
      stdio protocol (MCPRunner).  Results are fed back into the agent loop.

    Persistence:
      Installed MCPs are saved to ~/.sapiens2/mcp_state.json and loaded on startup.
    """

    def __init__(self, copilot: "CopilotModule"):
        self._copilot = copilot
        self._installed: Dict[str, dict] = {}
        self._load_state()

    # ── State persistence ────────────────────────

    def _load_state(self) -> None:
        """Load persisted MCP installation state from disk."""
        if not os.path.exists(MCP_STATE_FILE):
            return
        try:
            with open(MCP_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._installed = data.get("installed", {})
        except (json.JSONDecodeError, IOError):
            pass

    def _save_state(self) -> None:
        """Persist MCP installation state to disk."""
        try:
            os.makedirs(os.path.dirname(MCP_STATE_FILE), exist_ok=True)
            with open(MCP_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"installed": self._installed}, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    # ── Installed MCP queries ────────────────────

    def list_installed(self) -> Dict[str, dict]:
        """Return all installed MCPs as {name: info}."""
        return dict(self._installed)

    def is_installed(self, name: str) -> bool:
        return name in self._installed

    def get_installed(self, name: str) -> Optional[dict]:
        return self._installed.get(name)

    # ── GitHub discovery ─────────────────────────

    def _gh_headers(self) -> Dict[str, str]:
        """Return GitHub API request headers, including auth if available."""
        headers: Dict[str, str] = {"Accept": "application/vnd.github+json"}
        token = self._copilot._github_token
        if token:
            headers["Authorization"] = f"token {token}"
        return headers

    def search_github(self, query: str) -> List[dict]:
        """
        Search GitHub for MCP-related repositories matching *query*.
        Results are sorted by stars and limited to MCP_MAX_CANDIDATES entries.

        Returns a list of partial MCP info dicts (no run_cmd yet — set by install).
        """
        try:
            resp = requests.get(
                GITHUB_SEARCH_API,
                params={
                    "q": f"{query} topic:mcp",
                    "sort": "stars",
                    "per_page": MCP_MAX_CANDIDATES,
                },
                headers=self._gh_headers(),
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except requests.RequestException:
            return []

        results = []
        for item in items:
            results.append({
                "name": item.get("name", ""),
                "full_name": item.get("full_name", ""),
                "description": item.get("description") or "",
                "stars": item.get("stargazers_count", 0),
                "url": item.get("html_url", ""),
                "default_branch": item.get("default_branch", "main"),
                "install_type": "npm",
                "run_cmd": [],
                "tags": [],
                "package": "",
            })
        return results

    def fetch_readme(self, full_name: str) -> str:
        """
        Fetch the README of a GitHub repository (up to GITHUB_README_MAX_CHARS characters).
        Returns an empty string if the README cannot be retrieved.
        """
        try:
            resp = requests.get(
                f"{GITHUB_REPOS_API}/{full_name}/readme",
                headers=self._gh_headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                raw_content = resp.json().get("content", "")
                decoded = base64.b64decode(raw_content).decode("utf-8", errors="replace")
                return decoded[:GITHUB_README_MAX_CHARS]
        except requests.RequestException:
            pass
        return ""

    # ── Copilot-based selection ──────────────────

    def select_best(self, goal: str, candidates: List[dict]) -> Optional[dict]:
        """
        Use Copilot to pick the best MCP for *goal* from *candidates*.
        Falls back to the first entry when Copilot is unavailable.

        Returns the selected candidate dict, or None if candidates is empty.
        """
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        candidates_text = "\n\n".join(
            f"[{i + 1}] {c.get('name', '?')} "
            f"({c.get('full_name') or c.get('package', '')})\n"
            f"   Description: {c.get('description', '(none)')}\n"
            f"   Tags: {', '.join(c.get('tags', []))}\n"
            f"   Stars: {c.get('stars', 'N/A')}"
            for i, c in enumerate(candidates)
        )
        prompt = (
            f"User goal: {goal}\n\n"
            f"Available MCP servers:\n{candidates_text}\n\n"
            "Which MCP server number is BEST for this goal? "
            "Reply with ONLY the number (e.g. '2'). No explanation."
        )

        if self._copilot.is_authenticated():
            result = self._copilot._call_copilot_api(
                "You are a technical assistant selecting the best MCP server for a user's goal. "
                "Be concise. Reply with only a single integer.",
                prompt,
                max_tokens=8,
            )
            if result:
                match = re.search(r"\d+", result.strip())
                if match:
                    idx = int(match.group()) - 1
                    if 0 <= idx < len(candidates):
                        return candidates[idx]

        # Fallback: word-based scoring — pick candidate with the most tag words in the goal
        goal_words = set(re.split(r"\W+", goal.lower())) - _MCP_SCORE_STOP_WORDS
        best: Optional[dict] = None
        best_score = 0
        for c in candidates:
            tags = c.get("tags", [])
            score = sum(
                1
                for tag in tags
                for tw in re.split(r"\W+", tag.lower())
                if tw and tw in goal_words
            )
            if score > best_score:
                best_score = score
                best = c

        return best if best else candidates[0]

    # ── Full auto-discovery pipeline ─────────────

    def discover_and_select(self, goal: str) -> Optional[dict]:
        """
        Full discovery pipeline:
          1. Start with the curated baseline registry.
          2. Search GitHub for additional MCP candidates matching *goal*.
          3. Fetch READMEs for GitHub results to enrich descriptions.
          4. Use Copilot (or keyword matching) to select the best candidate.

        Returns the selected MCP info dict, or None if no candidates were found.
        """
        print(f"\n[MCP] Discovering MCP servers for: {goal[:80]}...")

        # Curated baseline
        candidates: List[dict] = list(MCP_CURATED_REGISTRY)

        # GitHub search
        github_hits = self.search_github(goal)
        if github_hits:
            print(f"[MCP] GitHub search returned {len(github_hits)} result(s).")
            for hit in github_hits[:3]:
                readme = self.fetch_readme(hit.get("full_name", ""))
                if readme:
                    # Enrich description from README if API description is empty
                    if not hit.get("description"):
                        for readme_line in readme.splitlines():
                            clean = readme_line.strip("# ").strip()
                            if clean and not clean.startswith(("!", "[", "<")):
                                hit["description"] = clean[:200]
                                break
                    # Extract npm package name from README if present
                    npm_match = NPM_PACKAGE_RE.search(readme)
                    if npm_match:
                        hit["package"] = npm_match.group(1)
                candidates.append(hit)

        if not candidates:
            print("[MCP] No candidates found.")
            return None

        selected = self.select_best(goal, candidates)
        if selected:
            print(
                f"[MCP] Selected: {selected.get('name', '?')} — "
                f"{selected.get('description', '')[:80]}"
            )
        return selected

    # ── Installation ─────────────────────────────

    def install(self, mcp_info: dict) -> Tuple[bool, str]:
        """
        Install an MCP server.

        Supports install_type values:
          "npm"  — checks npm is available, then runs npm install -g <package>
                   (npx is used at runtime so a failed pre-install is non-fatal)
          "pip"  — runs pip install <package>

        All installation steps are printed so the user can see what is happening.

        Returns:
            (True, success_message) or (False, error_message)
        """
        name = mcp_info.get("name", "unknown")
        install_type = mcp_info.get("install_type", "npm")
        package = mcp_info.get("package", "")
        repo = mcp_info.get("repo") or mcp_info.get("full_name", "")

        print(f"\n[MCP] ─── Installing '{name}' ───────────────────────────")
        if repo:
            print(f"[MCP]   Repository : https://github.com/{repo}")
        print(f"[MCP]   Package    : {package or '(none)'}")
        print(f"[MCP]   Install via: {install_type}")
        print(f"[MCP]   Description: {mcp_info.get('description', '')[:80]}")

        env_hints = mcp_info.get("env_hints", {})
        if env_hints:
            print("[MCP]   Env vars needed:")
            for var, hint in env_hints.items():
                val = os.environ.get(var, "")
                status = "✓ set" if val else "✗ NOT SET"
                print(f"[MCP]     {var} [{status}] — {hint}")
        print()

        if install_type == "npm":
            # Resolve npm executable (handles Windows .cmd wrappers and non-PATH installs)
            npm_cmd = _find_node_cmd("npm")

            # Verify npm is available
            try:
                npm_check = subprocess.run(
                    [npm_cmd, "--version"], capture_output=True, text=True, timeout=10
                )
                if npm_check.returncode != 0:
                    return False, (
                        "npm returned a non-zero exit code. "
                        "Please install Node.js from https://nodejs.org"
                    )
            except FileNotFoundError:
                return False, (
                    "npm (Node.js) not found. Node.js must be installed to use npm-based MCPs.\n"
                    "  Install from: https://nodejs.org\n"
                    "  After installing, restart your terminal and try again.\n"
                    f"  (Tried: {npm_cmd!r})"
                )
            except subprocess.TimeoutExpired:
                return False, "npm availability check timed out."

            # Pre-install the package globally (speeds up first use; non-fatal on failure)
            if package:
                print(f"[MCP] Running: {npm_cmd} install -g {package}")
                try:
                    result = subprocess.run(
                        [npm_cmd, "install", "-g", package],
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    if result.returncode != 0:
                        print(
                            f"[MCP] Warning: npm install failed (npx will handle it on first use):\n"
                            f"  {result.stderr.strip()[:MCP_STDERR_MAX_CHARS]}"
                        )
                    else:
                        print(f"[MCP] npm install succeeded.")
                except subprocess.TimeoutExpired:
                    print("[MCP] Warning: npm install timed out (npx will install on first use).")
                except FileNotFoundError:
                    pass  # Already checked above

        elif install_type == "pip":
            if not package:
                return False, "No pip package name specified in MCP info."
            print(f"[MCP] Running: pip install {package}")
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", package, "-q"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0:
                    return False, f"pip install failed:\n{result.stderr.strip()[:MCP_STDERR_MAX_CHARS]}"
                print("[MCP] pip install succeeded.")
            except subprocess.TimeoutExpired:
                return False, "pip install timed out."

        else:
            return False, f"Unsupported install_type: '{install_type}'"

        # Register as installed
        entry = {
            **mcp_info,
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._installed[name] = entry
        self._save_state()
        return True, f"✅ MCP '{name}' installed and registered."

    # ── Tool enumeration ─────────────────────────

    def get_available_tools(self, mcp_name: str) -> List[dict]:
        """
        Start the named MCP server, fetch its tool list, and shut it down.
        Returns an empty list if the server cannot be started.
        """
        info = self._installed.get(mcp_name)
        if not info:
            return []
        run_cmd = info.get("run_cmd", [])
        if not run_cmd:
            return []

        runner = MCPRunner(run_cmd)
        ok, _ = runner.start()
        if not ok:
            return []
        try:
            return runner.get_tools()
        finally:
            runner.stop()

    # ── Tool execution ───────────────────────────

    def call_tool(self, mcp_name: str, tool_name: str, arguments: dict) -> str:
        """
        Start the named MCP server, call *tool_name* with *arguments*, return the result.

        The server is started fresh for each call and shut down immediately after.
        This keeps the implementation simple at the cost of some startup overhead.

        Returns:
            Tool result as a string, or an error message.
        """
        info = self._installed.get(mcp_name)
        if not info:
            return (
                f"[MCP Error] '{mcp_name}' is not installed. "
                f"Run: /mcp install {mcp_name}"
            )
        run_cmd = info.get("run_cmd", [])
        if not run_cmd:
            return f"[MCP Error] No run command configured for '{mcp_name}'."

        # Build environment — pass any required env vars from the current process
        env: Dict[str, str] = {}
        for var in info.get("env_hints", {}):
            val = os.environ.get(var, "")
            if val:
                env[var] = val
        # Always forward GITHUB_TOKEN for GitHub-related MCPs
        if os.environ.get("GITHUB_TOKEN"):
            env.setdefault("GITHUB_TOKEN", os.environ["GITHUB_TOKEN"])
        if self._copilot._github_token:
            env.setdefault("GITHUB_PERSONAL_ACCESS_TOKEN", self._copilot._github_token)

        runner = MCPRunner(run_cmd, env=env)
        ok, err_msg = runner.start()
        if not ok:
            return f"[MCP Error] Could not start '{mcp_name}': {err_msg}"
        try:
            return runner.call_tool(tool_name, arguments)
        finally:
            runner.stop()


# ─────────────────────────────────────────────
#  Module 5: Agent Core
# ─────────────────────────────────────────────

class AgentCore:
    """
    Sapiens2.0 core agent module.

    Routes user input to the appropriate module (Copilot, System, Memory).

    Memory structure:
      - Short-term: MemoryModule._short_term (session conversation history)
      - Long-term:  ~/.sapiens2/sapiens_memory.json (persists across sessions)

    State file:
      - ~/.sapiens2/sapiens_state.json: stores selected model and other settings
    """

    def __init__(self, github_token: Optional[str] = None, client_id: str = DEFAULT_CLIENT_ID):
        self.copilot = CopilotModule()
        self.system = SystemCommandModule()
        self.memory = MemoryModule()
        self.client_id = client_id

        # Tracks the active Spinner so Ctrl+C in the main loop can stop it cleanly
        self._active_spinner: Optional[Spinner] = None

        # Reasoning-intensity level (off/low/medium/high). Loaded from state file.
        self._think_level: str = THINK_DEFAULT_LEVEL

        # Persistence/retry intensity level (off/low/medium/high/max). Loaded from state file.
        self._will_level: str = WILL_DEFAULT_LEVEL

        # Load saved state (model selection, think level, etc.)
        self._load_state()

        # Token priority: explicit arg > env var > saved auth file
        if github_token:
            self.copilot.set_token(github_token)
        else:
            saved_token = _load_auth_token()
            if saved_token:
                self.copilot.set_token(saved_token)

        # MCP module — depends on copilot (for GitHub token + AI selection)
        self.mcp = MCPModule(self.copilot)

    # ── State management ────────────────────────

    def _load_state(self) -> None:
        """Load agent state (model selection, think level, etc.) from disk."""
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                if isinstance(state, dict):
                    model = state.get("model", COPILOT_DEFAULT_MODEL)
                    self.copilot.set_model(model)
                    think = state.get("think_level", THINK_DEFAULT_LEVEL)
                    if think in THINK_LEVELS:
                        self._think_level = think
                    will = state.get("will_level", WILL_DEFAULT_LEVEL)
                    if will in WILL_LEVELS:
                        self._will_level = will
            except (json.JSONDecodeError, IOError):
                pass

    def _save_state(self) -> None:
        """Persist current agent state to disk."""
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            state = {
                "model": self.copilot.get_model(),
                "think_level": self._think_level,
                "will_level": self._will_level,
            }
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"[State] Failed to save state: {e}")

    # ── Input processing ────────────────────────

    def process(self, user_input: str) -> str:
        """
        Process user input and return a response.

        Slash commands (/cmd) are handled directly.
        Plain text is routed through the agentic chat loop with computer control support.

        Args:
            user_input: Raw user input string.

        Returns:
            Agent response string. May be empty if all output was already printed inline
            during the agent tool execution loop.
        """
        raw = user_input.strip()
        if not raw:
            return ""

        # Handle slash commands
        if raw.startswith("/"):
            return self._handle_slash_command(raw)

        # Plain text → agentic Copilot chat
        if not self.copilot.is_authenticated():
            return (
                "💬 Not connected to Copilot.\n"
                "  /auth          — start GitHub device flow authentication\n"
                "                   (visit https://github.com/login/device and enter the code)\n"
                "  /auth <token>  — provide a GitHub PAT/OAuth token directly\n\n"
                "  Slash commands (/pwd, /ls, /help, etc.) work without authentication."
            )

        lt_context = self.memory.get_long_term_context()
        history = self.memory.get_short_term()

        # Add user message to short-term memory before calling the agent
        self.memory.add_message("user", raw)

        try:
            response = self._run_agent_chat(raw, history, lt_context)
        except Exception as exc:
            # Catch-all so that an unexpected exception inside the agent loop never
            # crashes the interactive session or leaves it appearing hung.
            response = f"[Error] An unexpected error occurred during response generation: {exc}"

        if response:
            self.memory.add_message("assistant", response)
            self._try_update_long_term_memory(raw, response)
            return response

        return "[Error] No response received from Copilot."

    def _try_update_long_term_memory(self, user_msg: str, assistant_response: str) -> None:
        """Extract and persist important facts from the conversation to long-term memory."""
        try:
            updates = self.copilot.extract_memory_updates(user_msg, assistant_response)
            if updates:
                self.memory.update_long_term(updates)
        except Exception:
            pass  # Non-fatal: long-term memory update failure is silently ignored

    # ── Agentic computer control loop ───────────

    def _execute_agent_tool(self, cmd_str: str) -> str:
        """
        Execute a computer control tool command issued by the agent.
        Supports read-only and safe operations; write/delete require user confirmation.

        Args:
            cmd_str: Full slash command string (e.g. '/ls .', '/cat README.md').

        Returns:
            Tool execution result as a string.  Never raises — unexpected exceptions
            are caught and returned as a structured ``[Error]`` string so the calling
            agent loop can report them to the model and (if will_level permits) retry.
        """
        try:
            return self._execute_agent_tool_inner(cmd_str)
        except Exception as exc:
            return f"[Error] Tool execution raised an unexpected exception: {exc}"

    def _execute_agent_tool_inner(self, cmd_str: str) -> str:
        """Internal implementation of tool dispatch (may raise)."""
        cmd_str = cmd_str.strip()
        parts = cmd_str.split(None, 2)
        if not parts:
            return "[Error] Empty tool command"

        cmd = parts[0].lower()
        arg1 = parts[1] if len(parts) > 1 else ""
        arg2 = parts[2] if len(parts) > 2 else ""

        if cmd == "/pwd":
            return self.system.get_cwd()
        elif cmd == "/ls":
            return self.system.list_dir(arg1 if arg1 else ".")
        elif cmd == "/cat":
            if not arg1:
                return "[Error] Usage: /cat <file>"
            return self.system.read_file(arg1)
        elif cmd == "/cd":
            if not arg1:
                return "[Error] Usage: /cd <path>"
            return self.system.change_dir(arg1)
        elif cmd == "/exec":
            full = cmd_str[len("/exec"):].strip()
            if not full:
                return "[Error] Usage: /exec <command>"
            return self.system.exec_command(full)
        elif cmd == "/run":
            if not arg1:
                return "[Error] Usage: /run <file.py>"
            return self.system.run_file(arg1)
        elif cmd == "/mcp-auto":
            # Auto-discover and install best MCP for a goal
            goal = cmd_str[len("/mcp-auto"):].strip()
            if not goal:
                return "[Error] Usage: /mcp-auto <goal description>"
            return self._do_mcp_auto(goal)
        elif cmd == "/mcp-tools":
            # List tools available in an installed MCP server.
            # This is the in-loop equivalent of the user-facing /mcp tools command
            # so the model can discover capabilities without exiting the agent loop.
            name = arg1.strip()
            if not name:
                return "[Error] Usage: /mcp-tools <mcp_name>"
            if not self.mcp.is_installed(name):
                return (
                    f"[MCP Error] '{name}' is not installed. "
                    f"Use <tool>/mcp-auto {name}</tool> to discover and install it first."
                )
            tools = self.mcp.get_available_tools(name)
            if not tools:
                return (
                    f"[MCP] No tools found for '{name}' "
                    "(server may have failed to start — check Node.js is installed)."
                )
            lines = [f"Tools available in '{name}':"]
            for t in tools:
                desc = t.get("description", "")
                lines.append(f"  • {t.get('name', '?')}: {desc[:80]}")
            return "\n".join(lines)
        elif cmd == "/mcp-call":
            # Call a tool on an installed MCP: /mcp-call <mcp_name> <tool_name> [json_args]
            rest = cmd_str[len("/mcp-call"):].strip()
            call_parts = rest.split(None, 2)
            if len(call_parts) < 2:
                return "[Error] Usage: /mcp-call <mcp_name> <tool_name> [json_args]"
            mcp_name = call_parts[0]
            tool_name = call_parts[1]
            json_args_str = call_parts[2] if len(call_parts) > 2 else "{}"
            try:
                arguments = json.loads(json_args_str)
            except json.JSONDecodeError:
                return (
                    f"[MCP Error] Invalid JSON arguments for /mcp-call: {json_args_str!r}\n"
                    "  Provide valid JSON, e.g.: {\"path\": \"README.md\"}"
                )
            return self.mcp.call_tool(mcp_name, tool_name, arguments)
        else:
            return f"[Error] Unknown tool: {cmd}"

    def _run_agent_chat(self, user_msg: str, history: list, lt_context: str) -> str:
        """
        Agentic chat loop: the model can issue computer control and MCP tool calls in its
        response using <tool>/command args</tool> tags. Tool results are fed back
        to the model, and the loop continues until the model gives a final answer
        with no more tool calls.

        The maximum number of tool-call iterations is 5 (base) plus any extra
        retries allowed by the current ``will_level`` setting.  When failures are
        detected in tool results the model is given an explicit goal-seeking hint
        so it tries alternative approaches rather than giving up.

        Intermediate tool execution output is printed inline so the user can follow
        along in real time. Only the final natural language answer is returned.

        Args:
            user_msg: Current user message.
            history: Short-term memory before this message (for context).
            lt_context: Long-term memory context string.

        Returns:
            Final model response (natural language answer to the user).
        """
        installed_mcps = self.mcp.list_installed()
        system_prompt = _build_agent_system_prompt(lt_context, installed_mcps, self._think_level)
        messages: List[Dict[str, str]] = list(history) + [{"role": "user", "content": user_msg}]

        last_response = ""
        max_tokens = THINK_MAX_TOKENS.get(self._think_level, THINK_MAX_TOKENS[THINK_DEFAULT_LEVEL])

        # Base iterations + extra allowed by will_level
        extra_retries = WILL_EXTRA_RETRIES.get(self._will_level, WILL_EXTRA_RETRIES[WILL_DEFAULT_LEVEL])
        max_turns = 5 + extra_retries
        failure_count = 0  # track consecutive tool-result failures for retry hint

        for _turn in range(max_turns):
            # Show spinner while waiting for the Copilot API response
            spinner = Spinner()
            self._active_spinner = spinner
            with spinner:
                response = self.copilot._call_copilot_api_messages(
                    system_prompt, messages, max_tokens=max_tokens
                )
            self._active_spinner = None
            if not response:
                return last_response or "[Error] No response received."

            last_response = response
            tool_calls = TOOL_CALL_RE.findall(response)

            if not tool_calls:
                # No tool calls → this is the final answer
                return response

            # Print any narrative text the model wrote around the tool calls
            clean_text = TOOL_CALL_RE.sub("", response).strip()
            if clean_text:
                print(f"[Sapiens2.0] {clean_text}")

            # Execute each tool call and collect results
            tool_results: List[str] = []
            turn_has_error = False
            for tool_cmd in tool_calls:
                tool_cmd = tool_cmd.strip()
                print(f"  ⚙  {tool_cmd}")
                result = self._execute_agent_tool(tool_cmd)
                # Print result with indentation for readability
                for line in result.splitlines():
                    print(f"     {line}")
                print()
                tool_results.append(f"[{tool_cmd}]:\n{result}")
                if result.startswith("[Error]") or result.startswith("[MCP Error]"):
                    turn_has_error = True

            # Determine if goal-seeking retry hint should be included
            if turn_has_error:
                failure_count += 1
            else:
                failure_count = 0

            will_off = (self._will_level == "off")
            retries_remaining = extra_retries - max(0, failure_count - 1)
            include_retry_hint = (
                turn_has_error
                and not will_off
                and retries_remaining > 0
                and _turn < max_turns - 1
            )

            if include_retry_hint:
                retry_instruction = (
                    "\n\nOne or more tool calls returned errors.  "
                    f"You have approximately {retries_remaining} retry attempt(s) remaining "
                    f"(will_level={self._will_level}).  "
                    "Please analyze what went wrong, consider an alternative approach, "
                    "and try again — do NOT give up yet.  "
                    "If you can achieve the user's goal a different way, do so now."
                )
            else:
                retry_instruction = (
                    "\n\nThe results above have been shown to the user. "
                    "Please provide a brief, helpful natural language response summarising "
                    "what you found or did."
                )

            # Feed results back to the model
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": (
                    "Tool execution results:\n\n"
                    + "\n\n".join(tool_results)
                    + retry_instruction
                ),
            })

        return last_response

    def _handle_slash_command(self, raw: str) -> str:
        """Parse and execute a slash command."""
        parts = raw.split(None, 2)  # Split into at most 3 tokens
        cmd = parts[0].lower()
        arg1 = parts[1] if len(parts) > 1 else ""
        arg2 = parts[2] if len(parts) > 2 else ""

        # ── Authentication ───────────────────────
        if cmd == "/auth":
            if arg1:
                # Direct token (/auth <token>)
                self.copilot.set_token(arg1)
                _save_auth_token(arg1)
                return "✅ GitHub token set and saved for future sessions."
            else:
                # OpenClaw-style GitHub device flow
                ok = self.copilot.authenticate_device_flow(self.client_id)
                return "✅ Authentication complete!" if ok else "❌ Authentication failed. Please try again."

        # ── Logout / unlink saved account ────────
        if cmd == "/logout":
            _clear_auth_token()
            self.copilot._github_token = None
            self.copilot._copilot_token = None
            return (
                "✅ Logged out. Saved GitHub account has been unlinked.\n"
                "  Run /auth to link a new account."
            )

        # ── Auth status ──────────────────────────
        if cmd == "/status":
            return self.copilot.get_status()

        # ── Model selection ──────────────────────
        if cmd == "/models":
            models = self.copilot.list_models()
            current = self.copilot.get_model()

            if arg1:
                # Select by number or name
                try:
                    idx = int(arg1) - 1
                    if 0 <= idx < len(models):
                        selected = models[idx]
                        self.copilot.set_model(selected)
                        self._save_state()
                        return f"✅ Model changed to '{selected}'."
                    return f"[Error] Invalid number. Enter a value between 1 and {len(models)}."
                except ValueError:
                    # Select by name
                    if arg1 in models:
                        self.copilot.set_model(arg1)
                        self._save_state()
                        return f"✅ Model changed to '{arg1}'."
                    return f"[Error] Model not found: {arg1}\nRun /models to see available models."

            # List models
            lines = ["Available Copilot models:"]
            for i, model in enumerate(models, 1):
                marker = "  ◀ current" if model == current else ""
                lines.append(f"  {i}. {model}{marker}")
            lines.append("\nTo select: /models <number or name>")
            if not self.copilot.is_authenticated():
                lines.append("  (after authenticating, the live model list will be fetched from the API)")
            return "\n".join(lines)

        # ── New conversation ─────────────────────
        if cmd == "/new":
            self.memory.clear_short_term()
            return "🆕 New conversation started. (Short-term memory cleared; long-term memory retained.)"

        # ── Full reset ───────────────────────────
        if cmd == "/reset":
            if not _confirm(
                "⚠️  All long-term memory, conversation history, and model settings will be cleared. Continue?"
            ):
                return "Cancelled."
            self.memory.clear_short_term()
            self.memory.clear_long_term()
            self.copilot.set_model(COPILOT_DEFAULT_MODEL)
            if os.path.exists(STATE_FILE):
                try:
                    os.remove(STATE_FILE)
                except OSError:
                    pass
            return (
                "✅ Reset complete!\n"
                f"  - Long-term memory (~/.sapiens2/sapiens_memory.json) deleted\n"
                f"  - Conversation history (short-term memory) cleared\n"
                f"  - Model reset to '{COPILOT_DEFAULT_MODEL}'"
            )

        # ── Long-term memory view ─────────────────
        if cmd == "/memory":
            display = self.memory.get_display()
            return f"[Long-Term Memory]\n{display}"

        # ── File system ──────────────────────────
        if cmd == "/pwd":
            return f"Working directory: {self.system.get_cwd()}"

        if cmd == "/cd":
            if not arg1:
                return "[Error] Usage: /cd <path>"
            return self.system.change_dir(arg1)

        if cmd == "/ls":
            return self.system.list_dir(arg1 if arg1 else ".")

        if cmd == "/cat":
            if not arg1:
                return "[Error] Usage: /cat <file>"
            return self.system.read_file(arg1)

        if cmd == "/write":
            if not arg1:
                return "[Error] Usage: /write <file> [content]"
            content = arg2
            if not content:
                print(f"  Enter content for '{arg1}' (type EOF on its own line to finish):")
                lines = []
                while True:
                    line = _safe_input()
                    if line is None or line == "EOF":
                        break
                    lines.append(line)
                content = "\n".join(lines)
            return self.system.write_file(arg1, content)

        if cmd == "/rm":
            if not arg1:
                return "[Error] Usage: /rm <file>"
            return self.system.delete_file(arg1)

        # ── Execution ────────────────────────────
        if cmd == "/run":
            if not arg1:
                return "[Error] Usage: /run <file>"
            return self.system.run_file(arg1)

        if cmd == "/exec":
            if not arg1:
                return "[Error] Usage: /exec <command>"
            full_cmd = raw[len("/exec "):].strip()
            return self.system.exec_command(full_cmd)

        # ── Code generation ──────────────────────
        if cmd == "/codegen":
            if not self.copilot.is_authenticated():
                return "❌ Copilot authentication required. Run /auth first."

            codegen_args = raw[len("/codegen"):].strip()
            lang = "python"
            if "--lang" in codegen_args:
                lang_parts = codegen_args.split("--lang", 1)
                codegen_args = lang_parts[0].strip()
                lang_value = lang_parts[1].strip().split()[0] if lang_parts[1].strip() else ""
                if lang_value:
                    lang = lang_value

            if not codegen_args:
                return "[Error] Usage: /codegen <description> [--lang <language>]"

            print(f"  Requesting {lang} code from Copilot...")
            code = self.copilot.generate_code(codegen_args, language=lang)
            if code:
                return f"Generated code:\n\n{code}"
            return "❌ Code generation failed."

        # ── Help ─────────────────────────────────
        if cmd in ("/help", "/?"):
            return _help_text()

        # ── Think level ──────────────────────────
        if cmd == "/think":
            return self._handle_think_command(arg1)

        # ── Will level ───────────────────────────
        if cmd == "/will":
            return self._handle_will_command(arg1)

        # ── MCP commands ─────────────────────────
        if cmd == "/mcp":
            return self._handle_mcp_command(arg1, arg2, raw)

        # ── Update ───────────────────────────────
        if cmd == "/update":
            return self._do_update()

        # ── Exit ─────────────────────────────────
        if cmd in ("/exit", "/quit", "/q"):
            print("Goodbye! 👋")
            sys.exit(0)

        return f"[Error] Unknown command: {cmd}\nType /help to see all available commands."

    def _handle_think_command(self, level_arg: str) -> str:
        """
        Handle the /think command.

        /think           — show current level and available options
        /think <level>   — set reasoning intensity (off / low / medium / high)
        """
        if not level_arg:
            lines = [
                f"Current think level: {self._think_level}  "
                f"(max tokens: {THINK_MAX_TOKENS[self._think_level]})",
                "",
                "Available levels:",
            ]
            descriptions = {
                "off":    "Fastest replies, minimal reasoning. Best for simple/quick questions.",
                "low":    "Concise answers with brief reasoning.",
                "medium": "Balanced reasoning and response length. (default)",
                "high":   "Thorough, step-by-step analysis. Best for complex problems.",
            }
            for lvl in THINK_LEVELS:
                marker = "  ◀ current" if lvl == self._think_level else ""
                lines.append(
                    f"  {lvl:<8} (max_tokens={THINK_MAX_TOKENS[lvl]:<5}) "
                    f"— {descriptions[lvl]}{marker}"
                )
            lines.append("")
            lines.append("To change: /think <level>   e.g. /think high")
            return "\n".join(lines)

        lvl = level_arg.strip().lower()
        if lvl not in THINK_LEVELS:
            valid = " / ".join(THINK_LEVELS)
            return (
                f"[Error] Unknown think level: '{level_arg}'\n"
                f"  Valid levels: {valid}\n"
                f"  Example: /think medium"
            )
        self._think_level = lvl
        self._save_state()
        return (
            f"✅ Think level set to '{lvl}' "
            f"(max_tokens={THINK_MAX_TOKENS[lvl]}). "
            f"Setting saved."
        )

    def _handle_will_command(self, level_arg: str) -> str:
        """
        Handle the /will command.

        /will           — show current level and available options
        /will <level>   — set persistence/retry intensity (off / low / medium / high / max)
        """
        if not level_arg:
            lines = [
                f"Current will level: {self._will_level}  "
                f"(extra retries after failure: {WILL_EXTRA_RETRIES[self._will_level]})",
                "",
                "Available levels:",
            ]
            descriptions = {
                "off":    "No retry — stop on first tool failure.",
                "low":    "1 extra retry after failure; brief error analysis.",
                "medium": "2 extra retries; agent analyzes errors and tries alternatives. (default)",
                "high":   "3 extra retries; persistent goal-seeking with error analysis.",
                "max":    "5 extra retries; maximum persistence toward the user's goal.",
            }
            for lvl in WILL_LEVELS:
                marker = "  ◀ current" if lvl == self._will_level else ""
                lines.append(
                    f"  {lvl:<8} (extra_retries={WILL_EXTRA_RETRIES[lvl]:<2}) "
                    f"— {descriptions[lvl]}{marker}"
                )
            lines.append("")
            lines.append("To change: /will <level>   e.g. /will high")
            return "\n".join(lines)

        lvl = level_arg.strip().lower()
        if lvl not in WILL_LEVELS:
            valid = " / ".join(WILL_LEVELS)
            return (
                f"[Error] Unknown will level: '{level_arg}'\n"
                f"  Valid levels: {valid}\n"
                f"  Example: /will medium"
            )
        self._will_level = lvl
        self._save_state()
        return (
            f"✅ Will level set to '{lvl}' "
            f"(extra_retries={WILL_EXTRA_RETRIES[lvl]}). "
            f"Setting saved."
        )

    def _handle_mcp_command(self, sub: str, rest: str, raw: str) -> str:
        """
        Handle all /mcp sub-commands.

        Sub-commands:
          /mcp                      — show installed MCPs
          /mcp list                 — list curated + installed MCPs
          /mcp auto <goal>          — auto-discover, select, and install best MCP for goal
          /mcp install <name>       — install a curated MCP by name
          /mcp tools <name>         — list tools exposed by an installed MCP
          /mcp call <n> <t> [args]  — call tool <t> on MCP <n> with optional JSON args
        """
        sub_lower = sub.lower() if sub else ""

        # /mcp  or  /mcp list — show installed + curated
        if sub_lower in ("", "list"):
            installed = self.mcp.list_installed()
            lines: List[str] = []

            if installed:
                lines.append("Installed MCP servers:")
                for name, info in installed.items():
                    lines.append(
                        f"  ✅ {name:<20} {info.get('description', '')[:60]}"
                    )
            else:
                lines.append("No MCP servers installed yet.")

            lines.append("")
            lines.append("Curated MCP registry (ready to install):")
            for entry in MCP_CURATED_REGISTRY:
                marker = "  ✅" if entry["name"] in installed else "    "
                lines.append(
                    f"{marker} {entry['name']:<20} {entry['description'][:60]}"
                )
            lines.append("")
            lines.append("Commands:")
            lines.append("  /mcp auto <goal>       — auto-discover and install the best MCP")
            lines.append("  /mcp install <name>    — install a curated MCP by name")
            lines.append("  /mcp tools <name>      — list tools in an installed MCP")
            lines.append("  /mcp call <n> <t> [j]  — call a tool directly")
            return "\n".join(lines)

        # /mcp auto <goal>
        if sub_lower == "auto":
            goal = rest.strip()
            if not goal:
                return "[Error] Usage: /mcp auto <goal description>"
            return self._do_mcp_auto(goal)

        # /mcp install <name>
        if sub_lower == "install":
            name = rest.strip().split()[0] if rest.strip() else ""
            if not name:
                return "[Error] Usage: /mcp install <name>"
            return self._do_mcp_install_by_name(name)

        # /mcp tools <name>
        if sub_lower == "tools":
            name = rest.strip().split()[0] if rest.strip() else ""
            if not name:
                return "[Error] Usage: /mcp tools <mcp_name>"
            if not self.mcp.is_installed(name):
                return f"[Error] '{name}' is not installed. Run: /mcp install {name}"
            print(f"[MCP] Starting '{name}' to fetch tool list...")
            tools = self.mcp.get_available_tools(name)
            if not tools:
                return f"[MCP] Could not retrieve tools for '{name}' (server may have failed to start)."
            lines = [f"Tools available in '{name}':"]
            for t in tools:
                desc = t.get("description", "")
                lines.append(f"  • {t.get('name', '?'):<30} {desc[:60]}")
            return "\n".join(lines)

        # /mcp call <mcp_name> <tool_name> [json_args]
        if sub_lower == "call":
            call_parts = rest.strip().split(None, 2)
            if len(call_parts) < 2:
                return "[Error] Usage: /mcp call <mcp_name> <tool_name> [json_args]"
            mcp_name = call_parts[0]
            tool_name = call_parts[1]
            json_str = call_parts[2] if len(call_parts) > 2 else "{}"
            try:
                arguments = json.loads(json_str)
            except json.JSONDecodeError:
                return f"[Error] Invalid JSON arguments: {json_str}"
            if not self.mcp.is_installed(mcp_name):
                return f"[Error] '{mcp_name}' is not installed. Run: /mcp install {mcp_name}"
            print(f"[MCP] Calling {mcp_name}/{tool_name}...")
            result = self.mcp.call_tool(mcp_name, tool_name, arguments)
            return result

        return (
            f"[Error] Unknown /mcp sub-command: '{sub}'\n"
            "  Run /mcp for usage information."
        )

    def _do_mcp_auto(self, goal: str) -> str:
        """
        Auto-discover, select, and install the best MCP for *goal*.
        Called both from /mcp auto and from the agent tool <tool>/mcp-auto goal</tool>.
        """
        selected = self.mcp.discover_and_select(goal)
        if not selected:
            return (
                "[MCP] No suitable MCP found for this goal.\n"
                "  You can browse MCPs at https://github.com/topics/mcp and install one "
                "manually with: /mcp install <name>"
            )

        name = selected.get("name", "unknown")

        # Already installed? Skip install step.
        if self.mcp.is_installed(name):
            return (
                f"[MCP] '{name}' is already installed and ready.\n"
                f"  Description: {selected.get('description', '')}\n"
                f"  Run /mcp tools {name} to see available tools."
            )

        ok, msg = self.mcp.install(selected)
        if not ok:
            return f"[MCP] Installation failed: {msg}"

        env_hints = selected.get("env_hints", {})
        missing_vars = [v for v in env_hints if not os.environ.get(v)]

        lines = [msg]
        if missing_vars:
            lines.append("")
            lines.append("⚠️  The following environment variables may be required:")
            for var in missing_vars:
                lines.append(f"  {var} — {env_hints[var]}")
            lines.append("  Set them before calling tools from this MCP.")
        lines.append(f"\nRun /mcp tools {name} to see what tools are now available.")
        return "\n".join(lines)

    def _do_mcp_install_by_name(self, name: str) -> str:
        """Install a named MCP from the curated registry."""
        # Find in curated registry (case-insensitive)
        registry_entry = next(
            (e for e in MCP_CURATED_REGISTRY if e["name"].lower() == name.lower()),
            None,
        )
        if not registry_entry:
            known = ", ".join(e["name"] for e in MCP_CURATED_REGISTRY)
            return (
                f"[Error] '{name}' not found in the curated registry.\n"
                f"  Known names: {known}\n"
                f"  For GitHub discovery: /mcp auto <goal description>"
            )

        if self.mcp.is_installed(registry_entry["name"]):
            return f"[MCP] '{registry_entry['name']}' is already installed."

        ok, msg = self.mcp.install(registry_entry)
        if not ok:
            return f"[MCP] Installation failed: {msg}"

        env_hints = registry_entry.get("env_hints", {})
        missing_vars = [v for v in env_hints if not os.environ.get(v)]
        lines = [msg]
        if missing_vars:
            lines.append("")
            lines.append("⚠️  This MCP requires environment variables:")
            for var in missing_vars:
                lines.append(f"  {var} — {env_hints[var]}")
        lines.append(f"\nRun /mcp tools {registry_entry['name']} to see available tools.")
        return "\n".join(lines)

    def _do_update(self) -> str:
        """
        Update Sapiens2.0 to the latest code by running git pull in the
        installation directory, then re-running pip install to pick up any
        new dependencies.

        Returns:
            Status message describing what happened.
        """
        script_dir = os.path.dirname(os.path.abspath(__file__))

        if not os.path.isdir(os.path.join(script_dir, ".git")):
            return (
                "[Update] Cannot auto-update: the installation directory is not a git repository.\n"
                f"  Location: {script_dir}\n"
                "  To update manually, download the latest code from GitHub and\n"
                "  run: pip install -e ."
            )

        print("[Update] Pulling latest code from GitHub...")
        try:
            git_result = subprocess.run(
                ["git", "pull"],
                capture_output=True,
                text=True,
                cwd=script_dir,
                timeout=60,
            )
        except FileNotFoundError:
            return (
                "[Update] git command not found.\n"
                "  Please install Git (https://git-scm.com) or update manually:\n"
                "  1. Download the latest code from GitHub\n"
                "  2. Run: pip install -e ."
            )
        except subprocess.TimeoutExpired:
            return "[Update] git pull timed out. Check your network connection and try again."

        pull_parts = []
        if git_result.stdout.strip():
            pull_parts.append(git_result.stdout.strip())
        if git_result.stderr.strip():
            pull_parts.append(git_result.stderr.strip())
        pull_text = "\n".join(pull_parts) if pull_parts else "Already up to date."

        if git_result.returncode != 0:
            return f"[Update] git pull failed:\n{pull_text}"

        print("[Update] Refreshing dependencies (pip install -e .) ...")
        try:
            pip_result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", ".", "-q"],
                capture_output=True,
                text=True,
                cwd=script_dir,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            pip_result = None

        lines = [f"[Update] {pull_text}"]
        if pip_result is None:
            lines.append("[Update] Warning: pip install timed out; dependencies may be out of date.")
        elif pip_result.returncode != 0 and pip_result.stderr.strip():
            lines.append(
                f"[Update] Warning: pip install reported issues:\n  {pip_result.stderr.strip()}"
            )
        lines.append(
            "\n[OK] Update complete! Restart Sapiens2.0 to apply any changes.\n"
            "     Run: sapiens wakeup"
        )
        return "\n".join(lines)


# ─────────────────────────────────────────────
#  Utility functions
# ─────────────────────────────────────────────

def _confirm(message: str) -> bool:
    """
    Ask the user for a yes/no confirmation.

    Uses _safe_input() so it works correctly whether or not the background
    stdin reader thread is active.

    Returns:
        True if the user confirms (y/yes), False otherwise.
    """
    try:
        answer = _safe_input(f"{message} [y/N] ")
        if answer is None:
            return False
        return answer.strip().lower() in ("y", "yes")
    except KeyboardInterrupt:
        return False


def _run_subprocess(cmd: Union[str, List[str]], cwd: str = ".", shell: bool = False, timeout: int = 30) -> str:
    """
    Run a subprocess and return its combined stdout/stderr output.

    Args:
        cmd: Command to run (string or list).
        cwd: Working directory.
        shell: If True, run via the system shell.
        timeout: Maximum seconds to wait.

    Returns:
        Combined stdout + stderr string.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            shell=shell,
            timeout=timeout,
        )
        output_parts = []
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if stdout:
            output_parts.append(stdout)
        if stderr:
            output_parts.append(f"[stderr]\n{stderr}")
        if result.returncode != 0:
            output_parts.append(f"[exit code: {result.returncode}]")

        return "\n".join(output_parts) if output_parts else "(no output)"

    except subprocess.TimeoutExpired:
        return f"[Error] Command timed out ({timeout}s)"
    except FileNotFoundError as e:
        return f"[Error] Command or file not found: {e}"
    except PermissionError as e:
        return f"[Error] Permission denied: {e}"
    except Exception as e:  # catch-all so callers always get a string, never an exception
        return f"[Error] Unexpected error running command: {e}"


def _build_agent_system_prompt(
    lt_context: str = "",
    installed_mcps: Optional[Dict[str, dict]] = None,
    think_level: str = THINK_DEFAULT_LEVEL,
) -> str:
    """
    Build the system prompt for the agentic chat loop.
    Explicitly informs the model of its computer control capabilities, MCP tools,
    and the tool-call format it must use to actually execute commands.

    Args:
        lt_context:     Long-term memory context string.
        installed_mcps: Dict of installed MCP servers.
        think_level:    Current reasoning-intensity level (off/low/medium/high).
    """
    parts = [
        "You are Sapiens2.0, an AI agent with DIRECT computer control capabilities.\n"
        "\n"
        "IMPORTANT: You can control the user's computer right now. When asked to inspect "
        "files, navigate directories, run commands, or perform any local task, you MUST "
        "use the tools below — do not just describe what to do, ACTUALLY DO IT.\n"
        "\n"
        "HOW TO USE TOOLS:\n"
        "Include tool calls in your response using this exact format:\n"
        "  <tool>/command args</tool>\n"
        "These are executed automatically; results are fed back to you.\n"
        "\n"
        "COMPUTER CONTROL TOOLS:\n"
        "  <tool>/pwd</tool>                   — show current working directory\n"
        "  <tool>/ls</tool>                    — list current directory contents\n"
        "  <tool>/ls path/to/dir</tool>        — list a specific directory\n"
        "  <tool>/cat filename</tool>           — read a file's contents\n"
        "  <tool>/cd path/to/dir</tool>         — change working directory\n"
        "  <tool>/exec shell_command</tool>     — run any shell/PowerShell command\n"
        "  <tool>/run script.py</tool>          — execute a Python script\n"
        "\n"
        "MCP AUTO-DISCOVERY TOOL:\n"
        "  <tool>/mcp-auto goal description</tool>\n"
        "  — Searches GitHub for the best MCP server for the goal, installs it, and\n"
        "    reports what tools are now available. Use this when you need external\n"
        "    capabilities (web search, browser, database, etc.) not yet installed.\n"
        "\n"
        "MCP TOOL LIST (after installation):\n"
        "  <tool>/mcp-tools mcp_name</tool>\n"
        "  — List the tools available in an installed MCP server.\n"
        "    Use this right after installation to see what tools you can call.\n"
        "\n"
        "MCP TOOL CALL (after installation):\n"
        "  <tool>/mcp-call mcp_name tool_name {\"arg\": \"value\"}</tool>\n"
        "  — Calls a tool on an installed MCP server. The JSON arguments must be valid.\n"
        "\n"
        "COMPUTER CONTROL EXAMPLES:\n"
        "  User: 'what files are in this folder?' → <tool>/ls</tool>\n"
        "  User: 'show me the README'             → <tool>/cat README.md</tool>\n"
        "  User: 'what Python version?'           → <tool>/exec python --version</tool>\n"
        "  User: 'run the tests'                  → <tool>/exec python -m pytest</tool>\n"
        "  User: 'go to my Desktop'               → <tool>/cd ~/Desktop</tool>\n"
        "\n"
        "RULES:\n"
        "1. For any task involving files, directories, or commands, ALWAYS use the tools.\n"
        "2. You may chain multiple tool calls in one response.\n"
        "3. File write and delete operations (/write, /rm) require user confirmation;\n"
        "   they cannot be executed automatically from within the agent loop.\n"
        "4. Always show what you are doing before doing it.\n"
        "5. After seeing tool results, give a concise natural language summary.\n"
        "6. Answer in the same language the user writes in.\n"
        "7. If the user needs external capabilities (web search, database access, browser\n"
        "   automation, etc.) and no MCP is installed, use /mcp-auto to install one.\n"
        "8. NEVER run interactive, long-running, or non-terminating commands with /exec\n"
        "   (e.g. npx @modelcontextprotocol/inspector, server start scripts, watch modes,\n"
        "   interactive REPLs).  Such commands block indefinitely and freeze the agent.\n"
        "   Use /mcp-auto and /mcp-call instead of running MCP servers directly.",
    ]

    # Append think-level instruction when it carries a non-empty suffix
    think_suffix = THINK_PROMPT_SUFFIX.get(think_level, "")
    if think_suffix:
        parts.append(f"\nTHINKING STYLE: {think_suffix}")

    # Include installed MCP info if any servers are available
    if installed_mcps:
        mcp_lines = ["\nINSTALLED MCP SERVERS (external tool capabilities):"]
        for mcp_name, info in installed_mcps.items():
            desc = info.get("description", "")
            mcp_lines.append(f"  • {mcp_name}: {desc[:80]}")
        mcp_lines.append(
            "\nTo call an MCP tool: <tool>/mcp-call mcp_name tool_name {\"arg\": \"value\"}</tool>"
        )
        mcp_lines.append(
            "To list tools in an MCP: <tool>/mcp-tools mcp_name</tool>"
        )
        parts.append("\n".join(mcp_lines))

    if lt_context:
        parts.append(f"\n{lt_context}")

    return "\n".join(parts)


def _help_text() -> str:
    """Return the help text string."""
    return textwrap.dedent("""\
        ╔══════════════════════════════════════════════════╗
        ║              Sapiens2.0  —  Help                 ║
        ╚══════════════════════════════════════════════════╝

        INSTALLATION (run once):
          pip install -e .           Install — makes 'sapiens' command available globally
          sapiens wakeup             Start Sapiens2.0 from any terminal/PowerShell window

        AUTHENTICATION (GitHub device flow — same as OpenClaw):
          /auth                Start GitHub device flow (recommended)
                               → note the code shown, visit https://github.com/login/device
                                 and enter it to approve; your account is saved automatically
          /auth <token>        Provide a GitHub PAT/OAuth token directly (also saved)
          /logout              Unlink the saved GitHub account
          /status              Show current auth and token status

        MODEL SELECTION:
          /models              List available Copilot models
          /models <number>     Select model by number  (e.g. /models 2)
          /models <name>       Select model by name    (e.g. /models gpt-4o-mini)

        THINK LEVEL (reasoning intensity):
          /think               Show current level and all options
          /think off           Fastest, minimal reasoning  (max_tokens=512)
          /think low           Concise answers             (max_tokens=1024)
          /think medium        Balanced  (default)         (max_tokens=2048)
          /think high          Deep, thorough analysis     (max_tokens=4096)
          The setting is saved to ~/.sapiens2/sapiens_state.json and
          persists across restarts and updates.

        WILL LEVEL (persistence / retry intensity):
          /will                Show current level and all options
          /will off            No retry — fail immediately on first tool error
          /will low            1 extra retry with error analysis
          /will medium         2 extra retries  (default)
          /will high           3 extra retries; agent analyzes failures and tries alternatives
          /will max            5 extra retries; maximum persistence toward the user's goal
          When a tool call fails and will_level > off, Sapiens analyzes the error
          and tries a different approach instead of giving up immediately.
          The setting is saved to ~/.sapiens2/sapiens_state.json and
          persists across restarts and updates.

        CONVERSATION & MEMORY:
          /new                 Start a new conversation (clears short-term memory; long-term kept)
          /reset               Full reset: clears long-term memory + model settings (with confirm)
          /memory              Display current long-term memory contents

        COMPUTER CONTROL (direct commands):
          /pwd                 Show current working directory
          /cd <path>           Change working directory
          /ls [path]           List directory contents
          /cat <file>          Read and display a file
          /write <file> [text] Write to a file (prompts for content if text omitted)
          /rm <file>           Delete a file (requires confirmation)
          /run <file>          Execute a Python script
          /exec <cmd>          Run a shell/PowerShell command

        COMPUTER CONTROL (via conversation):
          Just ask naturally! The agent will automatically use the tools above.
          Examples:
            "what files are in this folder?"
            "show me the contents of README.md"
            "what Python version am I running?"
            "run the tests"
            "create a file called hello.py that prints Hello World"

        CODE GENERATION:
          /codegen <description> [--lang <language>]
                               Generate code using Copilot (default language: python)

        MCP — MODEL CONTEXT PROTOCOL (auto-discovery & external tools):
          /mcp                 Show installed MCP servers
          /mcp list            List curated + installed MCPs
          /mcp auto <goal>     Discover, select, and install the best MCP for your goal
                               Example: /mcp auto "I need to search the web"
                               Example: /mcp auto "I need to browse a website"
                               Example: /mcp auto "I need to query a SQLite database"
          /mcp install <name>  Install a curated MCP by name (e.g. /mcp install sqlite)
          /mcp tools <name>    List tools available in an installed MCP
          /mcp call <n> <t> [j]
                               Call tool <t> on MCP <n> with optional JSON args
                               Example: /mcp call sqlite query {"sql": "SELECT * FROM t"}

          The agent also uses MCPs automatically — just describe what you need:
            "search the web for the latest Python release"
            "open https://example.com and take a screenshot"
            "query my users.db for all active users"
          The agent will install the right MCP if needed and then call its tools.

          Curated MCPs (always available):
            filesystem    — read/write/list local files and directories
            github        — search GitHub repos, issues, code (needs GITHUB_PERSONAL_ACCESS_TOKEN)
            brave-search  — web search (needs BRAVE_API_KEY)
            sqlite        — SQLite database operations
            puppeteer     — browser automation and screenshots
            fetch         — HTTP fetch from URLs and APIs

          MCP state is persisted to: ~/.sapiens2/mcp_state.json

        THINKING INDICATOR:
          While Sapiens2.0 is preparing a response a spinning indicator
            [Sapiens2.0] Thinking |
          is shown on one line.  It stops and the line is cleared the moment
          the response starts printing, so it never interferes with output.

        QUEUED INPUT (type while the agent is responding):
          You do NOT need to wait for a response before typing your next message.
          A background thread reads stdin continuously, so any text you type while
          the agent is working is queued and processed the moment the current
          response finishes.  A hint is shown at the start of each response:
            ⏳ Thinking... (you can type your next message now — it will be queued)

        CANCEL RESPONSE (force-stop):
          Press Ctrl+C while Sapiens2.0 is generating a response to cancel it.
          The current request is stopped and you are returned to the prompt.
          Slash commands (/pwd, /ls, etc.) are not affected.

        DISCORD BOT MODE:
          Run Sapiens2.0 as a Discord bot:
            sapiens wakeup --discord --discord-token <BOT_TOKEN>
          Or set the env var and omit the flag:
            set DISCORD_BOT_TOKEN=<BOT_TOKEN>
            sapiens wakeup --discord

          The bot responds to:
            - Direct messages (DMs)
            - @mentions in server channels

          See README.md for full setup instructions.

        OTHER:
          /update              Update Sapiens2.0 to the latest code (git pull + pip install)
          /will [level]        View or set retry/persistence intensity (off/low/medium/high/max)
          /help  or  /?        Show this help text
          /exit  or  /quit     Exit Sapiens2.0

        MEMORY SYSTEM:
          Short-term : Current session conversation history.
                       Cleared on /new or exit.
          Long-term  : ~/.sapiens2/sapiens_memory.json — persists across sessions.
                       The agent automatically extracts and saves important facts.
                       Cleared only on /reset.

        PERSISTENT AUTH:
          After your first /auth, your GitHub account is saved to ~/.sapiens2/config.json.
          The next time you run 'sapiens wakeup', you are automatically logged in —
          no need to re-authenticate unless you run /logout.
    """)



# ─────────────────────────────────────────────
#  Discord Bot Integration
# ─────────────────────────────────────────────

# Config key used in ~/.sapiens2/config.json to persist the Discord token
_DISCORD_CONFIG_KEY = "discord_bot_token"


def _save_discord_token(token: str) -> None:
    """Persist the Discord bot token alongside the GitHub auth token."""
    try:
        os.makedirs(AUTH_CONFIG_DIR, exist_ok=True)
        cfg: Dict[str, str] = {}
        if os.path.exists(AUTH_CONFIG_FILE):
            try:
                with open(AUTH_CONFIG_FILE, "r", encoding="utf-8") as fh:
                    cfg = json.load(fh)
            except (json.JSONDecodeError, OSError):
                cfg = {}
        cfg[_DISCORD_CONFIG_KEY] = token
        with open(AUTH_CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
    except OSError:
        pass


def _load_discord_token() -> Optional[str]:
    """Load a previously saved Discord bot token from config."""
    try:
        with open(AUTH_CONFIG_FILE, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        token = cfg.get(_DISCORD_CONFIG_KEY, "")
        return token if token else None
    except (OSError, json.JSONDecodeError, KeyError):
        return None


class DiscordBot:
    """
    Discord bot integration for Sapiens2.0.

    Allows Sapiens2.0 to be used through Discord, similar in spirit to the
    OpenClaw Discord integration.  The bot uses the same AgentCore instance
    as the terminal so all settings, memory, and authentication are shared.

    Activation:
      sapiens wakeup --discord --discord-token <BOT_TOKEN>

    Or set the environment variable first:
      set DISCORD_BOT_TOKEN=<BOT_TOKEN>          # Windows PowerShell
      export DISCORD_BOT_TOKEN=<BOT_TOKEN>       # bash / macOS

    The bot responds to:
      - Direct messages (DMs) — always.
      - Server channel messages where the bot is @mentioned.

    Each Discord channel maintains its own independent short-term memory so
    conversations in different channels do not bleed into each other.
    The shared long-term memory and settings are updated as normal.

    Discord message length limit (2 000 chars) is handled automatically by
    splitting long responses into consecutive messages.
    """

    _MAX_CHARS = 1990  # Discord hard limit is 2000; leave a small buffer

    def __init__(self, agent: "AgentCore", token: str) -> None:
        self._agent = agent
        self._token = token
        # Per-channel short-term memory so each channel has its own context
        self._channel_memory: Dict[int, MemoryModule] = {}

    # ── Public ───────────────────────────────

    def run(self) -> None:
        """Start the Discord bot (blocking)."""
        if not _DISCORD_AVAILABLE:
            print(
                "[Discord] discord.py is not installed.\n"
                "  Install it with:  pip install discord.py\n"
                "  Then run:         sapiens wakeup --discord"
            )
            return

        if not self._token:
            print(
                "[Discord] No bot token provided.\n"
                "  Pass it with --discord-token <TOKEN>\n"
                "  or set the DISCORD_BOT_TOKEN environment variable."
            )
            return

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready() -> None:  # type: ignore[misc]
            print(f"[Discord] Sapiens2.0 online as {client.user}")
            if client.user is not None:
                print(
                    f"[Discord] Add the bot to a server:\n"
                    f"  https://discord.com/api/oauth2/authorize"
                    f"?client_id={client.user.id}&permissions=2147483648&scope=bot"
                )

        @client.event
        async def on_message(message: "discord.Message") -> None:  # type: ignore[misc]
            # Never respond to the bot's own messages
            if message.author == client.user:
                return

            is_dm = isinstance(message.channel, discord.DMChannel)
            is_mentioned = client.user in message.mentions if client.user else False

            if not (is_dm or is_mentioned):
                return

            # Strip the bot mention so only the user's actual text is processed
            content = message.content
            if client.user is not None:
                content = content.replace(f"<@{client.user.id}>", "").strip()
                content = content.replace(f"<@!{client.user.id}>", "").strip()

            if not content:
                await message.reply(
                    "Hello! How can I help you?  "
                    "Type a message or use `/help` to see available commands."
                )
                return

            # Swap short-term memory to the per-channel memory so each channel
            # maintains its own conversation context
            channel_id = message.channel.id
            if channel_id not in self._channel_memory:
                self._channel_memory[channel_id] = MemoryModule()
            original_memory = self._agent.memory
            self._agent.memory = self._channel_memory[channel_id]

            try:
                # Show Discord typing indicator while the agent is working
                async with message.channel.typing():
                    # Run the blocking agent call in a thread pool so the
                    # Discord event loop remains responsive
                    loop = asyncio.get_event_loop()
                    response = await loop.run_in_executor(
                        None, self._agent.process, content
                    )
            finally:
                # Restore the original terminal memory
                self._agent.memory = original_memory

            if not response:
                return

            # Split response into chunks that fit within Discord's message limit
            chunks = [
                response[i:i + self._MAX_CHARS]
                for i in range(0, len(response), self._MAX_CHARS)
            ]
            for idx, chunk in enumerate(chunks):
                if idx == 0:
                    await message.reply(chunk)
                else:
                    await message.channel.send(chunk)

        try:
            client.run(self._token, log_handler=None)
        except discord.LoginFailure:
            print(
                "[Discord] Login failed — check that the bot token is correct.\n"
                "  Create or copy your token at https://discord.com/developers/applications"
            )
        except Exception as exc:
            print(f"[Discord] Unexpected error: {exc}")


def _run_discord_bot(agent: "AgentCore", token: Optional[str]) -> None:
    """
    Resolve the Discord token from the provided value, env var, or saved config,
    then start the bot.
    """
    resolved = token or os.environ.get("DISCORD_BOT_TOKEN") or _load_discord_token()
    if resolved and token:
        # Persist a freshly supplied token for future runs
        _save_discord_token(resolved)
    bot = DiscordBot(agent=agent, token=resolved or "")
    bot.run()


# ─────────────────────────────────────────────
#  Entry points
# ─────────────────────────────────────────────

def _print_banner(agent: "AgentCore") -> None:
    """Print the Sapiens2.0 startup banner (ASCII-only for PowerShell compatibility)."""

    # Inner content width (characters between the | borders).
    # All content strings are padded/truncated to exactly this width.
    INNER = 60

    def _bline(content: str = "") -> str:
        """Return one box content line, padded to INNER chars."""
        return "  |" + content.ljust(INNER) + "|"

    border = "  +" + "=" * INNER + "+"

    # ASCII art for "Sapiens" — trailing spaces intentionally stripped;
    # _bline() pads every line to exactly INNER characters via ljust().
    logo = [
        "   ____             _                 ____   ___",
        "  / ___|  __ _ _ __|_) ___ _ __  ___ |___ \\ / _ \\",
        "  \\___ \\ / _` | '_ \\ |/ _ \\ '_ \\/ __|  __) | | |",
        "   ___) | (_| | |_) | |  __/ | | \\__ \\ / __/| |_|",
        "  |____/ \\__,_| .__/|_|\\___|_| |_|___/|_____|\\___/  2.0",
        "              |_|",
    ]

    print()
    print(border)
    print(_bline())
    for art_line in logo:
        print(_bline(art_line))
    print(_bline())
    print(_bline("  AI Agent  *  Computer Control  *  Long-Term Memory"))
    print(_bline("  GitHub Copilot powered"))
    print(_bline())
    print(border)
    print()

    # Auth status (plain text, no emoji that may misrender in PowerShell)
    if agent.copilot.is_authenticated():
        saved = os.path.exists(AUTH_CONFIG_FILE)
        note = "  (saved -- auto-login enabled)" if saved else ""
        print(f"  [OK] GitHub account linked{note}")
    else:
        print("  [i]  Not authenticated. Type /auth to link your GitHub account.")
        print("       Visit https://github.com/login/device and enter the code shown.")

    lt_mem = agent.memory.get_long_term()
    if lt_mem:
        print(f"  [M]  Long-term memory: {len(lt_mem)} entries loaded  (/memory to view)")

    installed_mcps = agent.mcp.list_installed()
    if installed_mcps:
        names = ", ".join(installed_mcps.keys())
        print(f"  [MCP] {len(installed_mcps)} MCP server(s) ready: {names}  (/mcp to manage)")
    else:
        print("  [MCP] No MCPs installed. Type /mcp auto <goal> to discover and install one.")

    print(f"  [>]  Model: {agent.copilot.get_model()}  (/models to change)")
    print(f"  [T]  Think level: {agent._think_level}  (/think to change)")
    print()
    print("  Type /help to see all commands. Type your message to start chatting.")
    print("  Tip: Press Ctrl+C to cancel a response. Type your next message at any time")
    print("       while the agent is thinking -- it will be queued automatically.")
    print()


def main() -> None:
    """Start the Sapiens2.0 agent (interactive session or Discord bot mode)."""
    parser = argparse.ArgumentParser(
        description="Sapiens2.0 - AI Agent with Computer Control",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Quick start (PowerShell / terminal):
              pip install -e .       Install once to get the 'sapiens' command
              sapiens wakeup         Start from any directory

            Or run directly from the project folder:
              python main.py

            Authentication:
              1. Start Sapiens2.0
              2. Type: /auth
              3. Note the code shown in the terminal
              4. Open https://github.com/login/device in your browser and enter the code
              5. Your account is saved -- no need to re-authenticate next time

            Discord bot mode:
              sapiens wakeup --discord --discord-token <BOT_TOKEN>
              (or set DISCORD_BOT_TOKEN env var and omit --discord-token)
        """),
    )
    parser.add_argument(
        "--token",
        metavar="GITHUB_TOKEN",
        default=os.environ.get("GITHUB_TOKEN"),
        help="GitHub OAuth/PAT token (overrides saved auth and GITHUB_TOKEN env var).",
    )
    parser.add_argument(
        "--client-id",
        metavar="CLIENT_ID",
        default=DEFAULT_CLIENT_ID,
        help=(
            "GitHub OAuth App Client ID (default: GitHub CLI public app). "
            "Only needed if you registered your own OAuth App at "
            "https://github.com/settings/developers"
        ),
    )
    parser.add_argument(
        "--discord",
        action="store_true",
        default=False,
        help=(
            "Run Sapiens2.0 as a Discord bot instead of starting the terminal interface. "
            "Requires a bot token via --discord-token or the DISCORD_BOT_TOKEN env var."
        ),
    )
    parser.add_argument(
        "--discord-token",
        metavar="DISCORD_BOT_TOKEN",
        default=None,
        help=(
            "Discord bot token. Overrides the DISCORD_BOT_TOKEN env var and any "
            "previously saved token."
        ),
    )
    args = parser.parse_args()

    agent = AgentCore(github_token=args.token, client_id=args.client_id)

    # ── Discord bot mode ─────────────────────────────────────────────────────
    if args.discord:
        _run_discord_bot(agent, args.discord_token)
        return

    # ── Interactive terminal mode ────────────────────────────────────────────
    _print_banner(agent)

    # Start the background stdin reader thread.  From this point on ALL stdin
    # reads must go through _safe_input() / _stdin_queue so they are serialised
    # through the single reader thread.  This enables queued input: if the user
    # types a message while the agent is still working, it lands in the queue
    # and is processed as soon as the current response finishes.
    _start_stdin_reader()

    while True:
        user_input_raw = _safe_input("[You] ")
        if user_input_raw is None:
            # EOF — stdin was closed (e.g. piped input exhausted)
            print("\nGoodbye!")
            break
        user_input = user_input_raw.strip()

        if not user_input:
            continue

        # Slash commands run directly (they may prompt stdin for confirmations,
        # so it is safer to keep them on the main thread).
        if user_input.startswith("/"):
            response = agent.process(user_input)
            if response:
                print(f"[Sapiens2.0] {response}\n")
            continue

        # ── Chat messages run in a background thread so Ctrl+C can cancel ──
        # The spinner is managed inside _run_agent_chat via agent._active_spinner
        # so it can be stopped cleanly from the Ctrl+C handler below.
        #
        # Because the stdin reader thread keeps reading from stdin while the
        # worker is busy, anything the user types now goes straight into
        # _stdin_queue and will be processed on the NEXT loop iteration —
        # providing transparent queued input without any extra complexity.
        result_box: List[Optional[str]] = [None]
        done_event = threading.Event()

        def _work(ui: str = user_input) -> None:
            result_box[0] = agent.process(ui)
            done_event.set()

        worker = threading.Thread(target=_work, daemon=True)
        worker.start()

        print(
            "  [Thinking...] "
            "(you can type your next message now -- it will be queued)\n",
            end="",
            flush=True,
        )

        try:
            done_event.wait()
        except KeyboardInterrupt:
            # Stop the active spinner immediately so it doesn't keep printing
            # over the cancellation message or the next prompt.
            active = agent._active_spinner
            if active is not None:
                active.stop()
                agent._active_spinner = None
            print("\n\n  [Cancelled] Response generation stopped.  (Ctrl+C)\n")
            continue

        response = result_box[0]
        if response:
            print(f"[Sapiens2.0] {response}\n")


def cli_entry() -> None:
    """
    Entry point for the 'sapiens' console script installed by pip.

    Usage:
      sapiens wakeup                          Start Sapiens2.0 (terminal mode)
      sapiens wakeup --token <TOKEN>          Start with a specific GitHub token
      sapiens wakeup --discord                Start in Discord bot mode
      sapiens wakeup --discord --discord-token <BOT_TOKEN>
    """
    args = sys.argv[1:]

    if not args or args[0] != "wakeup":
        print("Usage: sapiens wakeup [--token GITHUB_TOKEN] [--client-id CLIENT_ID]")
        print("       sapiens wakeup --discord [--discord-token DISCORD_BOT_TOKEN]")
        print("       sapiens wakeup --help")
        sys.exit(1)

    # Strip 'wakeup' from argv and let argparse handle the rest
    sys.argv = [sys.argv[0]] + args[1:]
    main()


if __name__ == "__main__":
    main()
