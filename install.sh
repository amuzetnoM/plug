#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════╗
# ║  PLUG — Discord AI Gateway                      ║
# ║  Cross-platform installer (Linux/macOS/Termux)  ║
# ╚══════════════════════════════════════════════════╝
set -euo pipefail

# Colors (safe for dumb terminals)
if [ -t 1 ] && command -v tput &>/dev/null && tput colors &>/dev/null; then
    RED=$(tput setaf 1); GREEN=$(tput setaf 2); YELLOW=$(tput setaf 3)
    CYAN=$(tput setaf 6); BOLD=$(tput bold); DIM=$(tput dim); RESET=$(tput sgr0)
else
    RED=""; GREEN=""; YELLOW=""; CYAN=""; BOLD=""; DIM=""; RESET=""
fi

ok()   { echo "${GREEN}  ✓ $1${RESET}"; }
fail() { echo "${RED}  ✗ $1${RESET}"; }
info() { echo "${CYAN}  → $1${RESET}"; }
dim()  { echo "${DIM}    $1${RESET}"; }
step() { echo; echo "${YELLOW}${BOLD}  [$1/$TOTAL_STEPS] $2${RESET}"; }

TOTAL_STEPS=5
PLUG_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUG_HOME="${PLUG_HOME:-$HOME/.plug}"
VENV_DIR="$PLUG_DIR/.venv"
MIN_PYTHON="3.11"

# ── Detect platform ──────────────────────────────────────────────
detect_platform() {
    if [ -d "/data/data/com.termux" ] || [ -n "${TERMUX_VERSION:-}" ]; then
        PLATFORM="termux"
    elif [ "$(uname)" = "Darwin" ]; then
        PLATFORM="macos"
    else
        PLATFORM="linux"
    fi
    echo
    echo "${BOLD}  ⚡ PLUG Installer${RESET}"
    echo "${DIM}    Platform: $PLATFORM | $(uname -m)${RESET}"
    echo
}

# ── Step 1: Check Python ─────────────────────────────────────────
check_python() {
    step 1 "Checking Python"

    # Find python
    PYTHON=""
    for cmd in python3.13 python3.12 python3.11 python3; do
        if command -v "$cmd" &>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    done

    if [ -z "$PYTHON" ]; then
        fail "Python 3.11+ not found"
        echo
        case "$PLATFORM" in
            termux) dim "Run: pkg install python" ;;
            macos)  dim "Run: brew install python@3.12" ;;
            linux)  dim "Run: sudo apt install python3 python3-venv python3-pip" ;;
        esac
        exit 1
    fi

    # Check version
    PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
    PY_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")

    if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]); then
        fail "Python $PY_VERSION found, need $MIN_PYTHON+"
        case "$PLATFORM" in
            termux) dim "Run: pkg install python" ;;
            macos)  dim "Run: brew install python@3.12" ;;
            linux)  dim "Run: sudo apt install python3.12 python3.12-venv" ;;
        esac
        exit 1
    fi

    ok "Python $PY_VERSION ($PYTHON)"

    # Check venv module
    if ! "$PYTHON" -c "import venv" &>/dev/null; then
        fail "venv module missing"
        case "$PLATFORM" in
            termux) dim "Should be included with python — try: pkg reinstall python" ;;
            *)      dim "Run: sudo apt install python3-venv (or python3.${PY_MINOR}-venv)" ;;
        esac
        exit 1
    fi
}

# ── Step 2: Check system dependencies ────────────────────────────
check_deps() {
    step 2 "Checking dependencies"

    local missing=()

    # git (required)
    if command -v git &>/dev/null; then
        ok "git $(git --version | cut -d' ' -f3)"
    else
        missing+=("git")
        fail "git not found"
    fi

    # Build tools for native extensions
    if [ "$PLATFORM" = "termux" ]; then
        # Termux needs specific packages for building wheels
        for pkg in rust binutils; do
            if command -v "$pkg" &>/dev/null || dpkg -l "$pkg" &>/dev/null 2>&1; then
                ok "$pkg"
            else
                info "$pkg not found (may be needed for some dependencies)"
                dim "Install with: pkg install $pkg"
            fi
        done
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        fail "Missing required: ${missing[*]}"
        case "$PLATFORM" in
            termux) dim "Run: pkg install ${missing[*]}" ;;
            macos)  dim "Run: brew install ${missing[*]}" ;;
            linux)  dim "Run: sudo apt install ${missing[*]}" ;;
        esac
        exit 1
    fi
}

