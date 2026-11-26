"""ui-v5 palette tokens and the window stylesheet.

The eight semantic tokens frozen by ui-v5 are ``bg``, ``panel``, ``line``,
``fg``, ``muted``, ``dim``, ``accent`` and ``annotation``.  Amber is the only
emphasis colour; ``annotation`` is the steel-blue picture-annotation colour and
must not appear in the widget rules (the Cairo overlays read it through
:func:`rgba`).  The remaining names cover the surfaces, text steps and
play-button shades the v4 stylesheet already used, snapped onto the same
scale, so no rule below needs a literal colour.

GTK reads the ``@define-color`` block; tests parse the same strings, so the
token table is the single source of truth for both the stylesheet and the
Python-drawn overlays.
"""

from __future__ import annotations

#: ui-v5 palette: name -> ``#RRGGBB``.  Keep the eight semantic values frozen.
TOKENS: dict[str, str] = {
    "bg": "#0E1216",  # window / headerbar / page base
    "bg_deep": "#0B0E12",  # drop zone, disabled surfaces
    "panel": "#151A20",  # sidebar, transport bar, playlist rows
    "panel_2": "#1A2027",  # buttons, raised rows
    "panel_3": "#202830",  # hover and pressed surfaces
    "line": "#232B33",  # 1 px separators and card edges
    "line_2": "#2D3742",  # control borders
    "line_3": "#3A4147",  # hover borders, muted dots
    "fg": "#E9E5DE",  # primary text
    "fg_2": "#CFCAC2",  # secondary text and glyphs
    "muted": "#98A0A8",  # labels and hints
    "dim": "#6C757E",  # least important text
    "accent": "#E9B756",  # the only emphasis colour
    "accent_hi": "#F3CF8C",  # accent text on tinted surfaces, hover
    "accent_ink": "#1A1204",  # text and knobs on filled accent
    "annotation": "#7EA8C4",  # picture annotations only (Cairo, no CSS rules)
    "warn": "#D9A85C",  # warning text
    "ok": "#7FB58A",  # readiness dot (hardware decode available)
    "danger": "#D96A5E",  # error text
    "play": "#ECE8E2",  # play button and slider knob
    "play_hi": "#F7F3EE",  # play button hover
    "play_active": "#D6D1C9",  # play button pressed
    "on_play": "#10151A",  # glyph on the play button
    "on_media": "#FFFFFF",  # subtitle over the picture
    "video": "#000000",  # video letterbox and picture shadows
}

#: The ``@define-color`` block loaded before the rules.
TOKENS_CSS: str = (
    "/* ui-v5 palette -- do not use literal colours in the rules below. */\n"
    + "\n".join(f"@define-color {name} {value};" for name, value in TOKENS.items())
    + "\n"
)

