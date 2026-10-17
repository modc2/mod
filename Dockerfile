# The mod protocol sandbox image.
#
# This image is a TOOLCHAIN, not a snapshot of the tree. The module tree is
# bind-mounted at /root/mod at run time (see docker-compose.yml), which is how
# pm.docker already runs every other mod — baking a 60GB working tree into a
# layer only produced a stale 21GB image that drifted from the repo within a day.
#
# Contract for downstream images (orbit/build does `FROM mod:latest`):
#   python3.12 · node 20 + npm + pm2 · rust stable + cargo · caddy · libssl-dev
FROM python:3.12-slim
ARG DEBIAN_FRONTEND=noninteractive

ENV MOD_DOCKER=1 \
    MOD_DIR=/root/mod \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/root/.cargo/bin:$PATH

# --- system toolchain -------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl wget ca-certificates gnupg tini \
        build-essential pkg-config libssl-dev \
        procps psmisc lsof net-tools iproute2 jq sqlite3 vim-tiny \
        debian-keyring debian-archive-keyring apt-transport-https \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
 && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        > /etc/apt/sources.list.d/caddy-stable.list \
 && apt-get update && apt-get install -y --no-install-recommends caddy \
 && npm install -g pm2 \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# --- rust (orbit/build compiles its job server against this) ----------------
RUN curl https://sh.rustup.rs -sSf | sh -s -- -y --profile minimal --no-modify-path \
 && rustc --version && cargo --version

# --- python deps ------------------------------------------------------------
# CPU torch goes in FIRST: requirements.txt asks for `torch>=2.7.1`, and the
# default index resolves that to the CUDA build, which drags ~6GB of nvidia-*
# wheels into an image that never sees a GPU.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --timeout 600 torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --timeout 600 -r /tmp/requirements.txt \
 && pip install --timeout 600 pytest pytest-timeout ipython

# --- wire `import mod` / `m` to the mounted tree ----------------------------
# A .pth line starting with `import` is executed by site.py at startup, so this
# resolves the tree location at run time instead of freezing one path at build
# time (orbit/build mounts it at ~/mod for a non-root user, compose at /root/mod).
RUN printf '%s\n' \
    'import os,sys;[sys.path.insert(0,p) for p in ("/root/mod",os.path.expanduser("~/mod"),"/app/mod","/mod") if os.path.isfile(os.path.join(p,"mod","__init__.py")) and p not in sys.path]' \
    > "$(python3 -c 'import site;print(site.getsitepackages()[0])')/mod_tree.pth" \
 && printf '%s\n' \
    '#!/usr/bin/env python3' \
    'import sys' \
    'from mod import main' \
    'sys.argv[0] = sys.argv[0].removesuffix(".exe")' \
    'sys.exit(main())' > /usr/local/bin/m \
 && chmod +x /usr/local/bin/m \
 && ln -sf /usr/local/bin/m /usr/local/bin/c

COPY docker-entrypoint.sh /usr/local/bin/mod-entrypoint
RUN chmod +x /usr/local/bin/mod-entrypoint

WORKDIR /root/mod

# gateway · app · api · the band pm.docker hands to mods run inside
EXPOSE 3000 3001 8000 50950-50999

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/mod-entrypoint"]
CMD ["idle"]
