"""ui-v6 "Contact Sheet" palette tokens and the window stylesheet.

Scheme C in ``mockups/ui-v6/skin-c.css`` splits the old single emphasis colour
in two:

* **bone** (``play`` / ``play_hi`` / ``play_active``) is the *action* colour:
  the primary call to action, a checked switch, the current rail item, the
  current tab, the play key and every knob;
* **amber** (``accent`` / ``accent_hi``) is the *data* colour: progress,
  counters, live telemetry values and the filled part of a slider.

``annotation`` stays the steel-blue picture-annotation colour and must not
appear in the widget rules (the Cairo overlays read it through :func:`rgba`).
The remaining names cover cool-neutral surfaces, the mono metadata step and
the playback page; ``play`` keeps its ui-v5 name because the Cairo overlays and
the play key already read it, but its value is scheme C's bone.

GTK reads the ``@define-color`` block; tests parse the same strings, so the
token table is the single source of truth for both the stylesheet and the
Python-drawn overlays.
"""

from __future__ import annotations

#: ui-v6 palette: name -> ``#RRGGBB``.  The eight semantic values follow
#: ``mockups/ui-v6/skin-c.css``.
TOKENS: dict[str, str] = {
    "bg": "#0B0D0F",  # window / headerbar / page base
    "bg_deep": "#090B0D",  # rail, drop zone, disabled surfaces
    "panel": "#111417",  # cards, popovers, status card
    "panel_2": "#171B1F",  # raised rows, buttons, selected list row
    "panel_3": "#20262B",  # hover and pressed surfaces
    "line": "#20252A",  # 1 px separators and hairline frames
    "line_2": "#2C333A",  # control borders
    "line_3": "#3A434B",  # hover borders, muted dots
    "fg": "#ECEAE5",  # primary text
    "fg_2": "#C3C0B8",  # secondary text and glyphs
    "muted": "#98A0A7",  # labels and hints
    "dim": "#747C84",  # least important text
    "meta": "#828B93",  # monospace card metadata (scheme C card-meta-fg)
    "accent": "#D9A85C",  # DATA amber: progress, counters, filled sliders
    "accent_hi": "#EBC48C",  # amber text on dark surfaces
    "accent_ink": "#10130F",  # text on a filled amber surface
    "annotation": "#7EA8C4",  # picture annotations only (Cairo, no CSS rules)
    "warn": "#E0B878",  # warning text
    "ok": "#86B394",  # readiness dot (hardware decode available)
    "danger": "#D97568",  # error text, destructive hover
    "play": "#F1EDE4",  # scheme C bone: action fill, play key, knobs
    "play_hi": "#FBF8F1",  # bone hover
    "play_active": "#E3DED4",  # bone pressed
    "on_play": "#0B0D0F",  # glyph on a bone surface
    "on_media": "#FFFFFF",  # subtitle over the picture
    "video": "#000000",  # video letterbox and picture shadows
    "play_bg": "#050607",  # playback page band behind picture and transport
    "drawer": "#0E1114",  # right-hand episodes / beauty drawer
    "setting": "#0C0F11",  # inset beauty settings card
    "thumb_line": "#2A3036",  # poster hairline (scheme C thumb-border)
    "switch_off": "#262C31",  # switch trough while off
    "scrim": "#040607",  # poster scrim / duration plate
    "time": "#C9D0D5",  # monospace timecodes
    "telemetry": "#AEB5BB",  # telemetry values
}

#: The ``@define-color`` block loaded before the rules.
TOKENS_CSS: str = (
    "/* ui-v6 contact-sheet palette -- do not use literal colours below. */\n"
    + "\n".join(f"@define-color {name} {value};" for name, value in TOKENS.items())
    + "\n"
)

