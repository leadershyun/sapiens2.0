"""
Sapiens2.0 - AI Agent
======================
An AI agent prototype with computer control, long-term memory, and GitHub Copilot integration.

Features:
  1. Conversational AI powered by GitHub Copilot
  2. Computer control: inspect files, navigate directories, create/edit files, run commands
  3. Short-term memory (session) and long-term memory (persistent file)
  4. Model selection (/models), new conversation (/new), full reset (/reset)
  5. Persistent GitHub account linkage across runs

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

Key Commands:
  /auth                 Start GitHub device flow authentication (recommended)
  /auth <token>         Provide a GitHub PAT/OAuth token directly
  /logout               Unlink the saved GitHub account
  /models [num|name]    List or select available Copilot models
  /new                  Start a new conversation (short-term memory cleared)
  /reset                Full reset: long-term memory + model settings cleared
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
  /help                 Show help text
  /exit or /quit        Exit

Memory system:
  Short-term: session conversation history. Cleared on /new or exit.
  Long-term:  sapiens_memory.json — persists across sessions. Cleared only on /reset.
              The agent automatically extracts and saves important facts from conversations.

Computer control:
  The agent can control your computer by issuing tool commands in its responses.
  Just ask naturally: "what files are in this folder?" or "run the tests" and the
  agent will use the appropriate tools automatically.

Dependencies:
  pip install requests
"""

import argparse
import os
import re
import subprocess
import sys
import time
import json
import textwrap
from typing import Dict, List, Optional, Union

try:
    import requests
except ImportError:
    print("[Error] 'requests' package is required. Run: pip install requests")
    sys.exit(1)

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

# Long-term memory file path (relative to working directory)
MEMORY_FILE = "sapiens_memory.json"

# Agent state file (stores selected model, etc.)
STATE_FILE = "sapiens_state.json"

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

# Persistent auth config (~/.sapiens2/config.json)
AUTH_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".sapiens2")
AUTH_CONFIG_FILE = os.path.join(AUTH_CONFIG_DIR, "config.json")

# Regex to detect agent tool calls embedded in model responses
# Format: <tool>/command args</tool>
TOOL_CALL_RE = re.compile(r'<tool>(.*?)</tool>', re.DOTALL | re.IGNORECASE)


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
      - Stored as JSON in sapiens_memory.json
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
        """Write content to a file. Asks for confirmation if the file already exists."""
        target = os.path.abspath(os.path.join(self._cwd, path))

        if os.path.exists(target):
            if not _confirm(f"⚠️  '{target}' already exists. Overwrite?"):
                return "Cancelled."

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
        """Run a Python script or shell script. Warns before executing dangerous file types."""
        target = os.path.abspath(os.path.join(self._cwd, path))
        if not os.path.isfile(target):
            return f"[Error] File not found: {target}"

        ext = os.path.splitext(target)[1].lower()

        if ext in DANGEROUS_EXTENSIONS:
            if not _confirm(f"⚠️  '{target}' is a potentially dangerous file type. Run anyway?"):
                return "Cancelled."

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
#  Module 3: Agent Core
# ─────────────────────────────────────────────

