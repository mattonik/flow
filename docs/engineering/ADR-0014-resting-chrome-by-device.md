# ADR-0014: Resting chrome by device

**Status:** Accepted, 27 November 2026 (the user chose it from the prototypes in
`docs/screenshots/2026-11-27-adaptive-island-prototype`). The notched-laptop band is simulated only: unverified on a
MacBook.

## Context

F-012 found that the Island, the home indicator and the work indicator sat over every window with no exclusive zone
and took apps' clicks. They covered Nautilus's Open and Save at a chooser's bottom right, a floating dialog's primary
button under the work indicator's *Close*, and the middle of every tab strip under the Island. Three answers were
tried before this one:

- **A reserved band** for the indicators: dropped at first, because the spec then had the Island float over content.
- **Bands that fade and let clicks through** on the pointer's arrival (branch `claude/f012-overlap-c5de56`, paused).
  A click within 2 to 208 ms of the pointer arriving was still lost while Niri moved focus off the shrunk input region,
  and the `edges` check failed 2 of 3 reruns under load. Taps also conflict with fading, since one Wayland input region
  serves pointer and touch, and the bands vanished under the pointer.
- **A thin margin on every screen** (10 px top, 6 px bottom), with a pill at rest (branch
  `claude/thin-margin-prototype`). Every app lost 16 px on every screen, and the pill was a fake notch on a plain one.
  The user was not convinced.

Spec §7.3 asks a notched MacBook to absorb its cutout, and §7.4 asks for no fake camera notch on an unnotched
monitor. Today's hanging tab and the thin-margin pill both are fake notches.

## Decision

Each kind of screen decides how the Island appears. The rule: nothing of the shell rests over an app. What is over
an app is there because the user summoned it, or only for a moment, and then takes no clicks.

**A plain screen** (keyboard and pointer, no cutout; the default, and what the guest runs):
- **At rest, nothing.** No Island, pill, page dots or home line. The only surface left is a 1 px **edge strip**
  along the top edge, drawn as nothing (`flow-edge`). Row 0 is the shell's; row 1 and below are the app's.
- **The Island opens** on the screen whose edge was used, as a floating card 6 px below the edge, round on every
  corner so that it fakes no notch:
  - the pointer pushed against the top edge and resting there for 150 ms (the rest keeps the Island from opening
    over a tab strip when the pointer only passes the edge);
  - a press on the strip, at once;
  - **Super+B**, from Flow's own Niri configuration (`session/niri.kdl`). Opened this way it takes the keyboard, and
    Escape closes it;
  - a click on the clock pill, when that is on.

  It closes 500 ms after the pointer has left it, the strip, the pill and the App button, once whatever it opened (a
  panel, search, the app menu) has closed. Desk or Overview opening closes it. It holds today's controls, the space's
  position with its arrows, and the App button beside it when the focused app has a menu. Panels hang below it.
- **The clock pill** is a setting, off by default: *Off*, *Main screen* or *All screens*. It is small (about
  50 × 22 px), 4 px below the top edge, and takes clicks only on itself. *Main screen* is Super+number's main display:
  Main, or the lowest-numbered connected display while Main is away. Screens come and go without a restart.
- **A notification's banner** shows near the top for 5 s (a critical one until the notifications are opened) and
  takes no clicks: a click there is the app's. To act on a notification, open the Island and its bell, or press
  Super+N. Resting on a banner no longer holds it.

**A notched MacBook in Asahi's full-panel mode** (`appledrm.show_notch=1`, formerly `apple_dcp.show_notch`):
- **The band beside the camera is the shell's.** A layer surface with an exclusive zone keeps tiled windows below
  it, so apps get exactly the rectangle Asahi gives them by default and lose nothing.
- **The Island lives in the band:** Desk and Apps at the left (after the App button when it shows, §8.1), the
  space's position and an activity left of the camera, and the clock, notifications and quick settings right of it.
  It never expands below the camera, so nothing repeats what the band shows. A notification's banner hangs below
  the band.
