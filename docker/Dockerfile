# MOG Server: one image that serves the API and runs sandboxed installs -
# unlike RomM's split (an Alpine web image + a separate Debian install-sandbox
# worker image), MOG only ever needs the Debian-based runtime, since install
# is the whole point: Wine/Proton/X11 are unreliable on Alpine/musl.
#
#   - bubblewrap: unprivileged sandbox that confines the installer process
#   - Xvfb + x11vnc + websockify/noVNC: headless display streamed to the browser
#   - icewm: minimal window manager - without one, installer windows
#     (InstallShield/Inno Setup wizards especially) can get stuck unmapped or
#     at their placeholder 1x1 geometry, since they rely on a WM to actually
#     negotiate their real size
#   - picom: compositing manager - modern wizards commonly draw their page as
#     a layered (WS_EX_LAYERED) window for rounded corners/gradients, which
#     renders fully transparent without something actually compositing it
#   - Proton (GE-Proton / Proton-CachyOS, downloaded at runtime) / Wine: runs
#     Windows installers
#   - tesseract-ocr + xdotool: the experimental auto mode reads installer
#     dialogs and presses their buttons
#   - GTK 2: GOG's Linux installers (.sh, MojoSetup) draw their window with it
#   - 7-Zip: archive/disc-image listing and extraction (see backend/utils/archives.py).
#     Debian's 7zip package is built without the unRAR code, so it lists a RAR but
#     cannot unpack it ("Unsupported Method"): the official build goes ahead of it
#     on PATH.

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim-trixie

ENV DEBIAN_FRONTEND=noninteractive

# Wine needs the i386 arch for 32-bit Windows installers. libvulkan1/
# mesa-vulkan-drivers: GE-Proton's own launcher script loads libvulkan.so.1
# at import time regardless of whether an install actually needs 3D.
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    bubblewrap \
    xvfb \
    x11vnc \
    websockify \
    novnc \
    xdotool \
    icewm \
    picom \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-ita \
    tesseract-ocr-deu \
    tesseract-ocr-fra \
    tesseract-ocr-spa \
    tesseract-ocr-jpn \
    tesseract-ocr-chi-sim \
    tesseract-ocr-chi-tra \
    wine \
    wine32:i386 \
    ca-certificates \
    curl \
    libvulkan1 \
    mesa-vulkan-drivers \
    p7zip-full \
    libarchive-tools \
    xz-utils \
    libgtk2.0-0t64 \
    gtk2-engines-pixbuf \
    gtk2-engines-murrine \
    && rm -rf /var/lib/apt/lists/*

# The official 7-Zip (it has the unRAR code Debian's lacks), checked against its known hash. Only the amd64
# build is pinned; other architectures keep Debian's 7z, which cannot unpack RAR archives.
ARG SEVENZIP_VERSION=2501
ARG SEVENZIP_AMD64_SHA256=4ca3b7c6f2f67866b92622818b58233dc70367be2f36b498eb0bdeaaa44b53f4
RUN if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
    curl -fsSL -o /tmp/7zip.tar.xz "https://www.7-zip.org/a/7z${SEVENZIP_VERSION}-linux-x64.tar.xz" && \
    echo "${SEVENZIP_AMD64_SHA256}  /tmp/7zip.tar.xz" | sha256sum -c - && \
    tar -xJf /tmp/7zip.tar.xz -C /tmp 7zz && \
    install -m 755 /tmp/7zz /usr/local/bin/7z && \
    rm -f /tmp/7zip.tar.xz /tmp/7zz; \
    else echo "warning: no official 7-Zip pinned for $(dpkg --print-architecture); RAR archives cannot be unpacked"; \
    fi

# Xvfb (run as the unprivileged mog user, never root) refuses to start unless
# /tmp/.X11-unix is owned by root with the sticky bit set.
RUN mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

RUN if [ -f /usr/share/novnc/vnc_lite.html ] && [ ! -f /usr/share/novnc/vnc.html ]; then \
    ln -s /usr/share/novnc/vnc_lite.html /usr/share/novnc/vnc.html; \
    fi

# IceWM preference overrides - see the file itself for why FocusOnMapTransient
# needs forcing on. Loaded via icewm's own --config flag, not
# $HOME/.icewm/preferences, so it applies regardless of $HOME.
COPY ./docker/icewm-preferences /etc/icewm-preferences

RUN mkdir -p /opt/proton && chmod 755 /opt/proton

WORKDIR /src
COPY ./pyproject.toml /src/
RUN pip install --no-cache-dir -e .

COPY ./backend /src/backend
COPY ./frontend /src/frontend
WORKDIR /src/backend

# Unprivileged user; bubblewrap needs unprivileged user namespaces enabled on
# the host kernel (kernel.unprivileged_userns_clone=1).
RUN useradd --create-home --uid 10001 mog && \
    mkdir -p /mog && chown -R mog:mog /mog /opt/proton

COPY ./docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ARG MOG_VERSION=dev
ENV MOG_VERSION=${MOG_VERSION}
ENV INSTALL_SANDBOX_ENABLED=true
ENV PROTON_INSTALL_ROOT=/opt/proton

EXPOSE 5000

# Starts as root (no USER here) so entrypoint.sh can fix bind-mounted
# volume ownership before dropping to the unprivileged `mog` user - see
# that script's own comment for why this is needed at all.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python3", "main.py"]
