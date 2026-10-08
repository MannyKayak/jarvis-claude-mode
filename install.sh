#!/bin/sh
# Jarvis mode bootstrap for macOS and Linux.
#
# Finds Python (offering to install it when missing), fetches the project when it is not
# run from a clone, then hands over to the guided installer in install.py.
#
#   curl -fsSL https://raw.githubusercontent.com/MannyKayak/jarvis-claude-mode/main/install.sh | sh
#   ./install.sh install | uninstall [--yes] [--dry-run]      (from a clone)

set -u
REPO="MannyKayak/jarvis-claude-mode"

YES=0
for arg in "$@"; do
    case "$arg" in --yes|-y) YES=1 ;; esac
done
case "${1:-}" in
    ""|-*) set -- install "$@" ;;
esac

# When piped from curl, stdin is the script itself: questions go through the terminal.
TTY=""
if [ ! -t 0 ] && (: < /dev/tty) 2>/dev/null; then
    TTY=/dev/tty
fi

confirm() {
    [ "$YES" = 1 ] && return 0
    printf '%s [Y/n] ' "$1"
    if [ -t 0 ]; then
        read -r answer || return 1
    elif [ -n "$TTY" ]; then
        read -r answer < "$TTY" || return 1
    else
        echo
        echo "No terminal to ask on: run again with --yes to accept."
        return 1
    fi
    case "$answer" in ""|y*|Y*) return 0 ;; *) return 1 ;; esac
}

good_python() {
    command -v "$1" >/dev/null 2>&1 &&
        "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1
}

find_python() {
    for candidate in python3 python; do
        if good_python "$candidate"; then
            PY="$candidate"
            return 0
        fi
    done
    return 1
}

SUDO=""
if [ "$(id -u)" != 0 ] && command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
fi

# Prints the command that installs package $1 with this system's package manager.
install_command() {
    if [ "$(uname -s)" = Darwin ]; then
        command -v brew >/dev/null 2>&1 && echo "brew install $1"
    elif command -v apt-get >/dev/null 2>&1; then
        echo "$SUDO apt-get install -y $1"
    elif command -v dnf >/dev/null 2>&1; then
        echo "$SUDO dnf install -y $1"
    elif command -v pacman >/dev/null 2>&1; then
        echo "$SUDO pacman -S --noconfirm $1"
    elif command -v zypper >/dev/null 2>&1; then
        echo "$SUDO zypper install -y $1"
    fi
}

# offer_install <what it is> <package>: asks, then installs. Fails when declined or impossible.
offer_install() {
    command="$(install_command "$2")"
    if [ -z "$command" ]; then
        return 1
    fi
    confirm "Install $1 now with: $command ?" || return 1
    $command
}

PY=""
if ! find_python; then
    echo "Python 3.8 or later was not found. Jarvis mode needs it to install and to run."
    package=python3
    if [ "$(uname -s)" = Darwin ]; then package=python; fi
    if command -v pacman >/dev/null 2>&1; then package=python; fi
    if ! offer_install "Python" "$package" || ! find_python; then
        echo "Python is still missing. Install Python 3.8 or later, then run this command again."
        if [ "$(uname -s)" = Darwin ]; then
            echo "On macOS: xcode-select --install, or https://www.python.org/downloads/"
        fi
        exit 1
    fi
fi

# Linux has no built-in speech engine.
if [ "$(uname -s)" = Linux ] && [ -z "${JARVIS_TTS_CMD:-}" ] && ! command -v espeak-ng >/dev/null 2>&1; then
    case "$1" in
        install|init)
            echo "The speech engine espeak-ng was not found: without it, Jarvis stays silent."
            offer_install "espeak-ng" espeak-ng || echo "Continuing without it."
            ;;
    esac
fi

# Use the files next to this script only when it really is a file. Piped from curl, $0 is
# the shell's name and its "directory" would be wherever the user happens to be standing,
# possibly an old clone.
SOURCE=""
if [ -f "$0" ]; then
    SOURCE="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
fi
TEMP=""
if [ -z "$SOURCE" ] || [ ! -f "$SOURCE/install.py" ]; then
    echo "Downloading Jarvis mode from github.com/$REPO ..."
    TEMP="$(mktemp -d)"
    trap 'rm -rf "$TEMP"' EXIT
    url="https://github.com/$REPO/archive/refs/heads/main.tar.gz"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" | tar -xz -C "$TEMP" --strip-components=1 2>/dev/null
    else
        wget -qO- "$url" | tar -xz -C "$TEMP" --strip-components=1 2>/dev/null
    fi
    if [ ! -f "$TEMP/install.py" ]; then
        echo "Download failed."
        exit 1
    fi
    SOURCE="$TEMP"
fi

if [ -n "$TTY" ]; then
    "$PY" "$SOURCE/install.py" "$@" < "$TTY"
else
    "$PY" "$SOURCE/install.py" "$@"
fi