- **Hit targets** are the band's height (32 logical px on a 13-inch Air at scale 2), not the brand's 44 px: the band is
  only that tall, and on a laptop without a touchscreen it is for a pointer. Text and glyphs never go below 12 and
  16 px, so a narrow logical width keeps them readable.
- **Which screens:** a small table of known panels (13- and 15-inch Air, 14- and 16-inch Pro), matched on the
  full-panel size an output reports, in physical pixels. The band is the panel's height less Asahi's default 16:10
  height, which m1n1 crops to (64 rows on the Airs, 74 on the Pros). No Asahi source gives the cutout's width or
  corner, so those are approximate, from third-party measurements; the 15-inch's and 16-inch's are taken as the 13-
  and 14-inch's. `notchPanel` in `flow-settings.ini` overrides the match for testing: `none` makes every screen
  plain, and a panel's id simulates that panel on every screen, scaled to its width, with the cutout's outline drawn.
- **Fullscreen covers the band.** Niri sizes a fullscreen window to the whole output, whatever the exclusive zones,
  and draws it over the band's layer (the guest: a fullscreen Firefox at 1280 × 832 on a 1280 × 832 output with a
  32 px band). The band is left that way rather than drawn over the window. The rows beside the camera are real
  pixels: only the cutout itself hides content. A band drawn over a fullscreen window would hide both strips beside
  it and leave the app's own controls there out of reach, while the window still believes those rows are its own.

**On every device:**
- **The work indicator** shows only while moving between spaces (for 1.2 s after the active space on a display
  changes), and takes no clicks. It no longer shows in Niri's overview either: there it would appear a moment after
  the overview, a frame of its own. The opened Island has the space's position and its arrows.
- **The home line** shows only when a direct touch device is plugged in (a touchscreen or a pen display:
  `INPUT_PROP_DIRECT` with multitouch positions, read from `/sys/class/input` at start, every 30 s and when the screens
  change). Without one, Super+O, Super+H and the Island reach Overview and Desk.
- **A tablet** (a touchscreen and no keyboard) is meant to get a thin status margin and the home line. It is not
  built yet: today a tablet is treated as a plain screen with a touch device.

## Consequences

- F-012's overlap is gone on plain screens: in the guest, every edge button of a full-window page takes its click with
  no rest, and Nautilus's Open and Save take theirs.
- A click on row 0 of a plain screen goes to the strip and opens the Island, not to the app. A browser tab clicked with
  the pointer thrown against the top edge opens the Island instead. Row 1 is the app's.
- Opening the Island from the edge takes the 150 ms rest plus about 80 ms to draw it (measured in the guest, push to
  first frame). Super+B takes the binding's spawn and the IPC call plus the first frame.
- Nothing shows the time, an activity, a waiting notification or the space's position at rest on a plain screen
  unless the clock pill is on. Those are in the opened Island.
- The banner's default action is no longer one click away. It is in the notifications panel.
- Overview snapshots no longer crop a band above and below on a plain screen. On a notched screen the top crop is the
  band.
- The band on a MacBook, the panel table and the cutout geometry are unverified on real hardware. The simulator proves
  the layout, not native cutout handling (§7.3).
- The fade branch and the thin-margin prototype are dropped from the plan; their branches stay.

## Open

Three choices are waiting on the user and are not built:
- How a pointer reaches Overview at rest without the home line: Niri's hot corner is off, and Flow has none. Either
  Overview and Desk buttons in the opened Island, or a Flow hot corner.
- Whether a critical notification's banner stays on screen, taking no clicks, until the notifications are opened (as
  built), or is brief like the rest.
- Whether a Group's members and a Layer's actions, which the work indicator showed at rest, move into the opened
  Island. Until then, a pointer reaches them only in Niri's overview; Super+Up and Super+Down still move between a
  Group's members.