#: Widget rules.  Every colour is a token, ``alpha(@token, …)`` or
#: ``shade(@token, …)``; ``tests/test_theme.py`` enforces this.
RULES: str = """\
/* ui-v5 graphite screening room.  Amber is the only emphasis colour and it
   lives on the controls (timeline, sliders, selection, focus, checked
   switches, primary buttons); the picture keeps its own steel-blue
   annotation layer.  The brightest surface is still the play button. */
window.cinema-window {
  background-color: @bg;
  color: @fg;
}
headerbar.cinema-headerbar {
  background-color: @bg;
  background-image: none;
  color: @fg;
  border-bottom: 1px solid @line;
  box-shadow: none;
}
headerbar button {
  min-height: 44px;
  padding: 0 12px;
  background-image: none;
  box-shadow: none;
}
/* The header keeps one window-level action.  White is reserved for play, so
   Open is a ghost pill and the two window actions stay dark. */
button.header-ghost {
  background-color: transparent;
  color: @fg_2;
  border: 1px solid @line_2;
  background-image: none;
  box-shadow: none;
}
button.header-ghost:hover {
  background-color: @panel_2;
  border-color: @line_3;
  color: @fg;
}
.video-surface {
  background-color: @video;
  border: 1px solid @line;
  border-radius: 12px;
}
.video-surface picture { background-color: @video; }
.player-column { color: @fg; }
.sidebar-panel {
  background-color: @panel;
  color: @fg;
  border: none;
  border-radius: 12px;
  padding: 12px;
}
scrolledwindow.sidebar-scroll {
  background-color: @panel;
  border: none;
}
scrolledwindow.playlist-scroll,
list.cinema-playlist {
  background-color: @panel;
  color: @fg;
}
separator {
  min-height: 1px;
  min-width: 1px;
  background-color: @line;
  background-image: none;
  border: none;
  box-shadow: none;
}
paned.cinema-paned > separator { background-color: @line; min-width: 1px; }
.section-heading {
  color: @fg;
  font-size: 13pt;
  font-weight: 700;
}
list.cinema-playlist row {
  min-height: 44px;
  padding: 4px 8px;
  border-radius: 8px;
  background-color: @panel;
  background-image: none;
  color: @fg_2;
}
list.cinema-playlist row:hover { background-color: @panel_2; }
list.cinema-playlist row:selected {
  background-color: alpha(@accent, 0.16);
  background-image: none;
  color: @accent_hi;
  border-left: 3px solid @accent;
}
list.cinema-playlist row:selected label { color: @accent_hi; }
list.cinema-playlist row.recent-row {
  min-height: 40px;
  padding: 2px 8px;
}
label.recent-name {
  font-size: 12pt;
}
label.recent-meta {
  font-size: 9pt;
  color: @muted;
}
label.recents-empty {
  font-size: 10pt;
  color: @dim;
  padding: 2px 4px 6px 4px;
}

/* ---- empty state ------------------------------------------------------
   No clip means no transport: the picture area becomes one large drop target
   with the only two actions that can actually do something. */
.dropzone {
  border: 1px dashed @line_3;
  border-radius: 16px;
  background-color: @bg_deep;
}
.dropzone.drag-active {
  border-color: @accent;
  background-color: alpha(@accent, 0.06);
}
.dropzone-icon {
  background-color: @panel;
  border: 1px solid @line;
  border-radius: 26px;
  padding: 12px;
  color: @accent_hi;
}
.dropzone-title {
  color: @fg;
  font-size: 20pt;
  font-weight: 700;
}
.dropzone-subtitle {
  color: @muted;
  font-size: 10.5pt;
}
.dropzone-hint {
  color: @dim;
  font-size: 9.5pt;
}
button.drop-primary {
  background-color: @accent;
  color: @accent_ink;
  border: none;
  background-image: none;
  min-height: 44px;
  padding: 0 22px;
  border-radius: 10px;
  font-weight: 600;
}
button.drop-primary:hover { background-color: @accent_hi; }
button.drop-secondary {
  background-color: transparent;
  color: @fg_2;
  border: 1px solid @line_2;
  background-image: none;
  min-height: 44px;
  padding: 0 20px;
  border-radius: 10px;
}
button.drop-secondary:hover {
  background-color: @panel_2;
  border-color: @line_3;
  color: @fg;
}
.capability-chip {
  background-color: @bg;
  border: 1px solid @line;
  border-radius: 999px;
  color: @muted;
  font-size: 9pt;
  padding: 5px 12px;
}

/* ---- sidebar tabs (v4: playlist / beauty pages) ----------------------
   Same segmented language as the decode row: a dark trough with one steel
   tinted selected segment, so the two page entrances read as one control. */
.sidebar-tabs {
  border: 1px solid @line_2;
  border-radius: 10px;
  background-color: @bg;
  padding: 3px;
}
.sidebar-panel .sidebar-tabs button {
  min-height: 44px;
  padding: 0 10px;
  border: none;
  border-radius: 7px;
  background-color: transparent;
  background-image: none;
  box-shadow: none;
  color: @muted;
  font-size: 10pt;
  font-weight: 600;
}
.sidebar-panel .sidebar-tabs button:hover {
  background-color: @panel_3;
  color: @fg_2;
}
.sidebar-panel .sidebar-tabs button:checked {
  background-color: alpha(@accent, 0.16);
  background-image: none;
  color: @accent_hi;
}

/* ---- beauty page (v4) ------------------------------------------------ */
.sidebar-page { padding: 0 2px; }
.sidebar-panel switch {
  min-width: 48px;
  min-height: 32px;
  margin: 4px 0;
  background-color: @line;
  border: 1px solid @line;
}
.sidebar-panel switch:checked {
  background-color: @accent;
  border-color: @accent;
}
.sidebar-panel switch:checked slider { background-color: @accent_ink; }
.sidebar-panel switch slider { background-color: @fg; }
label.beauty-title { color: @fg; font-size: 13pt; font-weight: 700; }
label.beauty-status { color: @muted; font-size: 8.5pt; font-family: monospace; }
label.beauty-status-dot { color: @accent; font-size: 8pt; }
.beauty-page.is-off label.beauty-status-dot { color: @line_3; }
.beauty-page.is-off label.beauty-status { color: @dim; }
/* The master switch stays clickable while the page is off, but it must read
   as off: the theme's white knob is the brightest thing in the greyed page. */
.beauty-page.is-off switch {
  background-color: @line;
  border-color: @line;
}
.beauty-page.is-off switch slider { background-color: @dim; }
label.beauty-cascade {
  color: @accent_hi;
  font-size: 8.5pt;
  background-color: alpha(@accent, 0.07);
  border-left: 2px solid @accent;
  border-top-right-radius: 8px;
  border-bottom-right-radius: 8px;
  padding: 8px 10px;
}
label.beauty-slider-label { color: @fg_2; font-size: 10pt; }
label.beauty-slider-value { color: @muted; font-size: 8.5pt; font-family: monospace; }
label.beauty-section-heading { color: @fg; font-size: 10pt; font-weight: 700; }
.beauty-settings {
  background-color: @panel_2;
  border: 1px solid @line;
  border-radius: 10px;
}
/* min-height is the content box in GTK CSS, so the mockup's 52 px row is the
   label pair plus this padding; the switch already forces 40 px of height. */
.beauty-settings .settings-row { padding: 6px 12px; min-height: 0; }
label.settings-title { color: @fg_2; font-size: 10pt; }
label.settings-hint { color: @dim; font-size: 8pt; }
.beauty-split-row { padding: 3px 12px 5px 12px; min-height: 0; }
label.split-title { color: @muted; font-size: 8.5pt; }
label.split-value { color: @muted; font-size: 8.5pt; font-family: monospace; }
.sidebar-panel .beauty-slider scale { min-height: 34px; }
.sidebar-panel .beauty-split-row scale { min-height: 28px; }
.beauty-split-row scale trough { min-height: 4px; }
.beauty-split-row scale slider { min-width: 14px; min-height: 14px; }
.sidebar-panel button.beauty-more-row {
  background-color: @panel_2;
  border: 1px solid @line;
  border-radius: 10px;
  min-height: 34px;
  padding: 0 10px;
  background-image: none;
  box-shadow: none;
  color: @muted;
}
.sidebar-panel button.beauty-more-row:hover {
  background-color: @panel_3;
  border-color: @line_2;
}
.sidebar-panel button.beauty-more-row:checked { background-color: @panel_3; }
label.beauty-more-title { color: @muted; font-size: 10pt; }
label.beauty-more-hint { color: @dim; font-size: 8pt; }
label.beauty-more-chevron { color: @muted; font-size: 10pt; }
label.beauty-note { color: @dim; font-size: 8pt; }
label.experimental-heading { color: @muted; font-size: 9pt; font-weight: 700; }
button.playlist-add {
  background-color: transparent;
  color: @muted;
  border: 1px dashed @line_2;
  border-radius: 8px;
  min-height: 40px;
  padding: 0 10px;
  background-image: none;
}
button.playlist-add:hover {
  color: @fg_2;
  border-color: @line_3;
  background-color: @panel_2;
}
label.empty-playlist {
  color: @dim;
  font-size: 10pt;
  border: 1px dashed @line_2;
  border-radius: 8px;
  padding: 14px 10px;
}
.sidebar-shortcuts {
  color: @dim;
  font-size: 8.5pt;
  padding: 2px 4px 2px 4px;
}

/* ---- two-row transport: timeline on top, 44 px keys underneath ------- */
.transport-bar {
  background-color: @panel;
  border: 1px solid @line;
  border-radius: 12px;
  padding: 6px 12px 8px 12px;
}
.transport-bar.docked {
  background-color: transparent;
  border: none;
  padding: 0;
}
.seek-row { padding: 0 2px; }
.button-row { padding: 0 2px; }
button.transport-button,
button.header-action,
.sidebar-panel button {
  background-color: @panel_2;
  color: @fg;
  border: none;
  background-image: none;
  box-shadow: none;
}
button.secondary-action,
button.accent-button,
dropdown > button {
  background-image: none;
  box-shadow: none;
  border: none;
}
button.header-action:checked,
button.transport-button:checked {
  background-color: alpha(@accent, 0.18);
  color: @accent_hi;
  background-image: none;
}
button.transport-button,
button.header-action {
  min-width: 44px;
  min-height: 44px;
  padding: 0 12px;
  border-radius: 10px;
}
button.transport-button.image-button { padding: 0; }
menubutton.transport-button > button {
  background-color: @panel_2;
  color: @fg;
  background-image: none;
  border: none;
  box-shadow: none;
  min-width: 44px;
  min-height: 44px;
  padding: 0 14px;
  border-radius: 10px;
}
menubutton.transport-button > button:hover { background-color: @panel_3; }
menubutton.transport-button > button:active { background-color: @panel_3; }
button.transport-button:hover,
button.header-action:hover,
button.secondary-action:hover,
button.accent-button:hover,
dropdown > button:hover {
  background-image: none;
  box-shadow: none;
}
button.transport-button:hover,
button.header-action:hover,
button.secondary-action:hover { background-color: @panel_3; }
button:disabled,
dropdown > button:disabled {
  background-color: @bg_deep;
  background-image: none;
  color: @dim;
  border: none;
  box-shadow: none;
}
button.transport-primary {
  background-color: @play;
  color: @on_play;
  border: none;
  min-width: 44px;
  min-height: 44px;
  padding: 0;
  border-radius: 22px;
}
button.transport-primary:hover { background-color: @play_hi; }
button.transport-primary:active { background-color: @play_active; }
button.transport-ghost {
  background-color: transparent;
  border: 1px solid @line_2;
  color: @muted;
}
button.transport-ghost:hover {
  background-color: @panel_3;
  border-color: @line_3;
  color: @fg;
}
.transport-divider {
  min-width: 1px;
  background-color: @line_2;
  margin: 6px 3px;
}
button.secondary-action {
  background-color: @panel_3;
  color: @fg;
  border: none;
}
button.accent-button {
  background-color: @accent;
  color: @accent_ink;
  border: none;
}
button.accent-button:hover { background-color: @accent_hi; }
button.accent-button:active { background-color: shade(@accent, 0.88); }
button:focus,
scale:focus,
dropdown:focus {
  outline: none;
  box-shadow: none;
}
button:focus-visible,
scale:focus-visible,
dropdown:focus-visible {
  outline: 2px solid @accent;
  outline-offset: 2px;
}
scale trough {
  min-height: 6px;
  border-radius: 999px;
  background-color: @panel_3;
  background-image: none;
  border: none;
  box-shadow: none;
}
scale highlight {
  border-radius: 999px;
  background-color: @accent;
  box-shadow: none;
}
scale slider {
  min-width: 18px;
  min-height: 18px;
  background-color: @play;
  background-image: none;
  border: 2px solid @accent;
  box-shadow: none;
}
.transport-bar scale { min-height: 30px; }
.transport-bar scale trough { min-height: 4px; }
.transport-bar scale slider { min-width: 14px; min-height: 14px; }
.sidebar-panel scale { min-height: 38px; }
.sidebar-panel scale slider { min-width: 18px; min-height: 18px; }
.volume-icon { color: @muted; }
.volume-value {
  color: @fg_2;
  min-width: 38px;
  font-size: 9pt;
}
.time-display {
  color: @fg_2;
  font-family: monospace;
  font-size: 9.5pt;
}
.sidebar-panel label.dim-label { color: @muted; }
dropdown > button {
  min-height: 44px;
  background-color: @panel_2;
  color: @fg;
}
.decode-segments {
  border: 1px solid @line_2;
  border-radius: 8px;
  background-color: @bg;
  padding: 3px;
}
.decode-segments button {
  min-height: 36px;
  min-width: 54px;
  padding: 0 10px;
  font-size: 10.5pt;
  border: none;
  border-radius: 6px;
  background-image: none;
  box-shadow: none;
  background-color: transparent;
  color: @muted;
}
.decode-segments button:hover {
  background-color: @panel_3;
  color: @fg_2;
}
.decode-segments button:checked {
  background-color: alpha(@accent, 0.18);
  color: @accent_hi;
}

/* ---- labelled info row ----------------------------------------------- */
.info-bar { padding: 2px 2px 0 2px; }
label.info-label {
  color: @muted;
  font-size: 9pt;
}
label.info-value {
  color: @fg;
  font-family: monospace;
  font-size: 9pt;
}
label.info-live {
  color: @accent_hi;
  font-family: monospace;
  font-size: 9pt;
}
label.info-dot {
  color: @accent;
  font-size: 10pt;
}
label.info-dot.is-off { color: @line_3; }
label.info-separator {
  color: @line_3;
  font-size: 9pt;
}

/* ---- fullscreen dock / persistent telemetry -------------------------- */
.transport-dock {
  background-color: alpha(@bg, 0.96);
  border: 1px solid alpha(@play, 0.10);
  border-top-color: alpha(@play, 0.16);
  border-radius: 12px;
  padding: 6px 12px 8px 12px;
  box-shadow: 0 -10px 30px alpha(@video, 0.50);
}
.transport-dock scale { min-height: 22px; }
.transport-dock scale trough { min-height: 3px; }
.transport-dock scale slider { min-width: 12px; min-height: 12px; }
.transport-dock .time-display { font-size: 9pt; }
.transport-dock button.transport-button,
.transport-dock menubutton.transport-button > button {
  min-width: 44px;
  min-height: 44px;
  border-radius: 8px;
}
.transport-dock button.transport-primary {
  min-width: 44px;
  min-height: 44px;
  border-radius: 22px;
}
.transport-dock dropdown > button { min-height: 44px; }
.transport-dock .decode-segments button {
  min-height: 34px;
  min-width: 46px;
  padding: 0 8px;
  font-size: 9pt;
}
.transport-dock .transport-divider { margin: 4px 2px; }
button.dock-pill {
  background-color: @panel_2;
  color: @fg_2;
  border: 1px solid @line_2;
  border-radius: 10px;
  min-height: 44px;
  padding: 0 14px;
  background-image: none;
}
button.dock-pill:checked {
  background-color: alpha(@accent, 0.30);
  background-image: none;
  border-color: alpha(@accent, 0.75);
  color: @accent_hi;
  box-shadow: none;
}
button.dock-pill:hover { background-color: @panel_3; }
button.dock-pill:checked:hover { background-color: alpha(@accent, 0.38); }
/* v4: the compare pill needs beauty; while beauty is off it stays visible but
   reads as unavailable instead of disappearing from the dock. */
button.dock-pill:disabled { opacity: 0.34; }
label.fullscreen-telemetry {
  color: alpha(@fg, 0.92);
  font-family: monospace;
  font-size: 9.5pt;
  text-shadow: 0 1px 4px alpha(@video, 0.90);
}
label.fullscreen-telemetry-live { color: @accent_hi; }

/* ---- transient hint / status / subtitles ----------------------------- */
.hint-toast {
  background-color: alpha(@bg, 0.88);
  border: 1px solid alpha(@play, 0.12);
  border-radius: 10px;
  padding: 6px 14px;
  font-size: 10pt;
  color: @fg_2;
}
label.hint-toast.warn-hint { color: @warn; border-color: alpha(@warn, 0.45); }
label.hint-toast.error-hint { color: @danger; border-color: alpha(@danger, 0.45); }
.header-title {
  font-size: 10.5pt;
  font-weight: 600;
  color: @fg;
}
.header-subtitle {
  font-size: 8pt;
  color: @dim;
  margin-top: 1px;
}
.playback-status-card {
  color: @fg;
  background-color: alpha(@bg, 0.94);
  border: 1px solid @line_2;
  border-radius: 14px;
  padding: 20px 24px;
  min-width: 360px;
}
.playback-status-card.status-paused {
  background-color: alpha(@bg, 0.78);
  border-color: alpha(@fg, 0.14);
}
.status-title {
  color: @fg;
  font-size: 15pt;
  font-weight: 700;
}
.status-message {
  color: @muted;
  font-size: 10pt;
}
image.status-symbol { color: @accent; }
image.status-error-symbol { color: @danger; }
spinner.status-spinner { color: @accent; }
.status-actions { margin-top: 4px; }
.status-actions button {
  min-height: 44px;
  min-width: 108px;
  padding: 0 18px;
}
.error-hint { color: @danger; }
.warn-hint { color: @warn; }
label.subtitle-overlay {
  color: @on_media;
  font-size: 21px;
  font-weight: 600;
  text-shadow: 0 2px 6px alpha(@video, 0.95);
  background-color: alpha(@video, 0.34);
  border-radius: 8px;
  padding: 2px 12px;
}

/* ---- ui-v5 browse shell: left rail ----------------------------------- */
.browse-shell { background-color: @bg; }
.rail {
  background-color: @bg_deep;
  border-right: 1px solid @line;
  padding: 14px 0 12px 0;
}
label.rail-mark {
  background-color: alpha(@accent, 0.08);
  border: 1px solid alpha(@accent, 0.34);
  border-radius: 12px;
  color: @accent_hi;
  font-size: 14pt;
  font-weight: 600;
  min-width: 42px;
  min-height: 42px;
}
button.rail-item {
  background-color: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
  border-radius: 11px;
  color: @dim;
  min-width: 66px;
  min-height: 60px;
  padding: 6px 0;
}
button.rail-item:hover {
  background-color: @panel_2;
  color: @fg_2;
}
button.rail-item:checked {
  background-color: alpha(@accent, 0.14);
  color: @accent_hi;
}
button.rail-item:disabled { opacity: 0.40; }
label.rail-label { font-size: 8pt; }
label.rail-status { font-size: 8pt; color: @dim; }
label.rail-clock {
  font-family: monospace;
  font-size: 8pt;
  color: @dim;
}
label.rail-dot { color: @ok; font-size: 7pt; }
label.rail-dot.is-off { color: @dim; }

/* ---- library page ----------------------------------------------------- */
.library-page {
  background-color: @bg;
  padding: 12px 26px 10px 26px;
}
label.page-title {
  font-size: 20pt;
  font-weight: 700;
  color: @fg;
}
label.page-stat {
  font-family: monospace;
  font-size: 10.5pt;
  color: @accent_hi;
}
label.page-sub { font-size: 9.5pt; color: @dim; }
button.tool {
  background-color: @panel_2;
  background-image: none;
  border: 1px solid @line_2;
  border-radius: 9px;
  box-shadow: none;
  color: @fg_2;
  font-size: 9.5pt;
  min-height: 38px;
  padding: 0 12px;
}
button.tool:hover {
  background-color: @panel_3;
  border-color: @line_3;
  color: @fg;
}
/* Gtk.MenuButton is a ``menubutton`` node whose inner button does not carry
   the ``tool`` class; without this rule the sort control falls back to the
   theme's light button (the same trap as the transport CC menu button). */
menubutton.tool > button {
  background-color: @panel_2;
  background-image: none;
  border: 1px solid @line_2;
  border-radius: 9px;
  box-shadow: none;
  color: @fg_2;
  font-size: 9.5pt;
  min-height: 38px;
  padding: 0 12px;
}
menubutton.tool > button:hover {
  background-color: @panel_3;
  border-color: @line_3;
  color: @fg;
}
menubutton.tool > button:active {
  background-color: @panel_3;
  border-color: @line_3;
}
menubutton.tool popover > contents {
  background-color: @panel;
  border: 1px solid @line_2;
  border-radius: 10px;
  box-shadow: none;
}
button.tool-square {
  min-width: 38px;
  padding: 0;
}
label.tool-key { color: @dim; font-size: 9pt; }
label.tool-value { color: @fg; font-size: 9.5pt; font-weight: 600; }
button.fchip {
  background-color: transparent;
  background-image: none;
  border: 1px solid @line_2;
  border-radius: 19px;
  box-shadow: none;
  color: @muted;
  font-size: 9.5pt;
  min-height: 38px;
  padding: 0 14px;
}
button.fchip:hover { border-color: @line_3; color: @fg; }
button.fchip:checked {
  background-color: alpha(@accent, 0.16);
  border-color: alpha(@accent, 0.55);
  color: @accent_hi;
}
label.fchip-count {
  color: @dim;
  font-family: monospace;
  font-size: 8.5pt;
}
button.fchip:checked label.fchip-count { color: @accent_hi; }
scrolledwindow.library-scroll {
  background-color: @bg;
  border: none;
}
.library-grid { background-color: @bg; }
button.lib-card {
  background-color: transparent;
  background-image: none;
  border: none;
  border-radius: 12px;
  box-shadow: none;
  min-width: 220px;
  padding: 0 0 2px 0;
}
button.lib-card:hover { background-color: alpha(@play, 0.05); }
button.lib-card:focus-visible { outline: 2px solid @accent; }
label.lib-title {
  color: @fg;
  font-size: 10pt;
  font-weight: 500;
}
label.lib-meta { color: @dim; font-size: 8.5pt; }
label.lib-foot { color: @dim; font-size: 8.5pt; }
label.lib-foot-path {
  color: @dim;
  font-family: monospace;
  font-size: 8pt;
}
label.lib-empty-note { color: @dim; font-size: 9pt; }
.library-page.drag-active { background-color: alpha(@accent, 0.05); }

/* ---- library empty board --------------------------------------------- */
.empty-board {
  background-color: @bg_deep;
  border: 1px solid @line;
  border-radius: 14px;
}
label.eb-title {
  color: @fg;
  font-size: 20pt;
  font-weight: 700;
}
label.eb-body {
  color: @muted;
  font-size: 10pt;
}
button.link-button {
  background-color: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
  color: @muted;
  font-size: 9.5pt;
  min-height: 44px;
  padding: 0 10px;
}
button.link-button:hover { color: @fg; }
.eb-bar {
  border-top: 1px solid @line;
  padding: 0 18px;
}
label.eb-bar-label {
  color: @dim;
  font-size: 8.5pt;
  min-width: 56px;
}
label.dir-chip {
  background-color: @panel_2;
  border: 1px solid @line;
  border-radius: 7px;
  color: @fg_2;
  font-family: monospace;
  font-size: 8.5pt;
  padding: 4px 8px;
}
label.dir-chip.is-missing { color: @warn; }
label.fmt-chip {
  background-color: @panel;
  border: 1px solid @line;
  border-radius: 7px;
  color: @muted;
  font-size: 8.5pt;
  padding: 4px 8px;
}
button.dir-remove {
  background-color: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
  color: @dim;
  min-height: 28px;
  min-width: 28px;
  padding: 0;
}
button.dir-remove:hover { color: @danger; }
button.header-back {
  background-color: transparent;
  background-image: none;
  border: 1px solid @line_2;
  border-radius: 9px;
  box-shadow: none;
  color: @fg_2;
  min-height: 40px;
  min-width: 40px;
  padding: 0;
}
button.header-back:hover { background-color: @panel_2; color: @fg; }
label.header-back-glyph { font-size: 17pt; color: @fg_2; }

/* ---- home page -------------------------------------------------------- */
.home-page { background-color: @bg; }
.browse-shell.drag-active { background-color: alpha(@accent, 0.04); }
.hero-device-pill {
  background-color: alpha(@video, 0.40);
  border-radius: 7px;
  padding: 3px 9px;
}
label.hero-device {
  font-family: monospace;
  font-size: 8pt;
  color: @fg_2;
}
label.hero-device-dot { font-size: 6pt; color: @muted; }
label.hero-kicker { font-size: 8.5pt; color: @accent; }
label.hero-title {
  font-size: 30pt;
  font-weight: 700;
  color: @fg;
}
label.hero-meta { font-size: 9.5pt; color: @fg_2; }
label.hero-path {
  font-family: monospace;
  font-size: 8pt;
  color: @dim;
}
label.hero-empty-title {
  font-size: 20pt;
  font-weight: 700;
  color: @fg;
}
label.hero-empty-body { font-size: 9.5pt; color: @muted; }
.home-row { background-color: transparent; }
.row-head {
  background-color: transparent;
  margin-bottom: 9px;
}
label.row-title {
  font-size: 13pt;
  font-weight: 700;
  color: @fg;
}
label.row-count { font-size: 8.5pt; color: @dim; }
label.row-note { font-size: 8.5pt; color: @dim; }
button.row-more {
  background-color: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
  color: @muted;
  font-size: 9pt;
  min-height: 40px;
  padding: 0 8px;
}
button.row-more:hover { color: @fg; background-color: alpha(@play, 0.05); border-radius: 8px; }

/* ---- primary / ghost calls to action (home hero, detail backdrop) ------ */
button.btn-primary {
  background-color: @accent;
  background-image: none;
  border: none;
  border-radius: 10px;
  box-shadow: none;
  color: @accent_ink;
  font-size: 10pt;
  font-weight: 600;
  min-height: 46px;
  padding: 0 20px;
}
button.btn-primary:hover { background-color: @accent_hi; }
button.btn-primary:active { background-color: @accent; }
button.btn-primary label { color: @accent_ink; }
button.btn-ghost {
  background-color: alpha(@play, 0.06);
  background-image: none;
  border: 1px solid alpha(@play, 0.12);
  border-radius: 10px;
  box-shadow: none;
  color: @fg;
  font-size: 10pt;
  min-height: 46px;
  padding: 0 20px;
}
button.btn-ghost:hover { background-color: alpha(@play, 0.10); }
button.btn-ghost label { color: @fg; }

/* ---- detail page ------------------------------------------------------ */
.detail-page { background-color: @bg; }
button.detail-back {
  background-color: alpha(@video, 0.55);
  background-image: none;
  border: 1px solid alpha(@play, 0.16);
  border-radius: 22px;
  box-shadow: none;
  min-height: 44px;
  min-width: 44px;
  padding: 0;
}
button.detail-back:hover { background-color: alpha(@video, 0.78); }
label.detail-back-glyph {
  font-size: 19pt;
  color: @play;
}
label.bd-title {
  font-size: 24pt;
  font-weight: 700;
  color: @fg;
}
label.bd-sub { font-size: 10.5pt; color: @fg_2; }
label.bd-path {
  font-family: monospace;
  font-size: 8pt;
  color: @dim;
}
label.bd-chip {
  background-color: alpha(@play, 0.055);
  border: 1px solid alpha(@play, 0.10);
  border-radius: 6px;
  color: @muted;
  font-size: 8pt;
  padding: 3px 8px;
}
label.bd-chip-mono { font-family: monospace; }
scrolledwindow.episode-scroll {
  background-color: transparent;
  border: none;
}
.episode-grid { background-color: transparent; }
button.ep-card {
  background-color: transparent;
  background-image: none;
  border: none;
  border-radius: 12px;
  box-shadow: none;
  padding: 0;
}
button.ep-card:hover { background-color: alpha(@play, 0.05); }
button.ep-card:focus-visible { outline: 2px solid @accent; }
label.ep-title { font-size: 9.5pt; color: @fg; }
button.ep-card.now label.ep-title { color: @accent_hi; }
label.ep-duration {
  font-family: monospace;
  font-size: 8pt;
  color: @dim;
}
.card-box {
  background-color: @panel;
  border: 1px solid @line;
  border-radius: 14px;
  padding: 12px 16px;
}
label.card-box-title {
  font-size: 9.5pt;
  color: @muted;
}
.spec-row {
  min-height: 22px;
  border-top: 1px solid alpha(@play, 0.045);
}
.spec-row:first-child { border-top: none; }
label.spec-key { font-size: 8.5pt; color: @muted; }
label.spec-value {
  font-family: monospace;
  font-size: 8.5pt;
  color: @fg_2;
}
label.spec-value.is-accent { color: @accent; }
button.rel-row {
  background-color: transparent;
  background-image: none;
  border: none;
  border-radius: 8px;
  box-shadow: none;
  padding: 3px 0;
}
button.rel-row:hover { background-color: alpha(@play, 0.03); }
label.rel-title { font-size: 9.5pt; color: @fg_2; }
label.rel-duration {
  font-family: monospace;
  font-size: 8pt;
  color: @dim;
}
label.detail-foot { font-size: 8.5pt; color: @dim; }
"""

CSS_TEXT: str = TOKENS_CSS + "\n" + RULES
#: Bytes for ``Gtk.CssProvider.load_from_data`` (kept under the old name).
CSS: bytes = CSS_TEXT.encode("utf-8")


def rgba(name: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    """Return a Cairo ``set_source_rgba`` tuple for a palette token.

    The Cairo-drawn layers (face brackets, split captions, progress hairline)
    cannot use CSS, so they read the same token table instead of repeating
    float triples.
    """
    value = TOKENS[name].lstrip("#")
    return (
        int(value[0:2], 16) / 255.0,
        int(value[2:4], 16) / 255.0,
        int(value[4:6], 16) / 255.0,
        float(alpha),
    )

