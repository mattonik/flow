# Floe native surfaces — visual and responsive UX audit

**Date:** 2026-11-13  
**Source reviewed:** \`session/shell.qml\`, \`session/niri.kdl\`, \`docs/IMPLEMENTATION_STATUS.md\`, \`docs/graphics-validation.md\`, \`docs/milestones.md\`, and \`docs/Flow_MASTER_SPEC_v1.1.md\` on the current Floe/Flow repository.  
**Status:** Source and existing recorded guest evidence reviewed. **No new native screenshots or touch-hardware recordings** were available to the reviewer. The companion SVG is a measured *source-geometry diagram*, not a native screenshot.

## Executive judgment

The desktop shell has a coherent text/search-first Desk and an experimental but real native Niri session. It does **not yet** realize the defining work-to-overview-to-desk spatial choreography. On tablet-sized displays, the top chrome is the clearest visual failure: a fixed 316px Island and a separate fixed 220px app-menu affordance occupy overlapping space.

This is a UI/interaction audit, not a restatement of contrast-token checks. The source permits *definite layout-risk findings* but not claims about actual raster rendering, stacking order, font shaping or pointer hit testing. Prior VM checks establish basic native operation, including one simulated 320×512 case for the Group switcher, **not** visual acceptance of all surfaces or colors.

### Highest-priority findings

| ID | Severity | Native evidence | Impact | Design / engineering action |
|---|---|---|---|---|
| UI-01 | **P0** | \`shell.qml\`: always-on Island is \`316px\` wide, anchored top-center; app-menu trigger is \`220px\` wide, anchored top-left with 8px margin. | At 320px width, Island spans x=2…318 and menu x=8…228; **220px overlap**. At 430px width the overlap is 171px. | Introduce a single top-chrome layout owner. On compact tablet, app name/commands must be in a distinct accessible location or the compact chrome; no simultaneous independent overlays in the same row. |
| UI-02 | **P0** | Always-visible Island \`PanelWindow\` and separate Desk pill (\`150×36\`) are both top-center and the Desk does not hide Island. | Their rectangles intersect; visible composition / input stacking is nondeterministic without native test. | One global Island persisted across perspectives, or hide/replace it for Desk. Never render two competing center capsules. |
| UI-03 | **P1** | Island notification / quick-settings controls use \`FlowButton { width:38; implicitHeight:34 }\`. "floe." and "Apps" are \`Text\` with \`MouseArea\` children, not normal focusable buttons. | Some interactive targets fall below design minimum 44×44 and keyboard/assistive-access focus can be missing. | Make every action a named \`Button\` with >=44×44 hit target on touch; keep visual icon smaller; provide tooltips/labels and visible focus ring. |
| UI-04 | **P1** | \`session/niri.kdl\` fixes overview backdrop \`#0d1313\`, focus-ring \`#b8ddcb\`, background \`#0b1015\`. \`floe-appearance.ini\` changes only Quickshell. | Arctic Daylight Desk → Niri Overview is an unthemed dark transition, visually incoherent. | Document desired adaptation. Explore supported Niri reload/configuration path **without** breaking compositor startup; alternatively choose deliberate dark compositor background with a transition justification, not an accidental mismatch. |
| UI-05 | **P1** | \`Super+Tab\` launches Floe’s *text-title* switcher, \`Super+O\` opens Niri live Overview. The primary \`Desk → Overview\` button invokes Niri, while the bottom handle invokes the switcher. | Two surfaces both called “overview”, but with different capabilities, visual language and focus behavior. | Commit to distinct labels/actions until the spatial overview is integrated: “Spaces switcher” vs “Live overview”. One explicit navigation grammar; don’t present the title-card list as live previews. |
| UI-06 | **P1** | App-menu trigger occupies top-left 220×44 and is opt-in AT-SPI. Popup uses full-screen transparent layer and content at x=8, width=min(390,w−16). | Popup itself shrinks, but its trigger placement conflicts with Island and touch safe area. | Define tablet-specific trigger placement and a true anchored / bottom-sheet menu variant. Preserve focused-app menu identity until dismissed and native menu fallback. |
| UI-07 | **P1** | Quick Settings width is fixed \`320px\`; notification width adapts to \`min(420,screen.width−24)\`. | Quick Settings leaves **zero horizontal clearance** at logical 320px; too close to edge for tablet, rounded corners, safe areas and larger fonts. | \`min(360, screen.width − 2 × safeHorizontalInset)\`; bottom-sheet treatment for portrait. Avoid touching screen corner and keyboard occlusion. |
| UI-08 | **P2** | Desk search button displays long \`Search apps and open spaces · Super K\` label; pinned buttons truncate labels at 15 characters. | On narrow tablet, hints dominate a control and labels may be uninformative for translated strings / Group identification. | Compact visible label “Search”; put shortcut separately on desktop, and put full app/Group name in accessible name and secondary text where possible. |
| UI-09 | **P2** | Desk saves/restores session controls and theme toggles in the same core ScrollView as launcher and pins; \`Switch\` rows have fixed horizontal placement; many groups are text-only. | At 320×512 the Desk can become a long management sheet, undermining calm hierarchy. | Move maintenance actions into a dedicated on-demand Settings surface; Desk still provides a link. Check text scale, content scroll-to-focus and keyboard overlap before removal. |
| UI-10 | **P2** | System Island mixes “floe.”, “Apps”, clock, notifications and Quick Settings in a 316px strip with 18px internal gaps; primary concept was three quiet system controls. | The central pill behaves like a miniature dock/taskbar rather than a system-only Island; product identity outweighs critical status. | Move Apps to Desk/search, keep Island for notifications/activity/quick control; preserve one fast Home access gesture/button. |

### Source geometry — top chrome

Assuming the documented top-center and top-left anchors on a single logical output:

| Logical display width | Island X range (316px) | App trigger X range (220px, left=8) | Shared X width |
|---:|---|---|---:|
| 1280px | 482…798 | 8…228 | 0px |
| 1024px | 354…670 | 8…228 | 0px |
| 768px | 226…542 | 8…228 | 2px |
| **430px** | 57…373 | 8…228 | **171px** |
| **320px** | 2…318 | 8…228 | **220px** |

**Caveat:** This predicts geometric intersections, not visible occlusion priority; Qt layer-shell focus/stacks must be inspected on-device. When Desk is open the top-center Desk pill spans \`(w−150)/2…(w+150)/2\`, entirely inside the Island's 316px horizontal span on every sufficiently wide display.

## Surface-by-surface design review

### 1. Desk (search-first home)

**What already works conceptually:** calm full-screen canvas, large editorial headline, search-first placement, pinned spaces before tools, persistent apps unaffected by opening Desk. The existing document-order and keyboard scroll-to-focus are good starting points.

**Desktop target:** large but not wasteful editorial greeting; search input/control visually dominant; pinned spaces form a compact left-to-right row with clear labels and active/unavailable states; tools secondary. Center content with maximum readable width; no second top pill. Use the actual system Island consistently.

**Tablet target:** top chrome respects physical safe areas. Search fills available width with one plain-language label; compact pinned spaces form full-width vertical rows or 2-column cards only when names fit, never forced fixed 180px controls that unexpectedly wrap. Use touch-sized pin actions. A long list scrolls naturally and keeps focused controls visible above the OSK. “Settings” entry should not expand a giant session-recovery form in the primary Desk view.

**State coverage:** empty pins, 1 pin, 9 pins, missing pinned app, Group pin with two members, edit-mode, error, virtual keyboard, 200% text, long CJK/Arabic Group names, RTL layout.

### 2. System Island

**Desktop target:** top-center floating 3-part pill for notification/activity/quick control; compact status such as time may be adjacent but not a permanent taskbar. On ordinary screens no imitation notch. On notched hardware the Island must visually split around the actual obstruction without placing interactive targets under it.

**Tablet target:** preserve useful touch-safe central controls but use responsive choreography: screen-wide top zoning is reserved by the shell, not guessed by independent windows. App command trigger uses a separately allocated region or contextual overlay; when available width is limited, the app control moves to its own non-overlapping row / bottom sheet affordance. Notification and Quick Settings become bottom sheets or height-constrained panels.

**Principle:** The system plane is for **system operations** only. Focused app commands belong to the app; Group arrangement belongs to Group controls; app launcher belongs to Desk/search rather than Island.

### 3. Contextual application menus

Desktop: name/commands for the actual focused app, not hover; never combine app command list with Group arrangement in one menu. Keep app context stable while its menu is open; gracefully disable stale actions on focus/close, then update upon dismissal, as currently implemented. Maintain native in-app menu as a fallback.

Tablet: app-name control with a 44px+ target, but not in the same top coordinate range as the Island. Use a near-trigger popup if there is room; otherwise a bottom sheet with vertical list and a clear close action. The 58px existing command buttons are a good touch baseline. Test long multi-script labels and same-process ambiguity. Ensure opening keyboard doesn't hide the selected menu item.

### 4. Overview and Group switcher

The **actual Niri Overview is a compositor surface**, not a set of Qt cards. It preserves app textures better than a fake shell thumbnail view. Current Floe bottom switcher shows button-sized window titles; its “Live overview” button opens Niri. This should remain explicitly labeled a **switcher** until Floe has compositor-provided previews and transitions.

Desktop: zoom around current space with coherent left-to-right pinned/dynamic ordering. Group one visually coherent destination, but show member-app focus. A source-of-truth change is needed before independent Group reorder and orientation semantics are complete.

Tablet: the existing bottom switcher can be a practical interim sheet, but must not claim to be the final Overview. No fullscreen panel on a portrait screen unless explicitly intended. Actual gesture affordances cannot be assumed supported by Niri; keyboard/touch alternatives must exist.

## Proposed responsive top-chrome layout contract (design, not current code)

| Mode / width | Island | Application menu | Quick settings |
|---|---|---|---|
| Desktop >=1024 logical px | centered floating compact capsule, max 220–240px | top-left distinct 44px-target app-name control | anchored panel max 360px, >=12px side margins |
| Compact landscape 600–1023px | central compact status/action capsule | separate nonoverlap left zone; collision algorithm needed | constrained popover; may use sheet if width insufficient |
| Tablet portrait 320–599px | touch-safe capsule, approximately <=190px with nonessential text hidden | move contextual action to separately allocated row or bottom context action; do not assume top-left 220px fits | bottom sheet, width=min(available screen minus safe margins), scrollable |
| Laptop with notch | left/right small controls positioned outside measured physical cutout | notched-aware app control placement | anchored outside cutout and protected edge insets |

Use **actual** usable width = logical width − notch/safe-area/OSK allowances; no threshold alone can guarantee no collision. These are target layout rules, not tested QML code. On no-touch desktop, 44px keyboard target still improves usability and should not create a bulky bar.

## View + input contract

- **Work:** real apps are live; horizontal navigation controls Niri columns; Island is available; focused app menu can be reached with keyboard.
- **Overview:** native Niri zoomed workspace, preserve current focus; Island never obscures essential Niri controls; selecting a real app returns to it. Don’t turn a text-only switcher into a fake live texture.
- **Desk:** reveals calm base without closing/minimizing apps; search and pins immediately usable; Esc returns without changing member focus. The system Island does not duplicate.
- **Desktop:** keyboard command for Desk is \`Super+H\`; existing Niri overview uses \`Super+O\`; \`Super+Tab\` currently opens separate switcher. Do not silently reassign in an unrelated theme patch.
- **Tablet:** native swipes are design hypotheses, not production features. Keep reachable explicit controls and avoid stealing gestures from scrollable app canvases.
- **Reduced motion:** communicate perspective changes through position, heading and focus even when transitions are removed; no spatial motion requirement at the price of accessibility.

## Verification matrix for the actual ARM64 VM

Capture real screenshots for each surface at **1280×800, 1024×768, 430×932 and 320×512 logical pixels** in both themes. The 320px case is a stress test; 430px representative portrait tablet is a more realistic design review profile. In every size:

1. Desk: no pins, 3 pins, settings expanded, 200% text, virtual keyboard visible.
2. Work + Island with menus disabled, enabled/focused, and open popup. Show a two-member Group focus change while a menu is open.
3. Quick Settings and notifications (empty and with long multilingual text), and active keyboard focus.
4. \`Super+O\` Niri live Overview with at least 3 real apps; \`Super+Tab\` Floe switcher separately.
5. Portrait/landscape safe-area test: no app trigger/Island overlap, no clipping, no hidden close/Back action.
6. Confirm theme toggling doesn't disturb window IDs, focus or pinned order; confirm theme persistence after restarting Quickshell.
7. Keyboard-only navigation, Tab order, Escape dismissal, focus visible, screen-reader labels; 44×44+ touch targets; inspect with Arabic/Indic/CJK sample strings.

**Actual screenshot steps:** Use Niri's built-in \`screenshot-screen\` action (via a configured keybind or a verified local \`niri msg action\` invocation), which saves according to \`screenshot-path\`. Ensure the guest's output is a *logical* target size and record \`niri msg -j outputs\`, compositor version and screenshot file names. Do not use a browser mockup as evidence for native rendering. Avoid private account data in screenshots.

See the Niri [screenshot action documentation](https://github.com/niri-wm/niri/blob/main/docs/wiki/Configuration%3A-Key-Bindings.md) and [screenshot-path configuration](https://github.com/niri-wm/niri/blob/main/docs/wiki/Configuration%3A-Miscellaneous.md).

## Sequenced design actions

1. **Correct the top plane first:** eliminate Island/app-menu collision and Desk duplicate; preserve separate app and system command semantics.
2. **Fix touch and keyboard affordances:** 44px targets, normal Qt controls and accessible labels, focus ring visibility.
3. **Separate the concepts in naming:** “Spaces switcher” (text list) vs “Niri Overview” (live view), until combined.
4. **Design actual two-mode UI composition:** make Arctic Daylight background transitions intentional, not only a palette switch.
5. **Capture native proof before merge:** no claimed approval on screenshots, hit-testing or RTL until a real VM run.

The existing master spec remains authoritative on semantic model, input policy, Group identity and compositor boundaries. This audit does not authorize replacing Niri, hiding unsupported app commands, inventing composited previews or reorganizing a user's space when switching perspectives.
