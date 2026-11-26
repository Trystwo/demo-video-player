# Desktop Icons NG (DING) — vendored for the offline installer

- Upstream: https://gitlab.com/rastersoft/desktop-icons-ng
- Debian package: `gnome-shell-extension-desktop-icons-ng` 48.1.0-1 (all)
- Source: https://deb.debian.org/debian/pool/main/g/gnome-shell-extension-desktop-icons-ng/
- License: GPL-3.0-or-later (see the header of each .js file)

Why it is vendored: the board image ships GNOME Shell 48 on Wayland without any
desktop-icons extension, and `gnome-shell-extension-desktop-icons-ng` is not in
the configured apt sources. `install.sh` copies this tree into
`~/.local/share/gnome-shell/extensions/ding@rastersoft.com/` and compiles the
GSettings schema into `~/.local/share/glib-2.0/schemas/`, so a freshly flashed
board gets desktop icons with no network access.

Contents:
- `ding@rastersoft.com/`  the extension itself (30 files)
- `schemas/`              `org.gnome.shell.extensions.ding.gschema.xml`

Note: GNOME Shell scans extension directories only at startup. After installing
on a running Wayland session, log out and back in once (or reboot) to load it.
