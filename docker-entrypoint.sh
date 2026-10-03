#!/bin/bash
# mod protocol sandbox entrypoint.
#
#   idle              (default) keep the container alive; you exec into it
#   serve <mod> [k=v] run one mod's server in the foreground
#   fleet             run start.sh (gateway + app) — isolated state only
#   <anything else>   exec it verbatim
set -o pipefail

MOD_DIR="${MOD_DIR:-/root/mod}"

banner() {
    echo "=== mod protocol sandbox ==="
    echo "tree    : $MOD_DIR"
    echo "python  : $(python3 --version 2>&1)"
    echo "node    : $(node --version 2>&1)  pm2 $(pm2 --version 2>/dev/null)"
    echo "rust    : $(rustc --version 2>&1 | cut -d' ' -f1-2)"
    echo "state   : ${HOME}/.mod"
}

# The tree is a bind mount. Without it there is no protocol to run, and every
# downstream failure ("no module named mod") points at the wrong thing.
require_tree() {
    if [ ! -f "$MOD_DIR/mod/__init__.py" ]; then
        echo "[!] no module tree at $MOD_DIR" >&2
        echo "[!] this image ships the toolchain only — mount the repo:" >&2
        echo "[!]   docker run -v \$HOME/mod:/root/mod ... mod:latest" >&2
        return 1
    fi
    if ! python3 -c 'import mod' 2>/dev/null; then
        echo "[!] tree is mounted at $MOD_DIR but 'import mod' failed:" >&2
        python3 -c 'import mod' 2>&1 | tail -20 >&2
        return 1
    fi
    return 0
}

case "${1:-idle}" in
    idle)
        banner
        require_tree || echo "[!] staying up anyway so you can fix the mount from inside" >&2
        echo "[ok] idle — exec in with: docker exec -it \$(hostname) bash"
        exec tail -f /dev/null
        ;;

    serve)
        shift
        MOD_NAME="${1:?usage: serve <mod> [key=value ...]}"; shift
        banner
        require_tree || exit 1
        echo "[+] m serve $MOD_NAME $*"
        exec m serve "$MOD_NAME" "$@"
        ;;

    fleet)
        banner
        require_tree || exit 1
        # start.sh's sibling stop.sh calls `m server/killall`, which walks the
        # shared ~/.mod registry. With the host's state mounted in, that reaps
        # the host fleet — so the fleet mode never runs it.
        if [ "${MOD_STATE_SHARED:-0}" = "1" ]; then
            echo "[!] MOD_STATE_SHARED=1: refusing to start a second fleet against the host's ~/.mod." >&2
            echo "[!] re-run with an isolated state volume (see docker-compose.yml)." >&2
            exit 1
        fi
        cd "$MOD_DIR" || exit 1
        bash ./start.sh || exit 1
        exec pm2 logs --raw
        ;;

    *)
        exec "$@"
        ;;
esac