# ── Step 3: Create venv & install ─────────────────────────────────
install_plug() {
    step 3 "Installing PLUG"

    cd "$PLUG_DIR"

    if [ ! -d "$VENV_DIR" ]; then
        info "Creating virtual environment..."
        "$PYTHON" -m venv "$VENV_DIR"
        ok "venv created at $VENV_DIR"
    else
        ok "venv exists"
    fi

    # Activate
    source "$VENV_DIR/bin/activate"

    # Upgrade pip
    info "Upgrading pip..."
    pip install --upgrade pip wheel setuptools -q 2>/dev/null
    ok "pip ready"

    # Install plug in editable mode
    info "Installing plug + dependencies..."
    if pip install -e . -q 2>&1; then
        ok "plug installed"
    else
        fail "pip install failed — trying with verbose output"
        pip install -e .
        exit 1
    fi

    # Install copilot proxy dependency (aiohttp) if copilot_proxy.py exists
    if [ -f "$PLUG_DIR/copilot_proxy.py" ]; then
        info "Installing copilot proxy dependency (aiohttp)..."
        pip install aiohttp -q 2>/dev/null
        ok "aiohttp installed"
    fi

    # Verify
    if command -v plug &>/dev/null || "$VENV_DIR/bin/plug" --version &>/dev/null; then
        ok "plug CLI ready"
    else
        fail "plug CLI not found after install"
        exit 1
    fi
}

# ── Step 4: Create config directory ───────────────────────────────
setup_config() {
    step 4 "Setting up config"

    mkdir -p "$PLUG_HOME"

    if [ -f "$PLUG_HOME/config.json" ]; then
        ok "Config exists at $PLUG_HOME/config.json"
        dim "Run 'plug init' to reconfigure"
    else
        info "No config found — run 'plug init' for interactive setup"
        dim "Or copy the example: cp config.example.json $PLUG_HOME/config.json"
    fi
}

# ── Step 5: Platform-specific setup ──────────────────────────────
platform_setup() {
    step 5 "Platform setup ($PLATFORM)"

    case "$PLATFORM" in
        termux)
            # Termux: no systemd, use termux-services or manual start
            ok "Termux detected — use 'plug start' to run"
            dim "For background: plug start (auto-daemonizes)"
            dim "For foreground: plug start -f"
            dim "To auto-start: add to ~/.bashrc or use termux-boot"

            # Create a convenience script
            if [ ! -f "$HOME/bin/plug" ] && [ -d "$HOME/bin" ] || mkdir -p "$HOME/bin"; then
                cat > "$HOME/bin/plug" << WRAPPER
#!/usr/bin/env bash
source "$VENV_DIR/bin/activate"
exec "$VENV_DIR/bin/plug" "\$@"
WRAPPER
                chmod +x "$HOME/bin/plug"
                ok "Wrapper script: ~/bin/plug"
                dim "Add to PATH if needed: export PATH=\$HOME/bin:\$PATH"
            fi
            ;;

        macos)
            ok "macOS detected"
            dim "Start: plug start"
            dim "For launchd service: plug install (creates LaunchAgent)"
            ;;

        linux)
            ok "Linux detected"
            dim "Start: plug start"
            dim "For systemd: plug install && systemctl --user enable --now plug"
            ;;
    esac
}

# ── Run ──────────────────────────────────────────────────────────
detect_platform
check_python
check_deps
install_plug
setup_config
platform_setup

echo
echo "${GREEN}${BOLD}  ✓ PLUG installed successfully${RESET}"
echo
echo "  ${CYAN}Next steps:${RESET}"
echo "    ${BOLD}1.${RESET} Activate venv:  ${DIM}source $VENV_DIR/bin/activate${RESET}"
echo "    ${BOLD}2.${RESET} Setup config:   ${DIM}plug init${RESET}"
echo "    ${BOLD}3.${RESET} Start the bot:  ${DIM}plug start${RESET}"
echo
echo "  ${DIM}Copilot proxy (free AI models via GitHub Copilot):${RESET}"
echo "    ${BOLD}4.${RESET} Authenticate:   ${DIM}python3 copilot_proxy.py auth${RESET}"
echo "    ${BOLD}5.${RESET} Start proxy:    ${DIM}python3 copilot_proxy.py${RESET}"
echo
