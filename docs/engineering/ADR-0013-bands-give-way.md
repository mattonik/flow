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

The Island, the home indicator, the work indicator and the App button each sit in a `GiveWay` item. It does three
things:

- **Fades after a rest.** The pointer rests on a band, 600 ms without moving or pressing, while an app lies beneath
  it: the plain work view (`workViewOnly`), a window on the focused workspace (`root.windows`), and no panel,
  search or app menu open. Then the band fades to nothing over 120 ms (at once with Reduce motion) and is disabled.
  Its surface's input region (Quickshell's `mask`) shrinks to the screen's outermost 2 rows along the edge the band
  hangs from, or to nothing for the work indicator, which sits off the edge. Niri hands the pointer to the app at
  once: in the guest the band lost the pointer 3 to 27 ms after the rest ended. The next click is the app's.
- **Comes back.** Every 1.5 s a faded band takes its whole input region back, still unseen and disabled. If the
  pointer is still over it, Niri gives it the pointer at once and it empties again. If not, after 250 ms the band
  fades back in. It also comes back at once when the pointer reaches its edge rows (pushing against the top or
  bottom edge), and whenever the work view gives way to Desk, Overview, the switcher, a panel or search.
- **Stays usable.** A rest only starts once the pointer stops moving. A quick click, a tap and a swipe reach the band
  as before, and so does a press held still: a `MouseArea` above the band notes the press and passes it on, and a
  noted press keeps the band. A pointer that came in through the edge rows keeps the band while it stays. A finger
  never hovers, so touch reaches the band unless the mouse has just faded it. The home indicator's swipe up works
  as before when it starts at once or from the screen's bottom edge; one that starts after a rest on the faded band
  reaches the app.

The toast has nothing to click. Its input region is always empty (`mask: Region {}`).

Only real motion restarts the rest. Qt hands a still pointer's position over again when something under it
animates (the home indicator's line widening, a button's hover tint). Counting that as motion restarted the rest
100 to 300 ms late, and a click 800 ms after arrival raced the fade. `give_way` (the shell's `desk` IPC target,
read-only) reports each band's state and how long it took to let go, for the smoke.

## Options considered

| Option | Why not |
|---|---|
| Exclusive zones: bands reserved for the Island and indicators | The user rejected it. It takes a strip of every screen from every app, and the spec has the Island float. |
| Bands faded whenever an app is beneath them, back only on edge hover | The Island, the work view's way to Desk, search and the system controls, would be gone for good in the usual work view, and the space indicator with it. |
| Fade the moment the pointer arrives from the app's side | The bands could not be clicked from where they are seen. A rest separates "this is what I want" from "something is under this". |
| Track the pointer to bring a band back when it leaves | A surface with an empty input region gets no pointer events, and Niri's IPC does not report the cursor. A ring of input around a band would eat clicks and miss a fast move. The probe asks Niri instead, through the surface's own input region. |
| A pointer handler on the band to notice presses | It does not see a press that a button inside the band takes (seen in the guest). The `MouseArea` above the band rejects each press, so it goes on. |

## Consequences

- A user who stops on a band to read it sees it fade after 0.6 s. A quick click needs no rest, and the edge row or
  a short move away brings it back.
- A click on a faded band's spot within 250 ms of a probe, while the pointer sits still there, reaches nothing:
  the band takes it, disabled and unseen. Its controls are disabled while faded, so the click does nothing. It
  cannot hit *Close*.
- While a band is faded the app gets a pointer leave and enter every 1.5 s, from the probe. A tooltip that waits
  for the pointer to rest may start over.
- An app's own outermost 2 rows under the Island and the home indicator still go to the shell (the edge rows).
  None of the guest's apps put a control there.
- The test's rest is a user's: `edges` and `chooser-pointer` move, rest 800 ms, then click. `edges` also checks that
  a quick click on the home indicator opens Overview and does not reach the page.

## Validation

Guest (UTM, Niri 26.04, Quickshell 0.3.2), on installed code: `tests/niri_portals_smoke.py`'s 11 checks with no
expected failures, and the shell's pointer smokes for these bands (desk and overview, prototype surfaces,
notification banner, search, app menu, Wi-Fi). Neutralised (symbols kept), `edges` and `chooser-pointer` fail.
`tests/test_give_way.py` checks the wiring in CI, which has no QML runner. Results: *F-012 acceptance* in
[IMPLEMENTATION_STATUS.md](../IMPLEMENTATION_STATUS.md).