class AgentCore:
    """
    Sapiens2.0 core agent module.

    Routes user input to the appropriate module (Copilot, System, Memory).

    Memory structure:
      - Short-term: MemoryModule._short_term (session conversation history)
      - Long-term:  sapiens_memory.json (persists across sessions)

    State file:
      - sapiens_state.json: stores selected model and other settings
    """

    def __init__(self, github_token: Optional[str] = None, client_id: str = DEFAULT_CLIENT_ID):
        self.copilot = CopilotModule()
        self.system = SystemCommandModule()
        self.memory = MemoryModule()
        self.client_id = client_id

        # Load saved state (model selection, etc.)
        self._load_state()

        # Token priority: explicit arg > env var > saved auth file
        if github_token:
            self.copilot.set_token(github_token)
        else:
            saved_token = _load_auth_token()
            if saved_token:
                self.copilot.set_token(saved_token)

    # ── State management ────────────────────────

    def _load_state(self) -> None:
        """Load agent state (model selection, etc.) from disk."""
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                if isinstance(state, dict):
                    model = state.get("model", COPILOT_DEFAULT_MODEL)
                    self.copilot.set_model(model)
            except (json.JSONDecodeError, IOError):
                pass

    def _save_state(self) -> None:
        """Persist current agent state to disk."""
        try:
            state = {"model": self.copilot.get_model()}
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

        response = self._run_agent_chat(raw, history, lt_context)
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
            Tool execution result as a string.
        """
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
        else:
            return f"[Error] Unknown tool: {cmd}"

    def _run_agent_chat(self, user_msg: str, history: list, lt_context: str) -> str:
        """
        Agentic chat loop: the model can issue computer control tool calls in its
        response using <tool>/command args</tool> tags. Tool results are fed back
        to the model, and the loop continues until the model gives a final answer
        with no more tool calls (up to 5 iterations).

        Intermediate tool execution output is printed inline so the user can follow
        along in real time. Only the final natural language answer is returned.

        Args:
            user_msg: Current user message.
            history: Short-term memory before this message (for context).
            lt_context: Long-term memory context string.

        Returns:
            Final model response (natural language answer to the user).
        """
        system_prompt = _build_agent_system_prompt(lt_context)
        messages: List[Dict[str, str]] = list(history) + [{"role": "user", "content": user_msg}]

        last_response = ""

        for _turn in range(5):  # max 5 tool-call iterations per turn
            response = self.copilot._call_copilot_api_messages(
                system_prompt, messages, max_tokens=2048
            )
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
            for tool_cmd in tool_calls:
                tool_cmd = tool_cmd.strip()
                print(f"  ⚙  {tool_cmd}")
                result = self._execute_agent_tool(tool_cmd)
                # Print result with indentation for readability
                for line in result.splitlines():
                    print(f"     {line}")
                print()
                tool_results.append(f"[{tool_cmd}]:\n{result}")

            # Feed results back to the model
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": (
                    "Tool execution results:\n\n"
                    + "\n\n".join(tool_results)
                    + "\n\nThe results above have been shown to the user. "
                    "Please provide a brief, helpful natural language response summarizing "
                    "what you found or did."
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
                f"  - Long-term memory (sapiens_memory.json) deleted\n"
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
                try:
                    while True:
                        line = input()
                        if line == "EOF":
                            break
                        lines.append(line)
                except EOFError:
                    pass
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

        # ── Exit ─────────────────────────────────
        if cmd in ("/exit", "/quit", "/q"):
            print("Goodbye! 👋")
            sys.exit(0)

        return f"[Error] Unknown command: {cmd}\nType /help to see all available commands."


# ─────────────────────────────────────────────
#  유틸리티 함수
# ─────────────────────────────────────────────

def _confirm(message: str) -> bool:
    """
    사용자에게 예/아니오 확인을 요청합니다.

    Args:
        message: 확인 메시지

    Returns:
        True: 사용자가 확인(y/yes), False: 취소(n/no 또는 기타)
    """
    try:
        answer = input(f"{message} [y/N] ").strip().lower()
        return answer in ("y", "yes", "예", "응")
    except (EOFError, KeyboardInterrupt):
        return False


def _run_subprocess(cmd: Union[str, List[str]], cwd: str = ".", shell: bool = False, timeout: int = 30) -> str:
    """
    서브프로세스를 실행하고 결과를 반환합니다.

    Args:
        cmd: 실행할 명령 (문자열 또는 리스트)
        cwd: 작업 디렉토리
        shell: True면 셸을 통해 실행
        timeout: 타임아웃 (초)

    Returns:
        표준 출력 + 표준 에러 결합 문자열
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
        if result.stdout.strip():
            output_parts.append(result.stdout.strip())
        if result.stderr.strip():
            output_parts.append(f"[stderr]\n{result.stderr.strip()}")
        if result.returncode != 0:
            output_parts.append(f"[종료 코드: {result.returncode}]")

        return "\n".join(output_parts) if output_parts else "(출력 없음)"

    except subprocess.TimeoutExpired:
        return f"[오류] 명령 실행 시간 초과 ({timeout}초)"
    except FileNotFoundError as e:
        return f"[오류] 명령 또는 파일을 찾을 수 없습니다: {e}"
    except PermissionError as e:
        return f"[오류] 실행 권한이 없습니다: {e}"


# ─────────────────────────────────────────────
#  Utility functions
# ─────────────────────────────────────────────

def _confirm(message: str) -> bool:
    """
    Ask the user for a yes/no confirmation.

    Returns:
        True if the user confirms (y/yes), False otherwise.
    """
    try:
        answer = input(f"{message} [y/N] ").strip().lower()
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
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
        if result.stdout.strip():
            output_parts.append(result.stdout.strip())
        if result.stderr.strip():
            output_parts.append(f"[stderr]\n{result.stderr.strip()}")
        if result.returncode != 0:
            output_parts.append(f"[exit code: {result.returncode}]")

        return "\n".join(output_parts) if output_parts else "(no output)"

    except subprocess.TimeoutExpired:
        return f"[Error] Command timed out ({timeout}s)"
    except FileNotFoundError as e:
        return f"[Error] Command or file not found: {e}"
    except PermissionError as e:
        return f"[Error] Permission denied: {e}"


