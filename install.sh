#!/usr/bin/env bash
# Install / uninstall G560 for GNOME (per user, no sudo except the optional udev rule).
#
#   ./install.sh                 install or update
#   ./install.sh uninstall       remove (keeps ~/.config/g560)
#   ./install.sh uninstall --purge   also remove settings
#   ./install.sh status          show what is installed and the speaker state
#
# Options: --udev (install/remove the udev access rule, needs sudo), --no-tests, -h
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
LIBDIR="$PREFIX/lib/g560"
BINDIR="$PREFIX/bin"
UNITDIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
CONFDIR="${XDG_CONFIG_HOME:-$HOME/.config}/g560"
UUID="g560@an-awful-person.github.io"
OLD_UUIDS=("g560@local")
EXTDIR="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$UUID"
UDEV_RULE="/etc/udev/rules.d/70-g560.rules"

if [[ -t 1 ]]; then B=$'\e[1m' G=$'\e[32m' Y=$'\e[33m' R=$'\e[31m' N=$'\e[0m'; else B= G= Y= R= N=; fi
step() { echo "${B}==>${N} $*"; }
ok()   { echo "  ${G}✓${N} $*"; }
warn() { echo "  ${Y}!${N} $*"; }
die()  { echo "${R}error:${N} $*" >&2; exit 1; }

usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

ACTION=install PURGE=0 UDEV=0 TESTS=1
for arg in "$@"; do
    case "$arg" in
        install|uninstall|status) ACTION=$arg ;;
        --purge) PURGE=1 ;;
        --udev) UDEV=1 ;;
        --no-tests) TESTS=0 ;;
        -h|--help) usage ;;
        *) echo "unknown argument: $arg" >&2; usage 1 ;;
    esac
done

[[ $EUID -eq 0 ]] && die "run as your normal user, not root (sudo is only used for --udev)"

missing_deps() {
    MISSING=()
    command -v python3 >/dev/null || MISSING+=(python3)
    python3 -c 'import gi' 2>/dev/null || MISSING+=(python3-gi)
    python3 -c 'import gi; gi.require_version("GUdev", "1.0")' 2>/dev/null || MISSING+=(gir1.2-gudev-1.0)
    command -v gjs >/dev/null || MISSING+=(gjs)
}