#: Widget rules.  Every colour is a token, ``alpha(@token, …)`` or
#: ``shade(@token, …)``; ``tests/test_theme.py`` enforces this.
RULES: str = """\
/* ui-v6 contact sheet.  Actions are bone, data is amber: the primary CTA, a
   checked switch, the current rail item/tab and the play key are bone; only
   progress, counters, live telemetry and slider fills keep the amber.  Header
   and page surfaces are cool neutral (#0B0D0F / #111417), hairline framed,
   with scheme C's 10 / 8 / 9 px radii. */
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
  border-radius: 9px;
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
  border-radius: 10px;
}
.video-surface picture { background-color: @video; }
.player-column { color: @fg; }
.sidebar-panel {
  background-color: @panel;
  color: @fg;
  border: none;
  border-radius: 10px;
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
  padding: 4px 10px;
  border-radius: 7px;
  background-color: transparent;
  background-image: none;
  color: @fg_2;
}
list.cinema-playlist row:hover { background-color: @panel_2; }
list.cinema-playlist row:selected {
  background-color: @panel_2;
  background-image: none;
  color: @fg;
  border-left: 2px solid @play;
}
list.cinema-playlist row:selected label { color: @fg; }
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
  border: 1px solid @line_2;
  border-radius: 10px;
  background-color: @bg_deep;
}
.dropzone.drag-active {
  border-color: @play;
  background-color: alpha(@play, 0.04);
}
.dropzone-icon {
  background-color: @panel;
  border: 1px solid @line;
  border-radius: 22px;
  padding: 12px;
  color: @play;
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
  background-color: @play;
  color: @on_play;
  border: 1px solid alpha(@on_media, 0.42);
  background-image: none;
  min-height: 44px;
  padding: 0 22px;
  border-radius: 9px;
  font-weight: 600;
}
button.drop-primary:hover { background-color: @play_hi; }
button.drop-secondary {
  background-color: transparent;
  color: @fg;
  border: 1px solid alpha(@fg, 0.20);
  background-image: none;
  min-height: 44px;
  padding: 0 20px;
  border-radius: 9px;
}
button.drop-secondary:hover {
  background-color: alpha(@play, 0.07);
  border-color: @line_3;
  color: @fg;
}
.capability-chip {
  background-color: @panel;
  border: 1px solid @line;
  border-radius: 9px;
  color: @muted;
  font-size: 9pt;
  padding: 5px 12px;
}

/* ---- sidebar tabs (v4: playlist / beauty pages) ----------------------
   Kept for the legacy two-page sidebar; scheme C would put a bone text +
   short underline here, which the v6 drawer head does (see window.py). */
.sidebar-tabs {
  border: 1px solid @line_2;
  border-radius: 9px;
  background-color: @bg;
  padding: 3px;
}
.sidebar-panel .sidebar-tabs button {
  min-height: 44px;
  padding: 0 10px;
  border: none;
  border-radius: 6px;
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
  background-color: alpha(@play, 0.10);
  background-image: none;
  color: @fg;
}

/* ---- beauty page (v4) ------------------------------------------------ */
.sidebar-page { padding: 0 2px; }
/* Scheme C switch: 44x24, 5 px corners, bone fill when on and an ink knob. */
.sidebar-panel switch {
  min-width: 44px;
  min-height: 24px;
  margin: 4px 0;
  background-color: @switch_off;
  border: 1px solid alpha(@fg, 0.13);
  border-radius: 5px;
}
.sidebar-panel switch:checked {
  background-color: @play;
  border-color: @play;
}
.sidebar-panel switch:checked slider { background-color: @on_play; }
.sidebar-panel switch slider {
  min-width: 16px;
  min-height: 16px;
  background-color: @play;
  border-radius: 50%;
}
label.beauty-title { color: @fg; font-size: 13pt; font-weight: 600; }
label.beauty-status { color: @muted; font-size: 8.5pt; font-family: monospace; }
label.beauty-status-dot { color: @accent; font-size: 8pt; }
.beauty-page.is-off label.beauty-status-dot { color: @line_3; }
.beauty-page.is-off label.beauty-status { color: @dim; }
/* The master switch stays clickable while the page is off, but it must read
   as off: the theme's white knob is the brightest thing in the greyed page. */
.beauty-page.is-off switch {
  background-color: @switch_off;
  border-color: alpha(@fg, 0.13);
}
.beauty-page.is-off switch slider { background-color: @dim; }
label.beauty-cascade {
  color: @fg_2;
  font-size: 8.5pt;
  background-color: alpha(@play, 0.06);
  border-left: 2px solid @play;
  border-top-right-radius: 8px;
  border-bottom-right-radius: 8px;
  padding: 8px 10px;
}
label.beauty-slider-label { color: @fg_2; font-size: 10pt; }
label.beauty-slider-value { color: @dim; font-size: 8.5pt; font-family: monospace; }
label.beauty-section-heading { color: @fg; font-size: 12px; font-weight: 700; }
.beauty-settings {
  background-color: @setting;
  border: 1px solid @line_2;
  border-radius: 9px;
}
/* min-height is the content box in GTK CSS, so the mockup's 46 px row is the
   label pair plus this padding; the switch already forces 32 px of height. */
.beauty-settings .settings-row { padding: 6px 12px; min-height: 0; }
label.settings-title { color: @fg_2; font-size: 10pt; }
label.settings-hint { color: @dim; font-size: 8pt; }
.beauty-split-row { padding: 3px 12px 5px 12px; min-height: 0; }
label.split-title { color: @muted; font-size: 8.5pt; }
label.split-value { color: @dim; font-size: 8.5pt; font-family: monospace; }
.sidebar-panel .beauty-slider scale { min-height: 34px; }
.sidebar-panel .beauty-split-row scale { min-height: 28px; }
.beauty-split-row scale trough { min-height: 3px; }
.beauty-split-row scale slider { min-width: 14px; min-height: 14px; }
.sidebar-panel button.beauty-more-row {
  background-color: @setting;
  border: 1px solid @line_2;
  border-radius: 9px;
  min-height: 34px;
  padding: 0 10px;
  background-image: none;
  box-shadow: none;
  color: @fg_2;
}
.sidebar-panel button.beauty-more-row:hover {
  background-color: @panel_2;
  border-color: @line_3;
  color: @fg;
}
.sidebar-panel button.beauty-more-row:checked { background-color: @panel_2; }
label.beauty-more-title { color: @fg_2; font-size: 10pt; }
label.beauty-more-hint { color: @dim; font-size: 8pt; }
label.beauty-more-chevron { color: @muted; font-size: 10pt; }
label.beauty-note { color: @dim; font-size: 8pt; }
label.experimental-heading { color: @muted; font-size: 9pt; font-weight: 700; }
button.playlist-add {
  background-color: transparent;
  color: @muted;
  border: 1px solid @line_3;
  border-radius: 9px;
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
  background-color: transparent;
  border: none;
  border-radius: 10px;
  padding: 6px 12px 8px 12px;
}
.transport-bar.docked {
  background-color: transparent;
  border: none;
  padding: 0;
}
.seek-row { padding: 0 2px; }
.button-row { padding: 0 2px; }
/* Scheme C transport keys are outlines on the picture, not filled plates. */
button.transport-button,
button.header-action {
  background-color: transparent;
  color: @fg_2;
  border: none;
  background-image: none;
  box-shadow: none;
}
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
  background-color: alpha(@play, 0.12);
  color: @fg;
  background-image: none;
}
button.transport-button,
button.header-action {
  min-width: 44px;
  min-height: 44px;
  padding: 0 12px;
  border-radius: 9px;
}
button.transport-button.image-button { padding: 0; }
menubutton.transport-button > button {
  background-color: transparent;
  color: @fg_2;
  background-image: none;
  border: none;
  box-shadow: none;
  min-width: 44px;
  min-height: 44px;
  padding: 0 14px;
  border-radius: 9px;
}
menubutton.transport-button > button:hover { background-color: alpha(@play, 0.07); }
menubutton.transport-button > button:active { background-color: alpha(@play, 0.07); }
button.transport-button:hover,
button.header-action:hover,
button.secondary-action:hover,
button.accent-button:hover,
dropdown > button:hover {
  background-image: none;
  box-shadow: none;
}
button.transport-button:hover,
button.header-action:hover { background-color: alpha(@play, 0.07); }
button.secondary-action:hover { background-color: alpha(@play, 0.07); }
button:disabled,
dropdown > button:disabled {
  background-color: transparent;
  background-image: none;
  color: @dim;
  border: none;
  box-shadow: none;
}
button.transport-primary {
  background-color: @play;
  color: @on_play;
  border: 1px solid alpha(@on_media, 0.30);
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
  background-color: alpha(@play, 0.07);
  border-color: @line_3;
  color: @fg;
}
.transport-divider {
  min-width: 1px;
  background-color: @line_2;
  margin: 6px 3px;
}
button.secondary-action {
  background-color: transparent;
  color: @fg_2;
  border: 1px solid @line_2;
}
button.accent-button {
  background-color: @play;
  color: @on_play;
  border: 1px solid alpha(@on_media, 0.42);
}
button.accent-button:hover { background-color: @play_hi; }
button.accent-button:active { background-color: @play_active; }
button:focus,
scale:focus,
dropdown:focus {
  outline: none;
  box-shadow: none;
}
button:focus-visible,
scale:focus-visible,
dropdown:focus-visible {
  outline: 2px solid @play;
  outline-offset: 2px;
}
/* Data colour lives in the fill; the trough and the knob are neutral. */
scale trough {
  min-height: 3px;
  border-radius: 999px;
  background-color: alpha(@fg, 0.16);
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
  min-width: 14px;
  min-height: 14px;
  background-color: @play;
  background-image: none;
  border: 2px solid alpha(@accent, 0.45);
  border-radius: 50%;
  box-shadow: none;
}
.transport-bar scale { min-height: 30px; }
.transport-bar scale trough { min-height: 3px; }
.transport-bar scale slider { min-width: 14px; min-height: 14px; }
.sidebar-panel scale { min-height: 38px; }
.sidebar-panel scale slider { min-width: 14px; min-height: 14px; }
.volume-icon { color: @muted; }
.time-display {
  color: @time;
  font-family: monospace;
  font-size: 11.7px;
}
.sidebar-panel label.dim-label { color: @muted; }
dropdown > button {
  min-height: 44px;
  background-color: alpha(@play_bg, 0.52);
  border: 1px solid alpha(@fg, 0.16);
  color: @fg;
}
.decode-segments {
  border: 1px solid alpha(@fg, 0.16);
  border-radius: 9px;
  background-color: transparent;
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
  background-color: alpha(@play, 0.07);
  color: @fg_2;
}
.decode-segments button:checked {
  background-color: alpha(@play, 0.12);
  color: @fg;
  box-shadow: 0 0 0 1px alpha(@play, 0.30) inset;
}

/* ---- labelled info row ----------------------------------------------- */
.info-bar { padding: 2px 2px 0 2px; }
label.info-label {
  color: @muted;
  font-size: 9pt;
}
label.info-value {
  color: @telemetry;
  font-family: monospace;
  font-size: 9pt;
}
label.info-live {
  color: @accent;
  font-family: monospace;
  font-size: 9pt;
}
label.info-dot {
  color: @ok;
  font-size: 10pt;
}
label.info-dot.is-off { color: @line_3; }
label.info-separator {
  color: @line_3;
  font-size: 9pt;
}

/* ---- fullscreen dock / persistent telemetry -------------------------- */
.transport-dock {
  background-color: alpha(@play_bg, 0.82);
  border: 1px solid alpha(@fg, 0.10);
  border-top-color: alpha(@fg, 0.16);
  border-radius: 10px;
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
  border-radius: 9px;
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
  background-color: transparent;
  color: @fg_2;
  border: 1px solid alpha(@fg, 0.16);
  border-radius: 9px;
  min-height: 44px;
  padding: 0 14px;
  background-image: none;
}
button.dock-pill:checked {
  background-color: alpha(@play, 0.12);
  background-image: none;
  border-color: alpha(@play, 0.30);
  color: @fg;
  box-shadow: 0 0 0 1px alpha(@play, 0.24) inset;
}
button.dock-pill:hover { background-color: alpha(@play, 0.07); }
button.dock-pill:checked:hover { background-color: alpha(@play, 0.16); }
/* v4: the compare pill needs beauty; while beauty is off it stays visible but
   reads as unavailable instead of disappearing from the dock. */
button.dock-pill:disabled { opacity: 0.34; }
label.fullscreen-telemetry {
  color: alpha(@fg, 0.92);
  font-family: monospace;
  font-size: 9.5pt;
  text-shadow: 0 1px 4px alpha(@video, 0.90);
}
label.fullscreen-telemetry-live { color: @accent; }

/* ---- transient hint / status / subtitles ----------------------------- */
.hint-toast {
  background-color: alpha(@play_bg, 0.88);
  border: 1px solid alpha(@fg, 0.12);
  border-radius: 9px;
  padding: 6px 14px;
  font-size: 10pt;
  color: @fg_2;
}
label.hint-toast.warn-hint { color: @warn; border-color: alpha(@warn, 0.45); }
label.hint-toast.error-hint { color: @danger; border-color: alpha(@danger, 0.45); }
.header-title {
  font-size: 14px;
  font-weight: 600;
  color: @fg;
}
.header-subtitle {
  font-size: 11px;
  color: @dim;
  margin-top: 1px;
}
.playback-status-card {
  color: @fg;
  background-color: alpha(@bg, 0.94);
  border: 1px solid @line_2;
  border-radius: 10px;
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
image.status-symbol { color: @play; }
image.status-error-symbol { color: @danger; }
spinner.status-spinner { color: @play; }
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

/* ---- ui-v6: picture-first playback page ------------------------------ */
.play-stage {
  background-color: @play_bg;
}
.play-video-surface {
  background-color: @video;
  border: none;
  border-radius: 0;
}
.play-veil-top {
  background-image: linear-gradient(to bottom, alpha(@play_bg, 0.92), alpha(@play_bg, 0));
}
.play-veil-bottom {
  background-image: linear-gradient(to top, alpha(@play_bg, 0.90) 0%, alpha(@play_bg, 0.55) 30%, alpha(@play_bg, 0.12) 64%, alpha(@play_bg, 0));
}
.play-top {
  color: @fg;
}
/* Glass chrome: a 52 % near-black plate and one hairline, scheme C. */
button.play-round,
menubutton.play-round > button {
  min-width: 44px;
  min-height: 44px;
  padding: 0;
  border-radius: 22px;
  background-color: alpha(@play_bg, 0.52);
  border: 1px solid alpha(@fg, 0.14);
  color: @fg;
  background-image: none;
  box-shadow: none;
}
button.play-round:hover,
menubutton.play-round > button:hover {
  background-color: alpha(@drawer, 0.82);
  border-color: alpha(@fg, 0.24);
}
label.play-back-glyph {
  font-size: 17pt;
  color: @fg;
}
label.play-more-glyph {
  font-size: 15pt;
  color: @fg;
}
.play-title {
  font-size: 13pt;
  font-weight: 600;
  color: @fg;
}
.play-subtitle {
  font-size: 8.5pt;
  color: @dim;
}
menubutton.play-top-chip > button {
  min-height: 44px;
  padding: 0 13px;
  border-radius: 9px;
  background-color: alpha(@play_bg, 0.52);
  border: 1px solid alpha(@fg, 0.14);
  color: @fg_2;
  background-image: none;
  box-shadow: none;
}
menubutton.play-top-chip > button:hover {
  background-color: alpha(@drawer, 0.82);
  border-color: alpha(@fg, 0.24);
}
label.play-chip-glyph {
  font-size: 10.5pt;
  color: @muted;
}
label.play-chip-label {
  font-size: 9.5pt;
  color: @fg_2;
}
/* Icon-only panel chip: the amber dot is the windowed beauty state (the
   "美颜 · 开/关" pill only exists in fullscreen/--record). */
label.play-chip-dot {
  font-size: 8pt;
  color: @accent_hi;
}
/* Checked chip = bone fill, so the dot switches to ink to stay readable. */
button.play-top-chip:checked label.play-chip-dot {
  color: @on_play;
}
/* 选集 is a toggle now (docked right column), not a popover button.
   Its checked state is scheme C's one filled chrome control: bone + ink. */
button.play-top-chip {
  min-height: 44px;
  padding: 0 13px;
  border-radius: 9px;
  background-color: alpha(@play_bg, 0.52);
  border: 1px solid alpha(@fg, 0.14);
  color: @fg_2;
  background-image: none;
  box-shadow: none;
}
button.play-top-chip:hover {
  background-color: alpha(@drawer, 0.82);
  border-color: alpha(@fg, 0.24);
}
button.play-top-chip:checked {
  border-color: @play;
  background-color: @play;
  color: @on_play;
}
button.play-top-chip:checked label { color: @on_play; }

/* -- docked episodes column ------------------------------------------- */
.play-split {
  background-color: @play_bg;
}
.episodes-panel {
  background-color: @drawer;
  border-left: 1px solid @line;
  border-radius: 0;
  padding: 14px 16px;
}
.episodes-panel label.popover-title {
  font-size: 13pt;
}
/* The beauty page is docked in the panel as a tab page, not an overlay card:
   the column already owns the background and padding. */
.episodes-panel .beauty-page {
  background-color: transparent;
  border-radius: 0;
  padding: 0;
}
.episodes-panel .beauty-page label.beauty-title {
  font-size: 12pt;
}
/* Scheme C's "add file" is a quiet outline, not a filled sidebar button;
   this needs to out-specificate the generic ``.sidebar-panel button`` rule. */
.episodes-panel button.playlist-add {
  background-color: transparent;
  color: @muted;
  border: 1px solid @line_3;
  border-radius: 9px;
  min-height: 40px;
  padding: 0 10px;
  background-image: none;
  box-shadow: none;
}
.episodes-panel button.playlist-add:hover {
  color: @fg_2;
  border-color: @line_3;
  background-color: @panel_2;
}
.episodes-panel scrolledwindow.playlist-scroll {
  min-height: 160px;
  background-color: transparent;
  border: none;
}
.episodes-panel scrolledwindow.playlist-scroll viewport {
  background-color: transparent;
}

/* -- header "open" menu ----------------------------------------------- */
menubutton.header-open > button {
  min-height: 36px;
  padding: 0 12px;
  border-radius: 9px;
  background-color: transparent;
  border: 1px solid @line_2;
  color: @fg_2;
  background-image: none;
  box-shadow: none;
}
menubutton.header-open > button:hover {
  background-color: @panel_2;
  border-color: @line_3;
  color: @fg;
}
label.header-open-glyph {
  font-size: 11pt;
  color: @fg_2;
}
label.header-open-label {
  font-size: 9.5pt;
  color: @fg_2;
}

/* Two-row transport while the episodes column narrows the picture. */
/* (Removed: the 920 px transport is one row now; the secondary controls live
   in the picture top bar and the panel tab.) */

/* -- telemetry row ----------------------------------------------------- */
.telemetry-row {
  min-height: 20px;
}
label.telemetry-value {
  color: @telemetry;
  font-family: monospace;
  font-size: 11.5px;
}
label.telemetry-live {
  color: @telemetry;
  font-family: monospace;
  font-size: 11.5px;
}
label.telemetry-accent { color: @accent; }
label.telemetry-cpu {
  color: @muted;
  font-family: monospace;
  font-size: 11px;
}
label.telemetry-dot { color: @ok; font-size: 7pt; }
label.telemetry-dot.is-off { color: @line_3; }
.telemetry-sep {
  min-width: 1px;
  min-height: 9px;
  background-color: alpha(@fg, 0.14);
}

/* -- timeline + 44 px transport --------------------------------------- */
.play-dock {
  background-color: transparent;
  padding: 0;
}
.play-seek-row {
  min-height: 20px;
  margin-top: 2px;
}
label.play-time {
  min-width: 44px;
  color: @time;
  font-family: monospace;
  font-size: 11.7px;
}
.chapter-timeline {
  min-height: 20px;
  background-color: transparent;
}
.play-controls {
  margin-top: 2px;
}
button.play-control,
menubutton.play-control > button {
  min-width: 44px;
  min-height: 44px;
  padding: 0;
  border-radius: 9px;
  background-color: transparent;
  color: @fg_2;
  border: none;
  background-image: none;
  box-shadow: none;
}
button.play-control:hover,
menubutton.play-control > button:hover {
  background-color: alpha(@play, 0.07);
  color: @fg;
}
menubutton.play-control > button:checked {
  background-color: alpha(@play, 0.12);
  color: @fg;
}
label.play-control-glyph {
  font-family: monospace;
  font-size: 10pt;
  color: currentColor;
}
button.play-skip { padding: 0 8px; }
button.play-primary {
  min-width: 52px;
  min-height: 52px;
  padding: 0;
  border-radius: 26px;
  background-color: @play;
  color: @on_play;
  border: 1px solid alpha(@on_media, 0.30);
  background-image: none;
  box-shadow: none;
}
button.play-primary:hover { background-color: @play_hi; }
button.play-primary:active { background-color: @play_active; }
button.play-ghost {
  background-color: transparent;
  border: 1px solid alpha(@fg, 0.16);
  color: @fg_2;
}
button.play-ghost:hover {
  background-color: alpha(@play, 0.07);
  border-color: alpha(@fg, 0.26);
  color: @fg;
}
.play-divider {
  min-width: 1px;
  min-height: 22px;
  margin: 0 6px;
  background-color: alpha(@fg, 0.14);
}
.play-speed > button {
  min-height: 44px;
  padding: 0 10px;
  border-radius: 9px;
  background-color: alpha(@play_bg, 0.52);
  border: 1px solid alpha(@fg, 0.16);
  color: @fg;
  background-image: none;
  box-shadow: none;
  font-family: monospace;
  font-size: 12px;
}
.play-speed > button:hover { background-color: alpha(@play, 0.07); }
button.play-text-control {
  padding: 0 10px;
  font-size: 9pt;
  font-family: monospace;
}
button.play-pill {
  min-height: 40px;
  padding: 0 12px;
  border-radius: 9px;
  border: 1px solid alpha(@fg, 0.16);
  background-color: transparent;
  color: @fg_2;
  font-size: 9pt;
  background-image: none;
  box-shadow: none;
}
button.play-pill:hover { background-color: alpha(@play, 0.07); }
button.play-pill:checked {
  border-color: alpha(@play, 0.30);
  background-color: alpha(@play, 0.12);
  color: @fg;
}
button.play-pill:disabled { opacity: 0.34; }

/* -- episodes / more popovers ----------------------------------------- */
popover.play-popover > contents {
  background-color: @panel;
  border: 1px solid alpha(@fg, 0.12);
  border-radius: 10px;
  box-shadow: 0 16px 40px alpha(@video, 0.55);
  padding: 0;
}
label.popover-title {
  font-size: 12pt;
  font-weight: 600;
  color: @fg;
}
label.popover-meta {
  font-family: monospace;
  font-size: 8pt;
  color: @dim;
}
label.popover-heading {
  color: @muted;
  font-size: 8.5pt;
  margin-top: 4px;
}
button.menu-row {
  min-height: 44px;
  border-radius: 9px;
  padding: 0 10px;
  background-color: transparent;
  border: none;
  background-image: none;
  box-shadow: none;
}
button.menu-row:hover { background-color: @panel_3; }
label.menu-row-glyph {
  color: @muted;
  font-size: 10pt;
  min-width: 16px;
}
label.menu-row-label {
  color: @fg_2;
  font-size: 9.5pt;
}
label.menu-shortcuts {
  color: @dim;
  font-size: 8pt;
  padding: 8px 10px 2px 10px;
}

/* -- beauty drawer ----------------------------------------------------- */
.sidebar-panel.beauty-drawer {
  background-color: @drawer;
  border: 1px solid @line;
  border-radius: 10px;
  padding: 16px;
}
scrolledwindow.drawer-scroll {
  background-color: transparent;
  border: none;
}
scrolledwindow.drawer-scroll viewport {
  background-color: transparent;
}
button.drawer-close {
  min-width: 34px;
  min-height: 34px;
  padding: 0;
  border-radius: 9px;
  background-color: transparent;
  border: none;
  color: @muted;
  background-image: none;
  box-shadow: none;
}
button.drawer-close:hover { background-color: alpha(@play, 0.07); color: @fg; }
label.beauty-drawer-face {
  color: @dim;
  font-family: monospace;
  font-size: 7.5pt;
}

/* ---- ui-v6 browse shell: left rail ----------------------------------- */
.browse-shell { background-color: @bg; }
.rail {
  background-color: @bg_deep;
  border-right: 1px solid @line;
  padding: 14px 0 12px 0;
}
/* Scheme C current entry: no filled plate, a 2 px bone bar (drawn by rail.py). */
button.rail-item {
  background-color: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
  border-radius: 8px;
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
  background-color: transparent;
  color: @fg;
}
button.rail-item:disabled { opacity: 0.40; }
label.rail-label { font-size: 11px; }
label.rail-status { font-size: 11px; color: @dim; }
label.rail-clock {
  font-family: monospace;
  font-size: 11px;
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
  font-size: 27px;
  font-weight: 700;
  color: @fg;
}
label.page-stat {
  font-family: monospace;
  font-size: 14px;
  color: @muted;
}
label.page-sub { font-size: 13px; color: @dim; }
button.tool {
  background-color: transparent;
  background-image: none;
  border: 1px solid @line_2;
  border-radius: 9px;
  box-shadow: none;
  color: @fg_2;
  font-size: 12.7px;
  min-height: 38px;
  padding: 0 13px;
}
button.tool:hover {
  background-color: @panel_2;
  border-color: @line_3;
  color: @fg;
}
/* Gtk.MenuButton is a ``menubutton`` node whose inner button does not carry
   the ``tool`` class; without this rule the sort control falls back to the
   theme's light button (the same trap as the transport CC menu button). */
menubutton.tool > button {
  background-color: transparent;
  background-image: none;
  border: 1px solid @line_2;
  border-radius: 9px;
  box-shadow: none;
  color: @fg_2;
  font-size: 12.7px;
  min-height: 38px;
  padding: 0 13px;
}
menubutton.tool > button:hover {
  background-color: @panel_2;
  border-color: @line_3;
  color: @fg;
}
menubutton.tool > button:active {
  background-color: @panel_2;
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
label.tool-key { color: @dim; font-size: 12px; }
label.tool-value { color: @fg; font-size: 12.7px; font-weight: 600; }
button.fchip {
  background-color: transparent;
  background-image: none;
  border: 1px solid @line_2;
  border-radius: 9px;
  box-shadow: none;
  color: @muted;
  font-size: 12.7px;
  min-height: 38px;
  padding: 0 13px;
}
button.fchip:hover { border-color: @line_3; color: @fg_2; }
button.fchip:checked {
  background-color: transparent;
  border-color: alpha(@fg, 0.62);
  color: @fg;
}
/* Scheme C: filter counts are data and stay amber in every state. */
label.fchip-count {
  color: @accent;
  font-family: monospace;
  font-size: 11.3px;
}
button.fchip:checked label.fchip-count { color: @accent; }
scrolledwindow.library-scroll {
  background-color: @bg;
  border: none;
}
.library-grid { background-color: @bg; }
button.lib-card {
  background-color: transparent;
  background-image: none;
  border: none;
  border-radius: 8px;
  box-shadow: none;
  min-width: 220px;
  padding: 0 0 2px 0;
}
button.lib-card:hover { background-color: alpha(@fg, 0.03); }
button.lib-card:focus-visible { outline: 2px solid @play; }
label.lib-title {
  color: @fg;
  font-size: 13px;
  font-weight: 400;
  letter-spacing: 0.1px;
}
label.lib-meta {
  color: @meta;
  font-family: monospace;
  font-size: 11px;
  letter-spacing: 0.15px;
}
label.lib-foot { color: @dim; font-size: 11.3px; }
label.lib-foot-path {
  color: @dim;
  font-family: monospace;
  font-size: 10.7px;
}
label.lib-empty-note { color: @dim; font-size: 9pt; }
.library-page.drag-active { background-color: alpha(@play, 0.04); }

/* ---- recent page ----------------------------------------------------- */
.recent-page {
  background-color: @bg;
  padding: 12px 26px 10px 26px;
}
scrolledwindow.recent-scroll {
  background-color: @bg;
  border: none;
}
box.recent-groups { background-color: @bg; }
.recent-group {
  margin-bottom: 18px;
}
.recent-group-head {
  margin-bottom: 10px;
}
label.recent-group-title {
  font-size: 13pt;
  color: @fg_2;
}
label.recent-group-count {
  font-family: monospace;
  font-size: 8.5pt;
  color: @dim;
}
box.recent-rule {
  background-color: @line_2;
  min-height: 1px;
}
.recent-empty {
  padding: 24px;
}
button.recent-clear:hover {
  border-color: alpha(@danger, 0.45);
  color: @danger;
}

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
.browse-shell.drag-active { background-color: alpha(@play, 0.04); }
.hero-device-pill {
  background-color: alpha(@play_bg, 0.46);
  border: 1px solid alpha(@fg, 0.12);
  border-radius: 6px;
  padding: 3px 9px;
}
label.hero-device {
  font-family: monospace;
  font-size: 10.7px;
  color: alpha(@fg, 0.72);
}
label.hero-device-dot { font-size: 6pt; color: @muted; }
label.hero-kicker { font-size: 11.5px; color: @muted; }
label.hero-title {
  font-size: 38px;
  font-weight: 600;
  letter-spacing: -0.4px;
  color: @fg;
}
label.hero-meta { font-size: 12.7px; color: @fg_2; }
label.hero-path {
  font-family: monospace;
  font-size: 10.7px;
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
  font-size: 17px;
  font-weight: 700;
  letter-spacing: -0.1px;
  color: @fg;
}
label.row-count { font-size: 11.5px; color: @dim; }
label.row-note { font-size: 11.5px; color: @dim; }
button.row-more {
  background-color: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
  color: @muted;
  font-size: 12px;
  min-height: 40px;
  padding: 0 8px;
}
button.row-more:hover { color: @fg; background-color: alpha(@play, 0.05); border-radius: 8px; }

/* ---- primary / ghost calls to action (home hero, detail backdrop) ------ */
/* Scheme C: the primary CTA is the bone action colour with ink text. */
button.btn-primary {
  background-color: @play;
  background-image: none;
  border: 1px solid alpha(@on_media, 0.42);
  border-radius: 9px;
  box-shadow: none;
  color: @on_play;
  font-size: 13.3px;
  font-weight: 600;
  min-height: 46px;
  padding: 0 20px;
}
button.btn-primary:hover { background-color: @play_hi; }
button.btn-primary:active { background-color: @play_active; }
button.btn-primary label { color: @on_play; }
button.btn-ghost {
  background-color: transparent;
  background-image: none;
  border: 1px solid alpha(@fg, 0.20);
  border-radius: 9px;
  box-shadow: none;
  color: @fg;
  font-size: 13.3px;
  min-height: 46px;
  padding: 0 20px;
}
button.btn-ghost:hover { background-color: alpha(@play, 0.07); }
button.btn-ghost label { color: @fg; }

/* ---- detail page ------------------------------------------------------ */
.detail-page { background-color: @bg; }
button.detail-back {
  background-color: alpha(@play_bg, 0.55);
  background-image: none;
  border: 1px solid alpha(@fg, 0.16);
  border-radius: 22px;
  box-shadow: none;
  min-height: 44px;
  min-width: 44px;
  padding: 0;
}
button.detail-back:hover { background-color: alpha(@play_bg, 0.80); }
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
  background-color: alpha(@fg, 0.055);
  border: 1px solid alpha(@fg, 0.10);
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
  border-radius: 8px;
  box-shadow: none;
  padding: 0;
}
button.ep-card:hover { background-color: alpha(@play, 0.05); }
button.ep-card:focus-visible { outline: 2px solid @play; }
label.ep-title { font-size: 9.5pt; color: @fg; }
button.ep-card.now label.ep-title { color: @play; }
label.ep-duration {
  font-family: monospace;
  font-size: 8pt;
  color: @dim;
}
/* ---- ui-v6 settings page -------------------------------------------- */
.settings-page { background-color: @bg; }
label.settings-card-note { color: @dim; font-size: 9pt; }
label.settings-hint { color: @dim; font-size: 9pt; }
.settings-seg {
  background-color: @panel_2;
  border: 1px solid @line;
  border-radius: 9px;
}
button.settings-seg-button {
  background-color: transparent;
  background-image: none;
  border: 1px solid transparent;
  border-radius: 0;
  box-shadow: none;
  color: @muted;
  font-size: 9.5pt;
  min-height: 34px;
  padding: 0 16px;
}
button.settings-seg-button:hover { color: @fg; }
button.settings-seg-button:checked {
  background-color: alpha(@play, 0.12);
  border-color: alpha(@play, 0.30);
  color: @fg;
  font-weight: 600;
}
button.settings-seg-button:first-child { border-radius: 8px 0 0 8px; }
button.settings-seg-button:last-child { border-radius: 0 8px 8px 0; }
.settings-dir-row { min-height: 26px; }
label.settings-dir-path {
  color: @fg_2;
  font-family: monospace;
  font-size: 8.5pt;
}
label.settings-badge {
  background-color: @panel_2;
  border: 1px solid @line;
  border-radius: 6px;
  color: @muted;
  font-size: 8pt;
  padding: 2px 7px;
}
label.settings-badge.is-missing {
  border-color: alpha(@warn, 0.40);
  color: @warn;
}

.card-box {
  background-color: @panel;
  border: 1px solid @line;
  border-radius: 10px;
  padding: 12px 16px;
}
label.card-box-title {
  font-size: 9.5pt;
  color: @muted;
}
.spec-row {
  min-height: 22px;
  border-top: 1px solid alpha(@fg, 0.05);
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

