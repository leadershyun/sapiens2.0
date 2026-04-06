#Requires -Version 5.1
<#
.SYNOPSIS
    One-line installer for Sapiens2.0.

.DESCRIPTION
    Downloads, installs, and configures Sapiens2.0 in a single step.
    Run from any PowerShell window (PowerShell 5.1+ or PowerShell 7+):

        iwr https://raw.githubusercontent.com/leadershyun/sapiens2.0/main/install.ps1 | iex

    What this script does:
      1. Checks that Python 3.8+ is available (guides you to install if not).
      2. Checks that Git is available (guides you to install if not).
      3. Clones the repository into  %USERPROFILE%\sapiens2.0  (or updates it
         if you have already cloned it there).
      4. Installs the 'sapiens' CLI command via  pip install -e .
      5. Runs  sapiens setup  to verify Node.js / npm and auto-install the
         essential MCPs (filesystem + Playwright).
      6. Prints the next steps so you can start chatting immediately.

.NOTES
    Sapiens2.0 requires:
      - Python 3.8 or later  (https://www.python.org/downloads/)
      - Git                  (https://git-scm.com/download/win)
      - Node.js LTS          (https://nodejs.org/en/download/)  [for MCP tools]
      - An active GitHub Copilot subscription
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── helpers ──────────────────────────────────────────────────────────────────

function Write-Header {
    param([string]$Text)
    Write-Host ""
    Write-Host "  +----------------------------------------------+" -ForegroundColor Cyan
    Write-Host ("  | {0,-44} |" -f $Text) -ForegroundColor Cyan
    Write-Host "  +----------------------------------------------+" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Step {
    param([string]$Text)
    Write-Host "  [*] $Text" -ForegroundColor Yellow
}

function Write-OK {
    param([string]$Text)
    Write-Host "  [+] $Text" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Text)
    Write-Host "  [!] $Text" -ForegroundColor DarkYellow
}

function Write-Fail {
    param([string]$Text)
    Write-Host "  [X] $Text" -ForegroundColor Red
}

function Find-Command {
    param([string]$Name)
    return (Get-Command $Name -ErrorAction SilentlyContinue) -ne $null
}

# ── banner ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  +============================================================+" -ForegroundColor Cyan
Write-Host "  |      Sapiens2.0 -- One-Line Windows Installer              |" -ForegroundColor Cyan
Write-Host "  |      https://github.com/leadershyun/sapiens2.0             |" -ForegroundColor Cyan
Write-Host "  +============================================================+" -ForegroundColor Cyan
Write-Host ""

# ── 1. Python ────────────────────────────────────────────────────────────────

Write-Header "Step 1 of 4 -- Checking Python"

$pythonCmd = $null
foreach ($candidate in @('python', 'python3', 'py')) {
    if (Find-Command $candidate) {
        # Confirm it is actually Python 3.8+
        try {
            $verOut = & $candidate -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}.{v.micro}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $verOut -match '^\d+\.\d+') {
                $parts  = $verOut.Trim().Split('.')
                $major  = [int]$parts[0]
                $minor  = [int]$parts[1]
                if ($major -ge 3 -and $minor -ge 8) {
                    $pythonCmd = $candidate
                    Write-OK "Python $($verOut.Trim()) found  ($candidate)"
                    break
                }
                else {
                    Write-Warn "Python $($verOut.Trim()) is too old (need 3.8+) -- trying next candidate"
                }
            }
        }
        catch {
            # ignore; try next candidate
        }
    }
}

if (-not $pythonCmd) {
    Write-Fail "Python 3.8+ was not found on this machine."
    Write-Host ""
    Write-Host "  Install Python from:  https://www.python.org/downloads/" -ForegroundColor White
    Write-Host ""
    Write-Host "  IMPORTANT: during installation, tick the box that says" -ForegroundColor White
    Write-Host "             'Add Python to PATH'." -ForegroundColor White
    Write-Host ""
    Write-Host "  After installing, open a new PowerShell window and re-run:" -ForegroundColor White
    Write-Host "    iwr https://raw.githubusercontent.com/leadershyun/sapiens2.0/main/install.ps1 | iex" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}

# ── 2. Git ───────────────────────────────────────────────────────────────────

Write-Header "Step 2 of 4 -- Checking Git"

if (Find-Command 'git') {
    $gitVer = (& git --version 2>$null).Trim()
    Write-OK "$gitVer found"
}
else {
    Write-Fail "Git was not found on this machine."
    Write-Host ""
    Write-Host "  Install Git from:  https://git-scm.com/download/win" -ForegroundColor White
    Write-Host ""
    Write-Host "  After installing, open a new PowerShell window and re-run:" -ForegroundColor White
    Write-Host "    iwr https://raw.githubusercontent.com/leadershyun/sapiens2.0/main/install.ps1 | iex" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}

# ── 3. Clone / update the repository ─────────────────────────────────────────

Write-Header "Step 3 of 4 -- Getting Sapiens2.0 source"

$repoUrl  = 'https://github.com/leadershyun/sapiens2.0.git'
$installDir = Join-Path $env:USERPROFILE 'sapiens2.0'

if (Test-Path (Join-Path $installDir '.git')) {
    Write-Step "Repository already exists at $installDir -- pulling latest changes..."
    Push-Location $installDir
    try {
        & git pull --ff-only origin main 2>&1 | ForEach-Object { "    $_" } | Write-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "git pull had a non-zero exit code -- your local changes may need manual merging."
        }
        else {
            Write-OK "Repository updated."
        }
    }
    finally {
        Pop-Location
    }
}
else {
    if (Test-Path $installDir) {
        Write-Warn "$installDir exists but is not a git repository -- installing alongside it."
        $installDir = Join-Path $env:USERPROFILE 'sapiens2.0-new'
    }
    Write-Step "Cloning repository into $installDir ..."
    & git clone $repoUrl $installDir 2>&1 | ForEach-Object { "    $_" } | Write-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "git clone failed.  Check your internet connection and try again."
        exit 1
    }
    Write-OK "Repository cloned to $installDir"
}