def _build_agent_system_prompt(lt_context: str = "") -> str:
    """
    Build the system prompt for the agentic chat loop.
    Explicitly informs the model of its computer control capabilities and the
    tool-call format it must use to actually execute commands.
    """
    parts = [
        "You are Sapiens2.0, an AI agent with DIRECT computer control capabilities.\n"
        "\n"
        "IMPORTANT: You can control the user's computer right now. When asked to inspect "
        "files, navigate directories, run commands, or perform any local task, you MUST "
        "use the tools below — do not just describe what to do, ACTUALLY DO IT.\n"
        "\n"
        "HOW TO USE COMPUTER CONTROL TOOLS:\n"
        "Include tool calls in your response using this exact format:\n"
        "  <tool>/command args</tool>\n"
        "These are executed automatically; results are fed back to you.\n"
        "\n"
        "AVAILABLE TOOLS:\n"
        "  <tool>/pwd</tool>                   — show current working directory\n"
        "  <tool>/ls</tool>                    — list current directory contents\n"
        "  <tool>/ls path/to/dir</tool>        — list a specific directory\n"
        "  <tool>/cat filename</tool>           — read a file's contents\n"
        "  <tool>/cd path/to/dir</tool>         — change working directory\n"
        "  <tool>/exec shell_command</tool>     — run any shell/PowerShell command\n"
        "  <tool>/run script.py</tool>          — execute a Python script\n"
        "\n"
        "EXAMPLES:\n"
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
        "6. Answer in the same language the user writes in.",
    ]

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

        OTHER:
          /help  or  /?        Show this help text
          /exit  or  /quit     Exit Sapiens2.0

        MEMORY SYSTEM:
          Short-term : Current session conversation history.
                       Cleared on /new or exit.
          Long-term  : sapiens_memory.json — persists across sessions.
                       The agent automatically extracts and saves important facts.
                       Cleared only on /reset.

        PERSISTENT AUTH:
          After your first /auth, your GitHub account is saved to ~/.sapiens2/config.json.
          The next time you run 'sapiens wakeup', you are automatically logged in —
          no need to re-authenticate unless you run /logout.
    """)


# ─────────────────────────────────────────────
#  Entry points
# ─────────────────────────────────────────────

def _print_banner(agent: "AgentCore") -> None:
    """Print the Sapiens2.0 startup logo and status information."""
    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    print("  ║                                                          ║")
    print("  ║   ____             _                 ____   ___          ║")
    print("  ║  / ___|  __ _ _ __|_) ___ _ __  ___ |___ \\ / _ \\        ║")
    print("  ║  \\___ \\ / _` | '_ \\ |/ _ \\ '_ \\/ __|  __) | | |        ║")
    print("  ║   ___) | (_| | |_) | |  __/ | | \\__ \\ / __/| |_|        ║")
    print("  ║  |____/ \\__,_| .__/|_|\\___|_| |_|___/|_____|\\___/       ║")
    print("  ║              |_|                                          ║")
    print("  ║                                                          ║")
    print("  ║  AI Agent  ·  Computer Control  ·  Long-Term Memory      ║")
    print("  ║  GitHub Copilot powered  ·  v2.0                         ║")
    print("  ║                                                          ║")
    print("  ╚══════════════════════════════════════════════════════════╝")
    print()

    # Auth status
    if agent.copilot.is_authenticated():
        saved = os.path.exists(AUTH_CONFIG_FILE)
        note = "  (saved — auto-login enabled)" if saved else ""
        print(f"  ✅ GitHub account linked{note}")
    else:
        print("  ℹ️  Not authenticated. Type /auth to link your GitHub account.")
        print("     Visit https://github.com/login/device and enter the code shown.")

    # Long-term memory
    lt_mem = agent.memory.get_long_term()
    if lt_mem:
        print(f"  💾 Long-term memory: {len(lt_mem)} entries loaded  (/memory to view)")

    # Model
    print(f"  🤖 Model: {agent.copilot.get_model()}  (/models to change)")
    print()
    print("  Type /help to see all commands. Type your message to start chatting.")
    print()


def main() -> None:
    """Start the Sapiens2.0 agent (interactive session)."""
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
              5. Your account is saved — no need to re-authenticate next time
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
    args = parser.parse_args()

    agent = AgentCore(github_token=args.token, client_id=args.client_id)
    _print_banner(agent)

    # Interactive loop
    while True:
        try:
            user_input = input("[You] ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye! 👋")
            break

        if not user_input:
            continue

        response = agent.process(user_input)
        if response:
            print(f"[Sapiens2.0] {response}\n")


def cli_entry() -> None:
    """
    Entry point for the 'sapiens' console script installed by pip.

    Usage:
      sapiens wakeup          Start Sapiens2.0
      sapiens wakeup --token  Start with a specific GitHub token
    """
    args = sys.argv[1:]

    if not args or args[0] != "wakeup":
        print("Usage: sapiens wakeup [--token GITHUB_TOKEN] [--client-id CLIENT_ID]")
        print("       sapiens wakeup --help")
        sys.exit(1)

    # Strip 'wakeup' from argv and let argparse handle the rest
    sys.argv = [sys.argv[0]] + args[1:]
    main()


if __name__ == "__main__":
    main()