check_deps() {
    step "Checking dependencies"
    command -v systemctl >/dev/null || die "systemd (systemctl --user) is required"
    missing_deps
    if ((${#MISSING[@]})); then
        warn "missing packages: ${MISSING[*]}"
        if command -v apt-get >/dev/null && [[ -t 0 ]]; then
            read -r -p "  Install them now with 'sudo apt install ${MISSING[*]}'? [y/N] " reply
            if [[ $reply =~ ^[Yy] ]]; then
                sudo apt-get install -y "${MISSING[@]}"
                missing_deps
            fi
        fi
        ((${#MISSING[@]})) && die "install these packages first (Debian/Ubuntu names): ${MISSING[*]}
       Fedora: python3-gobject libgudev gjs   Arch: python-gobject libgudev gjs"
    fi
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
        || die "Python 3.10 or newer is required (found $(python3 -V 2>&1))"
    ok "Python $(python3 -c 'import platform; print(platform.python_version())'), PyGObject, GUdev, gjs, systemd"
    if command -v gnome-shell >/dev/null; then
        local ver; ver=$(gnome-shell --version | grep -oE '[0-9]+' | head -1)
        if grep -q "\"$ver\"" "$SRC/extension/$UUID/metadata.json"; then
            ok "GNOME Shell $ver"
        else
            warn "GNOME Shell $ver is not listed in metadata.json; the top-bar extension may not load"
        fi
    else
        warn "GNOME Shell not found; only the service and CLI will be useful"
    fi
}

remove_old_extensions() {
    local old dir
    for old in "${OLD_UUIDS[@]}"; do
        dir="$(dirname "$EXTDIR")/$old"
        if [[ -d $dir ]]; then
            gnome-extensions disable "$old" 2>/dev/null || true
            python3 "$SRC/tools/enable_ext.py" "$old" --remove >/dev/null 2>&1 || true
            rm -rf "$dir"
            ok "removed old extension ID $old"
        fi
    done
}

speaker_node() {
    PYTHONPATH="$SRC" python3 -c 'from g560d.device import find_hidraw; print(find_hidraw() or "")'
}

check_access() {
    step "Checking speaker access"
    local node; node=$(speaker_node)
    if [[ -z $node ]]; then
        warn "G560 not connected (fine: the service picks it up when plugged in)"
    elif [[ -r $node && -w $node ]]; then
        ok "$node is accessible"
    elif ((UDEV)); then
        warn "$node is not accessible; installing udev rule"
    else
        warn "$node is not accessible for your user; re-run with --udev (uses sudo)"
    fi
}

install_udev() {
    step "Installing udev rule (sudo)"
    sudo install -m644 "$SRC/data/70-g560.rules" "$UDEV_RULE"
    sudo udevadm control --reload
    sudo udevadm trigger --subsystem-match=hidraw
    ok "$UDEV_RULE"
}

do_install() {
    check_deps
    if ((TESTS)); then
        step "Running unit tests"
        (cd "$SRC" && python3 -m unittest discover -s tests -t . >/dev/null 2>&1 \
            && { ! command -v gjs >/dev/null || gjs -m tests/test_colors.js >/dev/null 2>&1; }) \
            || die "unit tests failed; run: cd '$SRC' && python3 -m unittest discover -s tests -t . -v"
        ok "tests pass"
    fi
    check_access
    ((UDEV)) && install_udev

    step "Installing files"
    install -d "$LIBDIR/g560d" "$BINDIR" "$UNITDIR" "$EXTDIR"
    rm -f "$LIBDIR"/g560d/*.py
    install -m644 "$SRC"/g560d/*.py "$LIBDIR/g560d/"
    install -m755 "$SRC/bin/g560" "$SRC/bin/g560d" "$BINDIR/"
    install -m644 "$SRC/data/g560d.service" "$UNITDIR/"
    remove_old_extensions
    rm -rf "$EXTDIR" && install -d "$EXTDIR"
    install -m644 "$SRC/extension/$UUID"/* "$EXTDIR/"
    ok "daemon + CLI  → $LIBDIR, $BINDIR/{g560,g560d}"
    ok "service       → $UNITDIR/g560d.service"
    ok "extension     → $EXTDIR"
    case ":$PATH:" in
        *":$BINDIR:"*) ;;
        *) if grep -qs '\.local/bin' "$HOME/.profile"; then
               warn "$BINDIR will be on your PATH after the next login (until then: $BINDIR/g560)"
           else
               warn "$BINDIR is not in your PATH; add it, or run $BINDIR/g560"
           fi ;;
    esac

    step "Starting service"
    systemctl --user daemon-reload
    systemctl --user enable g560d.service >/dev/null 2>&1
    systemctl --user restart g560d.service
    sleep 1
    if systemctl --user is-active --quiet g560d.service; then
        ok "g560d is running"
    else
        warn "g560d failed to start; see: journalctl --user -u g560d"
    fi

    step "Enabling GNOME extension"
    local relogin=1
    if gnome-extensions enable "$UUID" 2>/dev/null; then
        ok "enabled"
        gnome-extensions info "$UUID" 2>/dev/null | grep -q 'State: ACTIVE' && relogin=0
    else
        python3 "$SRC/tools/enable_ext.py" "$UUID" >/dev/null && ok "will be enabled at next login"
    fi

    echo
    echo "${G}${B}Installed.${N} Next steps:"
    local n=1
    if ((relogin)); then
        echo "  $n. Log out and back in once, so GNOME Shell loads the top-bar extension."; n=$((n + 1))
    fi
    echo "  $n. Click the speaker icon in the top bar (scroll on it to change the subwoofer)."; n=$((n + 1))
    echo "  $n. From a terminal: ${B}g560 status${N}   (all commands: g560 --help)"
}

do_uninstall() {
    step "Stopping service"
    systemctl --user disable --now g560d.service >/dev/null 2>&1 || true
    ok "g560d stopped"

    step "Disabling GNOME extension"
    gnome-extensions disable "$UUID" 2>/dev/null || true
    python3 "$SRC/tools/enable_ext.py" "$UUID" --remove >/dev/null 2>&1 || true
    ok "disabled"

    step "Removing files"
    rm -rf "$LIBDIR" "$EXTDIR"
    remove_old_extensions
    rm -f "$BINDIR/g560" "$BINDIR/g560d" "$UNITDIR/g560d.service"
    systemctl --user daemon-reload || true
    ok "removed program, service and extension"

    if ((PURGE)); then
        rm -rf "$CONFDIR"
        ok "removed settings ($CONFDIR)"
    else
        echo "  settings kept in $CONFDIR (use --purge to remove)"
    fi
    if ((UDEV)) && [[ -e $UDEV_RULE ]]; then
        step "Removing udev rule (sudo)"
        sudo rm -f "$UDEV_RULE"
        sudo udevadm control --reload
        ok "removed $UDEV_RULE"
    fi
    echo
    echo "${G}${B}Uninstalled.${N} The top-bar icon disappears now or after your next login."
}

do_status() {
    step "Installation"
    [[ -x $BINDIR/g560d ]] && ok "program installed ($BINDIR)" || warn "program not installed"
    [[ -d $EXTDIR ]] && ok "extension installed" || warn "extension not installed"
    if systemctl --user is-active --quiet g560d.service 2>/dev/null; then ok "service running"
    else warn "service not running"; fi
    local state; state=$(gnome-extensions info "$UUID" 2>/dev/null | sed -n 's/.*State: //p' || true)
    [[ -n $state ]] && ok "extension state: $state" || warn "extension not loaded by GNOME Shell (log in again?)"
    step "Speaker"
    if [[ -x $BINDIR/g560 ]]; then "$BINDIR/g560" status || true
    else PYTHONPATH="$SRC" python3 -m g560d status || true; fi
}

case "$ACTION" in
    install) do_install ;;
    uninstall) do_uninstall ;;
    status) do_status ;;
esac
