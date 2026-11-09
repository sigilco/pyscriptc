# docs/install.ps1: check the pyscriptc prerequisites, install what is missing,
# then install pyscriptc and run `pyscriptc doctor`.
#
# The Windows twin of install.sh, with the same rules: safe to run any number
# of times (every step skips its work when it is already done), never elevated.
# For anything it will not install itself it prints the exact command to run
# as your normal user and exits non-zero.
#
# Usage:
#   .\docs\install.ps1           check prerequisites, print commands for anything missing
#   .\docs\install.ps1 -Yes      also install uv and zig where it can, without asking
#
# With -Yes the script may install:
#   - uv, via its official installer (https://astral.sh/uv/install.ps1)
#   - zig, via winget or scoop when either is on PATH
# It still never elevates: if the package manager needs an admin shell it
# prints the command and exits.

param(
    [switch]$Yes,
    [switch]$Help
)

$script:AssumeYes = [bool]$Yes
$ErrorActionPreference = "Stop"

if ($Help) {
    Get-Content $PSCommandPath | Select-Object -First 21 | ForEach-Object { $_ -replace '^#\s{0,1}', '' }
    exit 0
}

function Say { param([string]$Message) Write-Host $Message }
function Err { param([string]$Message) Write-Error $Message -ErrorAction Continue }

function Have {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

# --- C compiler or zig -------------------------------------------------------

function Ensure-Toolchain {
    if ((Have zig) -or (Have clang)) { return $true }
    Err "no C compiler found (need zig or clang on PATH)."
    if ($script:AssumeYes) {
        if (Have winget) {
            Say "+ winget install zig.zig"
            winget install zig.zig --silent 2>&1 | Out-Null
        }
        elseif (Have scoop) {
            Say "+ scoop install zig"
            scoop install zig 2>&1 | Out-Null
        }
        else {
            Err "no winget or scoop found; could not install zig automatically."
        }
        if ((Have zig) -or (Have clang)) { return $true }
        Err "could not install zig automatically; install zig or clang and re-run."
    }
    else {
        Err "re-run with -Yes to install zig automatically (winget or scoop), or install one of:"
        Err "  winget install zig.zig"
        Err "  scoop install zig"
    }
    return $false
}

# --- uv ----------------------------------------------------------------------

function Ensure-Uv {
    if (Have uv) { return $true }
    if (-not $script:AssumeYes) {
        Err "uv is not installed. Install it with:"
        Err "  powershell -ExecutionPolicy ByPass -c `"irm https://astral.sh/uv/install.ps1 | iex`""
        Err "or re-run this script with -Yes to do that step automatically."
        return $false
    }
    Say "+ irm https://astral.sh/uv/install.ps1 | iex"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    # The installer puts uv in %USERPROFILE%\.local\bin (or UV_INSTALL_PATH);
    # make it visible for the rest of this script without assuming a login shell.
    $uvBin = if ($env:UV_INSTALL_PATH) { $env:UV_INSTALL_PATH } else { Join-Path $env:USERPROFILE ".local\bin" }
    if (Test-Path (Join-Path $uvBin "uv.exe")) {
        $env:Path = "$uvBin;$env:Path"
    }
    return (Have uv)
}

# --- main --------------------------------------------------------------------

$missing = $false

if (-not (Ensure-Toolchain)) { $missing = $true }
if (-not (Ensure-Uv)) { $missing = $true }

if ($missing) {
    Err "prerequisites are missing; fix the items above and re-run."
    exit 1
}

Say "+ uv tool install pyscriptc"
uv tool install pyscriptc

Say "+ pyscriptc doctor"
uv tool run pyscriptc doctor

Say "done. Try: pyscriptc run hello.py"
