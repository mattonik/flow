# ADR-0013: The work view's bands get out of an app's way

**Status:** Accepted, 27 November 2026

## Context

The work view keeps a few shell surfaces over the apps (`session/shell.qml`). All are `WlrLayer.Overlay` layer
surfaces with `ExclusionMode.Ignore`: they reserve nothing and float over whatever is beneath. Flow's columns reach
the screen's edges, so under each one lies an app's own top or bottom edge.

| Surface | Where | When | Covers |
|---|---|---|---|
| Island (`workIsland`) | Top middle, 44 px tall, as wide as its controls | Work view (hidden once Desk or Overview has drawn) | Tab strips, header bars, a text field in a header (GTK's chooser's Name) |
| App button (`appMenuTrigger`) | Top, 220 × 44, right of the Island (below it on a narrow screen) | Apps with a published menu bar | The app's top row right of the middle |
| Home indicator (`homeIndicator`) | Bottom middle, 126 × 24 | Work view, no switcher, search or app menu | Bottom-middle buttons and status bars |
| Work indicator (`workIndicatorWindow`) | Bottom right, 52 px tall, 8 px up (68 px on a narrow screen) | A space or Layer is active, in the work view and in Niri's overview | Dialog buttons: Nautilus's Open and Save, OK and Cancel. Over a floating dialog it shows the Layer's *Close*, right where the dialog's primary button is |
| Toast | Bottom middle, 68 px up, 44 px | About 2 s after a shortcut | Whatever is there |

The others are not the problem. The notification banner hangs below the Island for 5 s and is meant to be clicked.
Desk, Overview, search, the switcher, the system panels and the app menu cover the screen on purpose while they
are open.

F-012's smoke (`tests/niri_portals_smoke.py`) found the cost. A click on Nautilus's Open or Save reached the work
indicator, and over Firefox's upload dialog it reached *Close*, which closes the dialog. A full-window page's
top-middle, bottom-middle and bottom-right buttons never took a click (checks `chooser-pointer` and `edges`). A band
reserved for the indicators, an exclusive zone, was implemented on the F-012 branch and removed. The spec has the
Island float over content, not take a strip of the screen, and the user chose instead indicators that fade and let
clicks through when they would cover an app.

## Decision

The Island, the home indicator, the work indicator and the App button each sit in a `GiveWay` item.

- **Pass through from the moment the pointer comes.** The pointer moves onto a band from the app's side while an
  app is beneath it. "An app beneath" means the plain work view (`workViewOnly`, which excludes Desk, Overview,
  the switcher, search, the panels and the app menu) with a window on the focused workspace. The bands do not
  compare window rectangles: Flow's columns reach the screen's edges. Then, on that first motion, the band fades
  over 120 ms (at once with Reduce motion) and is disabled. Its surface's input region (Quickshell's `mask`)
  shrinks to its edge rows. Niri moves the pointer to the app as soon as the region is applied: 6 to 60 ms after
  the motion in the guest. A click after that is the app's, however quick.
- **The shell's when reached on purpose.** There are two ways:
  - Through its edge rows: the outermost 2 rows of the screen edge the band hangs from (top for the Island and
    the App button, bottom for the home indicator). The work indicator's surface reaches the screen's
    bottom-right corner, so its bottom rows and right columns count, while it takes input only on its capsule
    and those edges.
  - Super+B (`desk bands`): every band the pointer rests on comes back.

  Either way, the band stays the shell's until the pointer leaves it.
- **Back once the pointer has left.** A surface with an empty input region gets no pointer events, and Niri's IPC
  does not report the cursor. So every 1.5 s a faded band takes its whole input region back, still unseen and
  disabled. If the pointer is still over it, Niri gives it the pointer at once and it fades out again. If not,
  after 250 ms it fades back in. A band is back 1.5 to 1.75 s after the pointer leaves it (2.0 s measured in
  `edges`). The delay is the probe's. A shorter one would make the app under a faded band get a pointer leave and
  enter more than once a second, hiding its tooltips.
- **Where no app is beneath** (an empty workspace, Desk, Overview), the bands take the pointer as before.
- **Urgent states stay seen.** With an activity under way or a critical notification waiting (`islandUrgent`), a
  faded Island keeps 40% opacity, still letting clicks through. The banner, its own surface, is unchanged.
- The toast has nothing to click. Its input region is always empty (`mask: Region {}`).

`give_way` (the shell's `desk` IPC target, read-only) reports each band's state and how long it took to let go.

## Options considered

| Option | Why not |
|---|---|
| Exclusive zones: bands reserved for the Island and indicators | The user rejected it. It takes a strip of every screen from every app, and the spec has the Island float. |
| Fade after the pointer rests 0.6 s (this branch's first version) | It kept F-012's bug for the way people click. A click within 0.6 s of stopping on Nautilus's hidden Open still hit the Layer's *Close*, and the tests passed only because they rested 0.8 s. A user who paused to read a band lost it instead. |
| Bands always faded over an app | The Island, the work view's way to Desk, search and the system controls, would be gone in the usual work view, and the space indicator with it. |
| Track the pointer to bring a band back when it leaves | A ring of input around a band would eat clicks and miss a fast move. The probe asks Niri instead, through the surface's own input region. |

## Consequences

- With a mouse, a band over an app is reached through its screen edge or Super+B, not by moving straight onto it.
  The Island and the home indicator touch their edges, and a pointer pushed against an edge stops there. A Fitts
  throw to the top (the usual way to hit a browser's tabs) under the Island lands in its edge rows, so the Island
  is the shell's until the pointer leaves it.
- **Touch.** A finger does not hover, so a tap reaches a band that is shown. A tap on a band over an app's control
  is the band's, not the app's. The coordinating session asked for taps to reach the app. On Wayland one input
  region serves the pointer and touch, so a body that takes no touch takes no pointer either, and the fade could
  never start. Spec §7.2's tentative "tap → compact actions" on the Island also needs taps to reach it. A finger
  on a band the mouse has just faded reaches the app (its body has no input then). Untested: the guest has no
  touchscreen, and a 2 px edge row is narrow for a finger's edge swipe.
- **A pointer that jumps onto a band and clicks at once.** A tablet pen's tap in place, or a warp, may click before
  Niri has moved the pointer off the band (up to 60 ms measured). The click then lands on the disabled band and
  does nothing. A mouse reaches the band through its border first, so the band has let go before the pointer
  stops. The smoke glides in over about 190 ms, slowing as a hand does, and clicks as it stops.
- While a band is faded the app gets a pointer leave and enter every 1.5 s from the probe. A tooltip waiting for
  the pointer to rest may start over. During an app's implicit grab (a button held), the band gets no enter, and
  after 250 ms it shows under the still pointer. The next motion fades it again.
- An app's own outermost 2 rows under the Island and the home indicator, and the work indicator's corner, still
  go to the shell (the edge rows).
- A drag and drop (not a pointer motion) over a band may be dropped on the band. Untested.

## Validation

Guest (UTM, Niri 26.04, Quickshell 0.3.2), on installed code. The results, with the build, the run and each
neutralisation, are in *F-012 acceptance* in [IMPLEMENTATION_STATUS.md](../IMPLEMENTATION_STATUS.md).
`tests/test_give_way.py` checks the source text in CI, which has no QML runner. It catches a band left unwired or
a rule rewritten, not a change in behaviour that keeps the text. The behaviour rests on the guest smokes.