# ── 4. Install the 'sapiens' CLI ─────────────────────────────────────────────

Write-Header "Step 4 of 4 -- Installing Sapiens2.0"

Push-Location $installDir
try {
    Write-Step "Running  pip install -e .  (this installs the 'sapiens' command)..."
    & $pythonCmd -m pip install -e . --quiet 2>&1 | ForEach-Object { "    $_" } | Write-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "pip install failed.  Output shown above."
        exit 1
    }
    Write-OK "'sapiens' CLI installed successfully."
}
finally {
    Pop-Location
}

# ── 5. Run sapiens setup ──────────────────────────────────────────────────────

Write-Host ""
Write-Host "  +----------------------------------------------+" -ForegroundColor Cyan
Write-Host "  | Running environment health check...          |" -ForegroundColor Cyan
Write-Host "  +----------------------------------------------+" -ForegroundColor Cyan
Write-Host ""

# Node.js advisory (non-blocking)
if (-not (Find-Command 'node')) {
    Write-Warn "Node.js was not found."
    Write-Host "  Sapiens2.0 works without Node.js, but the filesystem and browser" -ForegroundColor DarkYellow
    Write-Host "  MCP tools require it.  Install Node.js LTS from:" -ForegroundColor DarkYellow
    Write-Host "    https://nodejs.org/en/download/" -ForegroundColor White
    Write-Host ""
}

# Check whether the 'sapiens' command is on PATH after pip install
if (Find-Command 'sapiens') {
    Write-Step "Running  sapiens setup  ..."
    Write-Host ""
    & sapiens setup
}
else {
    # Fall back to invoking cli_entry() directly (pip scripts dir may not be in
    # PATH yet in the current shell session — it will be available after a new
    # PowerShell window is opened).
    Write-Warn "'sapiens' command not yet on PATH (may need a new shell window)."
    Write-Step "Running setup via Python directly..."
    Write-Host ""
    Push-Location $installDir
    try {
        & $pythonCmd -c "import sys; sys.path.insert(0, '.'); sys.argv = ['sapiens', 'setup']; import main; main.cli_entry()"
    }
    finally {
        Pop-Location
    }
}

# ── done ─────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  +============================================================+" -ForegroundColor Green
Write-Host "  |  Sapiens2.0 is installed!                                  |" -ForegroundColor Green
Write-Host "  +============================================================+" -ForegroundColor Green
Write-Host ""
Write-Host "  Start Sapiens2.0 any time with:" -ForegroundColor White
Write-Host ""
Write-Host "    sapiens wakeup" -ForegroundColor Cyan
Write-Host ""
Write-Host "  If 'sapiens' is not recognised yet, open a new PowerShell" -ForegroundColor DarkGray
Write-Host "  window first (so the updated PATH takes effect), then run:" -ForegroundColor DarkGray
Write-Host ""
Write-Host "    sapiens wakeup" -ForegroundColor Cyan
Write-Host ""
Write-Host "  First-time use:" -ForegroundColor White
Write-Host "    1. Type  /auth  and follow the GitHub device-flow link" -ForegroundColor White
Write-Host "       (you need an active GitHub Copilot subscription)." -ForegroundColor White
Write-Host "    2. Start chatting or ask the agent to control your computer." -ForegroundColor White
Write-Host ""
Write-Host "  Useful commands:" -ForegroundColor White
Write-Host "    /auth       -- link your GitHub Copilot account" -ForegroundColor White
Write-Host "    /help       -- list all commands" -ForegroundColor White
Write-Host "    /mcp list   -- browse available MCP tools" -ForegroundColor White
Write-Host "    /setup      -- re-run the environment health check" -ForegroundColor White
Write-Host ""
