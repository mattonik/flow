# Flow — Master Product & Platform Specification

**Version:** 1.1 — consolidated anchor for Codex  
**Date:** 12 November 2026  
**Status:** Product vision and architecture decisions; browser prototypes exist, native implementation is in progress and **must be audited independently**.  
**Owners:** Product/design direction — user and UX collaboration; implementation — Codex/engineering.  
**Canonical title:** `FLOW_MASTER_SPEC.md`  
**Purpose:** One authoritative product, UX and engineering reference for building Flow. A Codex implementation must not infer that a simulated prototype feature is a native OS capability.

> **Vision:** Flow is one personal computing environment that adapts from touch to desktop, remembers how work is arranged, and makes the device disappear into the experience.
>
> **Core metaphor:** Your **Desk** is the foundation; applications are the stacks of work on it; **Flow** is how you move between them.

## 0. How to use this document

**Status vocabulary** (applies throughout):

- **COMMITTED** — explicit product or architectural decision. Changes require a documented ADR and product approval.
- **REQUIRED** — planned capability; not necessarily implemented. Tests gate completion.
- **HYPOTHESIS** — direction to prototype and validate with users and platform constraints.
- **RESEARCH** — candidate technology, not selected or guaranteed feasible.
- **LONG TERM** — vision to preserve without blocking initial milestones.
- **IMPLEMENTED** — only when verified in an actual running NixOS/Niri environment. An HTML demonstration never qualifies.

**Source-of-truth hierarchy:** This master specification and accepted ADRs define product intent; repository tests and verified implementation-status records describe what actually works; HTML versions v0.1–v1.0 are UX references, not implementation blueprints or proof of feasibility. If docs conflict, flag and reconcile rather than silently choose. Keep the decision register and test matrix updated when changing behavior.

**Engineering instruction:** Inspect the existing Flow repository and VM configuration before writing code. Preserve working parts. Never assert a feature is tested without recording the environment, commands/evidence, actual results and limitations.

---

## 1. Product definition and ambition

Flow is a **dark-native, fullscreen-first, spatial Linux desktop environment** built on existing Linux components and a distinct interaction model. It is **not** a new kernel, a Linux distribution theme, a GNOME/KDE fork, a Hyprland configuration, or a collection of custom applications.

The medium-term ambition is a serious **daily-driver alternative to macOS + iPadOS**, including professional design, research, heavy browser use, software development and virtualization. The longer-term ambition includes tablet, laptop, desktop, convertible devices, and eventually a phone that can expose a full desktop-style session on an external monitor (a DeX-like convergence experience).

### 1.1 Target usage

- **Design:** Figma in a browser, large shared documents, design systems, prototype playback, fonts, presentation, video meetings and screen sharing.
- **Research:** many tabs (test with 30–50), multiple browser windows/profiles, files and reference materials, clipboard, downloads, previews and drag/drop.
- **Development:** editors, terminal, AI coding tools, Git, local containers/services, several Linux VMs, and Windows VMs when possible.
- **Mobility:** external keyboard, trackpad, pencil/touch, rotation, docks, monitors, headphones/AirPods, suspend/resume and battery life.
- **Shared devices:** multiple secure local users, including a guest, with isolated sessions and per-user organization.

### 1.2 Experience principles — COMMITTED

1. **Content owns the screen.** Avoid permanently visible large taskbars, top bars, card-heavy dashboards and oversized app switchers.
2. **Fullscreen-first, not fullscreen-only.** One app feels immersive; Groups and Layers progressively reveal multitasking.
3. **Spatial memory.** The user can always find important work at a predictable screen/space address.
4. **One workspace model across inputs.** Touch, pen, trackpad, mouse and keyboard change interaction physics and controls, **not** saved group identity or space order.
5. **A permanent System plane.** Notifications, quick controls, search, lock and Home are accessible regardless of where the user is in Flow.
6. **Desk is beneath work.** Overview and Desk are spatial perspectives of the same open applications; showing them must not close apps.
7. **Dark native default.** Restrained IceBear-inspired design, deep graphite, thoughtful hierarchy, subtle mint accents.
8. **Existing applications first.** Standard Linux apps must work without adopting a Flow SDK. Integrate established services instead of rewriting them.
9. **Touch/tablet first-class from architecture day one.** Initial milestone may be desktop-first in execution; design and contracts must not preclude touch.
10. **Reproducible and reversible.** NixOS configuration, per-user state, testable integrations, rollback and a fallback desktop.
11. **Honest capabilities.** Distinguish demo simulations, hypotheses, hardware requirements and working native behavior.
12. **Framework-independent ecosystem.** Flow is a platform, not a QML-only app system.

### 1.3 Explicit non-goals for the early release

Do **not** implement a new kernel, compositor, file system, package manager, Bluetooth/audio stack, network stack, login/PAM implementation, browser, complete file manager, Windows/macOS compatibility layer, proprietary sync service, or GPU driver. Do not switch to Hyprland. Do not create fake apps simply to fill the Home screen. Do not build elaborate compositor animations before native session reliability is proven.

---

## 2. Architecture — COMMITTED direction

```text
Device hardware (x86-64 / aarch64; graphics, display, touch, pen, keyboard, Bluetooth, dock)
  └─ Linux kernel + DRM/KMS + Mesa/GPU drivers + input/USB/network/audio drivers
      └─ NixOS (systemd, login, accounts, package configuration, updates, rollback)
          ├─ Standard system services (PipeWire/WirePlumber, BlueZ, NetworkManager,
          │   UPower, portals, storage/GVfs/UDisks, Polkit/PAM, Flatpak/XWayland)
          ├─ Niri Wayland compositor [COMMITTED]
          │    └─ Native window layout, focus, workspaces, scrolling, outputs, input,
          │       compositor event stream and supported IPC commands
          ├─ Flow Platform Service(s) [TO BUILD]
          │    ├─ Space/group/pin semantic model and persistence
          │    ├─ Input-aware navigation, display assignment, launch/restore policies
          │    ├─ Versioned integration API + events + authorization
          │    └─ Niri adapter, system bridges and CLI
          ├─ Flow Shell via Quickshell/QML [COMMITTED]
          │    └─ Island/notch, Desk, Overview UI, app menus, search, quick controls,
          │       status, notifications, lock integration and shell surfaces
          └─ Standard apps and optional Flow-aware apps
               ├─ GTK / Qt / GPUI / Electron / browser / XWayland / Flatpak
               └─ Optional compatibility bridges and sync providers
```

### 2.1 Firm technology choices

- **NixOS** — operating system and declarative/reproducible deployment.
- **Niri** — compositor/window manager; scrollable horizontal columns match the product vision. **Do not adopt Hyprland** on the basis of Omarchy's implementation.
- **Quickshell/QML** — system shell and native Wayland system surfaces. Favor one well-structured long-running shell process with modules, controlled lifecycle and clean internal APIs.
- **Flow Platform API** — independent of Quickshell and Niri; intended for GTK, Qt, GPUI, Electron and other clients.
- **Existing Linux infrastructure** — audio, Bluetooth, networking, screen sharing, file pickers, power, package management, security and storage.

### 2.2 Clear separation of responsibilities

| Responsibility | Owner | Boundary |
|---|---|---|
| Window positions, focus, input capture, compositor animations | Niri | Do not fake arbitrary app surfaces in Quickshell |
| Logical identity of spaces, pins, Groups, restoration intent | Flow Controller | Never assume Niri column indices equal stable space IDs |
| Desk/Island/search/notifications and app command surfaces | Quickshell | UI never becomes system's only source of truth |
| Cross-framework app integration | Flow API | No third-party app dependency on shell internals |
| Authentication, packages, device management | Linux/NixOS services | Flow exposes UI and policy, does not invent substitutes |
| Native app UI and app-specific documents | Each application | App controls remain usable without Flow integration |
| File/settings/workspace sync | Independent service providers | Offline-capable, per-user, permission-aware |

### 2.3 Architectural constraints

- Niri has **horizontal columns inside its workspaces**; don't conflate logical Flow spaces with vertical Niri workspaces. A dedicated mapping/reconciliation layer is necessary.
- Use Niri's event stream where available; maintain a state model and reconcile after crashes/restarts instead of brittle command-only state assumptions.
- Use normal compositor-managed windows for **application Layers** and appropriate layer-shell surfaces for **trusted shell UI**. Do not misuse Wayland layer-shell to impersonate arbitrary app windows.
- High-fidelity transforms of *real application textures* (Work → Overview → Desk) may require upstream Niri changes; Quickshell overlays alone cannot do everything. Start with Niri's own overview mechanisms and prove feasibility.
- GPU hardware acceleration and touch/rotation support are **device-specific**; neither Quickshell nor Wayland supplies missing drivers.
- Maintain the current functioning desktop/login session as a recovery fallback; ensure Flow shell failure does not force OS reinstall or lock the user out.

---

## 3. Spatial primitives and terminology

**Display**: physical or logical output, with persistent hardware identity (if available), user-assigned number, geometry and profile.  
**Flow**: an ordered horizontal collection of logical spaces for a given display.  
**Space**: one addressable destination containing one or more windows; may be pinned or dynamic.  
**Group**: a space containing multiple apps/windows with saved relationship, layout ratios and identity.  
**Layer**: a temporary window/panel above active work; does not disrupt base space.  
**System**: global shell plane, not a normal space.  
**Desk**: calm surface revealed by moving work aside; home/tools/start point.  
**Overview**: organized, zoomed-out arrangement of work, with spatial order and direct manipulation.  
**Pinned app**: launcher favorite, **not** a fixed space.  
**Pinned space**: ordered anchored location with semantic ID, display preference, layout and optional launch policy.  
**Activity**: progress or ongoing event surfaced through the system island (media/call/file operation/build etc.).

### 3.1 Space invariants — COMMITTED

- Every display has a **separate** Flow; each Flow's pinned spaces occupy **contiguous positions starting from the left**. Dynamic spaces always follow.
- A Group is **one navigation destination** regardless of its number of windows.
- Pinning an app shortcut never automatically pins a space; pinning a space can contain one or multiple apps.
- Space identity is stable and separate from a compositor window ID and visible index.
- The current app and Group arrangement must not be destroyed by showing Desk/Overview, switching input methods, rotation, or disconnecting a monitor.
- Opening an already running app should **prefer focusing its existing window/space**; newly launched apps should not unexpectedly replace or disturb pinned spaces. Default placement after the current position is a **hypothesis**, adjustable after testing.
- When an app cannot restore, retain a clear placeholder or repairable state rather than silently dropping the pin.

---

## 4. Magnetic Flow — primary navigation model

**COMMITTED product concept:** One horizontal spatial canvas with adaptive navigation response.

| Input | Desired motion | Alignment |
|---|---|---|
| Finger/touch | Deliberate, strong snap to full app or Group | Complete destination, avoid awkward half-visible app |
| Trackpad | Continuous movement while gesture active | Gentle magnetic alignment at meaningful column boundaries when settling |
| Mouse wheel | Predictable movement by space, optionally fine control | Alignment generally on stop |
| Keyboard | Exact focus jump | Land precisely on numbered pinned space; restore app focus |
| Ultrawide display | Multiple columns may remain visible | Left/center/right useful alignment; no forced huge fullscreen width |

**Rules:** Input changes navigation physics, **not** workspace content. Edge or multi-finger gestures must avoid stealing horizontal gestures inside Figma, browsers, canvases and file lists. Reduced-motion mode, keyboard-only and screen-reader alternatives are essential. Consciously test velocity, gesture thresholds, diagonal motion, boundaries, interrupted gestures, focus and accessibility. Native touch gestures are not assumed to be supported by Niri today; define a compositor feasibility spike and upstream-first contribution strategy.

### 4.1 Space lifecycle — REQUIRED UX to validate

Create space → open app → add second app → form Group → resize/reorder → optional pin → temporary Layer → dismiss/promote → move display → unpin/close. Make these reversible, predictable and discoverable. Do not imply closing a space must kill apps without clear user intent.

### 4.2 Flow Overview — HYPOTHESIS with approved direction

- Zoom out from current work into its actual horizontal location; neighbors reveal themselves.
- Select, focus, reorder, pin/unpin, group/ungroup and move spaces, with a visual pinned/dynamic distinction but **one continuous strip**.
- Drag app onto another to form a Group; pull it out to create a space. Keyboard and accessible equivalents mandatory.
- Multiple monitors appear as distinct spatial strips in display-aware Overview; eventually support dragging Groups between them.
- A refined transition should maintain object identity and spatial anchor rather than switch to an unrelated screen.

---

## 5. Desk, Overview and Work: one continuous place

**COMMITTED metaphor:** The user pushes stacks of papers aside to reveal their desk. Revealing the Desk is **not** minimization, closing, suspension or destructive reorganization.

### 5.1 Three perspectives

1. **Work / Flow:** the active fullscreen-looking app or Group; adjacent spaces are navigated horizontally.
2. **Overview / Organized Desk:** scaled arrangement of the same work in correct space order; editing and navigation surface.
3. **Desk / Clear Home:** work is pushed aside but remains alive. Calm launcher/tools/search surface sits beneath.

### 5.2 Proposed transition grammar — HYPOTHESIS

- Touch: swipe up and hold → Overview; continue swipe up → Desk; swipe down/choose a space → return.
- Desktop: trackpad gesture or shortcut → Overview; further command → Desk; escape/back/select → work.
- Keyboard: `Super+Tab` for Overview and `Super+H` for Desk are **proposals**, not confirmed keybindings. `Esc` returns one level where appropriate.
- The returning app, focus, Group and order remain unchanged unless intentionally edited.
- For early native builds, use simple transitions or Niri's supported Overview and present the complex choreography as future compositor work; do not block daily-driver readiness.

### 5.3 Desk content hierarchy — COMMITTED

A **minimal, quiet** Home; no busy cards or default widget dashboard:

1. Universal search, immediately accessible.
2. Pinned spaces on main display; compact indication of other displays' pins.
3. Small pinned-app shortcut row and a few essential tools (Apps, Files, Search, Settings).
4. On-demand full app drawer; advanced system controls hidden until requested.
5. Optionally small, subdued status and a “Continue where you left off” affordance.

No news/weather feed, productivity stats, or complex home widgets by default. Avoid a permanently visible large bottom app switcher; a subtle gesture handle or on-demand reveal suffices.

---

## 6. Groups and Layers

### 6.1 Groups — REQUIRED

- A Group has independent ID, name, saved membership, relative sizing and display/space placement.
- Common landscape: side-by-side split; portrait: stacked top/bottom with usable proportions.
- Support tabbed and stacked variants where appropriate; the Group remains one destination.
- Layout adapts to orientation/display without changing member apps, their state or Group identity.
- Focus within Group determines app-specific menu context and keyboard target.
- Move across monitors without losing membership. If content no longer fits, reflow rather than truncate invisibly.
- Allow pinning a Group as a single space; e.g., Main/2 = Studio + Notes.

### 6.2 Layers — REQUIRED concept, advanced behavior HYPOTHESIS

- Temporary utility/preview/notes/chat/app over a space; dismiss without displacing underlying layout.
- On portrait tablet, bottom-sheet treatment is a useful default; on desktop, compact floating window.
- Promote Layer into Group or own space as a later capability. Trusted System panels remain separate.
- Focus management, screen-reader order, transient permission prompts and app-native drag/drop require tests.

---

## 7. System Island, cutouts, and permanent system access

### 7.1 System plane — COMMITTED

Notifications, Home, search/launcher, settings, quick controls, ongoing activities and reliable lock access must work irrespective of the active space/app. System is above the work and not a regular Flow space. Apps must not hide essential recovery actions in a permanent fullscreen trap.

### 7.2 Tablet island

- Small top-centered **floating** capsule **over** app content, not a bar with reserved height. Accommodate display safe areas, camera holes and corners.
- Tentative gestures: left → notifications, right → quick settings, down → Desk/Home; tap → compact actions. Validate against application gestures.
- Middle section holds a brief contextual activity: progress, call, timer, music, sync/build task, battery/charging. Expand only for relevant information.

### 7.3 Notched MacBook / camera-pill laptop

- Do **not** create an oversized duplicate island beneath a notch.
- Visually absorb the physical black cutout; small actions on **left/right of it** and concise activity when needed. Maintain hit targets and accessible actions.
- Actual cutout geometry needs device data/configuration. Simulator presets do not prove native cutout handling.

### 7.4 Conventional desktop/external monitor

- Approximately three compact system controls or equivalently unobtrusive status/command affordance. No fake camera notch on unnotched monitors.
- Do not let UI permanently consume a large vertical strip.
- Notifications have history, do-not-disturb, priority/action semantics, privacy/redaction and normal focus behavior.

---

## 8. Menus, application commands and universal search

### 8.1 Contextual application menu — approved HYPOTHESIS

- **Desktop:** shallow app menu/name near the top-left, optionally `File / Edit / View / Window / Help`, adapting around notch and system status. Only expose menu items that the focused application actually supports. Reveal on hover/shortcut when appropriate.
- **Tablet:** compact app-name/button at the top-left reveals touch-friendly commands, not a permanent macOS-style menu strip.
- **Groups:** change menu dynamically when focus switches between Group members.
- **Island remains system-only**; do not mix application menu with notifications/settings.
- Preserve original in-app headers and menus unless replacement has proven parity. Qt DBusMenu, GTK/AT-SPI and Electron support vary; global menus are best-effort.
- Application command indexing can also feed universal search when a reliable invocation interface exists. Do not fabricate commands for unsupported apps.

### 8.2 Universal search — REQUIRED

One palette for apps (`.desktop`, Flatpak etc.), files (permission-aware), pinned/temporary spaces, system actions, settings, and optional contributed application commands/activities. Good ranking and keyboard navigation; touch-friendly results; per-user isolation; no unconsented searching in another account.

Suggested shortcut `Super+K` (browser demos may use Ctrl/Cmd+K because host shortcuts can be intercepted). Plugin/provider model can evolve, but a useful minimal app+space+settings search should come first.

### 8.3 Launcher and pins

App drawer is on demand, searchable and shows installed supported applications; launcher shortcuts are distinct from pinned spaces. Unintegrated Linux apps must launch normally and can be managed through Niri.

---

## 9. Pinned spaces, display addressing and keyboard grammar

### 9.1 Rules — COMMITTED

- Pinned spaces always **start at position 1** on each display and occupy the leftmost consecutive sequence.
- Per-user, per-display ordering; dynamic spaces follow.
- Example: Main/1 = Mail, Main/2 = Studio + Notes; External 1/1 = Terminal, External 1/2 = Files; External 2/1 = Writing.
- Store unique logical space ID, label, apps/windows identifiers when recoverable, Group relationships, layout constraints, target display role/ID, order, optional launch-on-login policy, last focused member and restoration status.
- Persist declarative defaults separately from **mutable** per-user space state.

### 9.2 Keybindings — COMMITTED grammar or explicitly proposed

| Input | Action | Status |
|---|---|---|
| `Super+1` … `Super+9` | Focus pinned space 1–9 on **main** display | COMMITTED |
| Hold `Super+Alt`, `1`, `1` | External display 1, pinned space 1 | COMMITTED concept, exact OS dispatch to validate |
| Hold `Super+Alt`, `1`, `2` | External display 1, pinned space 2 | COMMITTED concept |
| Hold `Super+Alt`, `2`, `1` | External display 2, pinned space 1 | COMMITTED concept |
| `Super+Shift+…` | **Move** active app/Group rather than focus | RESERVED; exact grammar TBD |
| `Super+K` | Universal search | APPROVED |
| `Super+Tab` / `Super+H` | Overview / Desk | PROPOSED |

Interpret the external sequence as **modifier + display digit, then space digit**. Don't first focus Main/1 as an unwanted intermediate action. Show a small temporary indicator after the display digit; move focus only when sequence completes. Feedback for unknown display/space, timeout, cancellation, keyboard layout/remapping and accessibility is required. Focus follows navigation immediately.

Shortcuts on external monitors must follow **user-stable display numbers**, not unstable kernel output order. Physical display matching may use EDID/dock information but must handle duplicates and changes safely; logical display assignment is user-adjustable.

**Engineering caveat:** Do not assume Niri can natively parse every multi-key chord; a small authorized input/shortcut state machine or alternate feasible grammar may be needed. Validate before locking keybindings into implementation. Never intercept ordinary typing or system secure-attention sequences.

---

## 10. Multiple monitors, docks, DisplayLink and input mapping

### 10.1 Native external display support — REQUIRED

Extend/mirror; resolution, refresh rate, scaling, primary output, relative arrangement, rotation, monitor identification; hotplug, lid close, sleep/wake, move window/Group across displays; cross-output keyboard focus and drag/drop; touch mapping to rotated/external screens; user-visible display settings.

Each monitor has **its own Flow**. When a display disconnects, move or temporarily surface its spaces on a remaining display without destroying pinned identity. On reconnect, restore according to saved profile while preserving user changes made during disconnection. Treat external display identity and shortcut number separately.

### 10.2 DisplayLink — REQUIRED research/testing, not universal guarantee

Integrate driver status and troubleshooting in **Displays & Docks**, not as a separate always-open manager. Candidate Linux stack includes DisplayLink driver and EVDI. Device/kernel/compositor combinations may differ. Test on named docks with real monitor load, mixed outputs, hotplug, bandwidth, cursor/video performance, standby/resume, and recovery. Licensing may require user steps.

### 10.3 Device convergence

Tablet → keyboard/dock/monitor should preserve workspace identities and reflow layouts. Long-term phone desktop mode may run a compact local UI plus independent desktop Flow on external output. Do not force one phone window size on large displays.

---

## 11. Multi-user accounts, session management, security and recovery

**REQUIRED for trustworthy daily driving:** real Linux accounts, isolated home directories, per-user pins/Flow state, app permissions, device profiles, guest cleanup policy, session switching, secure lock and unlock backed by PAM and supported session facilities. Never ship the browser demo's simulated users as native authentication.

- Lock screen: minimal dark UI, account selector where permitted, battery/network status without leaking active documents.
- Switching users must not expose prior session windows, screenshots, notification content, clipboard, files or search index. Privileged system actions require authentication/polkit as appropriate.
- Shell can crash/restart without losing app windows or trapping the user. Recovery shortcut/session and fallback desktop must be available.
- Sandboxed apps may not control arbitrary windows, switch accounts, read system notifications or enumerate other apps' private data without authorization.
- User content/sync tokens must not leak through logs and debugging output. Store secrets via appropriate credential facilities.
- Accessibility, secure screen-sharing prompts, privacy status indicators and permission revocation should be first-class.

---

## 12. Stable, toolkit-independent Flow Platform API — COMMITTED

**Core rule:** Flow is a platform, not a framework. Ordinary apps work unmodified. Optional integrations must be callable from GTK, Qt, GPUI, Electron, Rust, JavaScript and other ecosystems without importing Quickshell classes or Niri-specific IPC.

### 12.1 Proposed transport and layering

- **Primary transport:** versioned introspectable **session D-Bus** interfaces, with explicit per-user/session ownership and policy checks.
- **Clients:** a stable `flow` CLI (for automation/tests), optional small SDKs, an event subscription mechanism, and discoverable app capabilities/providers.
- **System integration:** use standard XDG portals wherever a standardized API exists, especially for Flatpak. If a novel permission-sensitive Flow API requires sandbox access, design an explicit portal/permission broker rather than granting every Flatpak unrestricted D-Bus control.
- **Adapter:** one backend talks to Niri's IPC and translates events/commands; neither apps nor shell-facing API consumers should use raw Niri identifiers as durable IDs.
- **Quickshell:** a **client** of Flow state and APIs, not the only process that defines state.

### 12.2 Proposed capability surface (specification, not implemented methods)

| Domain | Possible actions/events | Notes |
|---|---|---|
| Spaces | list/focus/pin/unpin/reorder/create/restore; `space-changed` | IDs stable, display-scoped positions |
| Groups/windows | request-group/ungroup/move/focus/float; `window-focused` | privileged/mutable; policy and focus checks |
| Displays | enumerate, suggested output, move-space; connect/disconnect events | unprivileged apps get safe metadata |
| Search | register provider, return results/actions | timeouts, ranking, ownership, permission scopes |
| System | request Desk/Overview/launcher | apps may request UI, not impersonate it |
| Activities | publish/update/complete progress/music/build/file transfer | app owns activity and expiry; avoid notification spam |
| Events | subscribe to permitted state changes | avoid exposing sensitive title/path metadata broadly |
| Capabilities | negotiate API version, supported feature set | graceful failure for unsupported actions |

### 12.3 Suggested early CLI contract — PROPOSED

```bash
flow spaces list
flow spaces focus <space-id>
flow spaces pin <space-id>
flow overview show
flow events watch
```

These are **target interfaces only**, not existing commands. Version names, D-Bus method signatures and data contracts should be finalized in API IDL/ADR and tested. Return structured errors such as `not-found`, `not-authorized`, `unsupported`, `display-unavailable`, `app-not-running` rather than silently failing.

### 12.4 Security model and interoperability requirements

- Define least-privilege roles: observer, caller for own app/space, trusted shell/controller, privileged system actor; no broad window-control default for ordinary apps.
- Rate-limit subscriptions and search providers; handle disappearing apps/services and malformed payloads. Event timestamps/ordering, replay/snapshot and reconciliation documented.
- Explicit capability/version negotiation and backward compatibility; avoid breaking client applications whenever shell UI changes.
- App-owned activities cannot spoof shell notifications from another user or from security-sensitive services.
- Include opt-in provider registration and clear uninstall/revoke behavior.
- No Flow SDK should be mandatory for normal Wayland/Flatpak apps.

### 12.5 Reference integration scenarios

- **Flea:** contributes file search, opens selected directory, publishes copy progress to Island, perhaps participates in a Desk file shelf.
- **Development tool:** publishes project/build task as an Activity and offers command-palette actions where authorized.
- **Browser:** remains fully usable without Flow integration; optional automation is separately permissioned.
- **VM launcher:** opens a guest UI as a normal Flow space, publishes startup/progress and returns focus appropriately.

---

## 13. Linux application compatibility and menu fallback

**REQUIRED baseline:** GTK, Qt, GPUI, Electron, native Wayland, Flatpak and selected X11 apps via XWayland must behave like ordinary apps. They retain their native menus/styles unless safely integrated.

- Correct portal backend for file dialogs, screencast/window picker and permissions; test drag/drop, clipboard, notifications, app opening, login autostart and input methods.
- Fullscreen-looking columns should generally be implemented using maximization/column sizing where exclusive fullscreen would suppress needed system controls.
- Global app menus are optional enhancement. Discover menu structure using standards/bridges when available; otherwise keep in-app menus. Some contemporary GTK apps intentionally have no global menu export.
- Live desktop command search only indexes actions actually exposed by the app. File/Edit/View labels should not be fabricated for incompatible apps.

### 13.1 Windows apps — RESEARCH / later

Wine/Bottles/Proton for compatible software; full Windows guest via QEMU/KVM/libvirt plus a window/remote-app bridge (WinBoat/WinApps-type) for apps that need Windows. Consider Windows licensing, architecture, GPU, clipboard, file exchange, focus, security and battery cost. Do not guarantee compatibility. Niri can manage resulting guest windows as normal windows if integration permits.

### 13.2 macOS apps — LONG TERM / weak feasibility

Darling is a research candidate, not an assurance of mainstream graphical macOS app compatibility. For proprietary Mac-only workflows, remote access to a **real Mac** may be the pragmatic bridge. Avoid promising native macOS GUI execution on Linux.

---

## 14. File management and the Flea/GPUI opportunity

A reliable Files experience is **REQUIRED**, but a custom file manager is **not** an early priority.

Needs: local/remote folders (SMB/NFS/SFTP/WebDAV where supported), removable and encrypted volumes, previews, metadata, large directories, file actions/undo, search/tags, drag/drop across Groups and monitors, portal-based Open/Save, user permissions, mount errors and operation progress visible anywhere in Flow. Touch-friendly selection, file shelf/temporary collection, Quick Look as a Layer, fullscreen views and column browsing are attractive options.

**Flea** (`https://github.com/thisisgm/flea`) is a high-priority **research candidate**, originally tied to Omarchy. Its active 0.4.x UI migrated from Quickshell/QML to Rust **GPUI**. Investigate NixOS/ARM64 compilation, Omarchy dependencies, portal ownership, Niri/Wayland integration, file shelf, IPC, license, accessibility and touch ergonomics. Do not assume it already works on Flow. Maintain Dolphin or another reliable general file manager as fallback.

**GPUI decision:** **Do not replace Quickshell.** GPUI is a first-class option for performance-sensitive apps and perhaps individual utilities; it must integrate via the Flow API rather than duplicating shell state. Its fast-evolving APIs and system integration maturity are engineering considerations, not reasons to abandon the Niri/Quickshell stack.

---

## 15. Everyday Linux system integrations

Flow's polish should come from seamless orchestration of proven services.

| Function | Likely Linux foundation | Flow UX role / test focus |
|---|---|---|
| Wi-Fi, Ethernet, VPN | NetworkManager | reliable connect/disconnect/status and captive portal flows |
| Bluetooth | BlueZ | pairing, reconnect, profiles, device status |
| Audio, mic, media | PipeWire + WirePlumber, MPRIS | volume/routing, input selection, device switching, Island activity |
| AirPods | standard Bluetooth profiles; optional integration | playback/calls first; advanced Apple switching not guaranteed |
| Battery, power profiles | UPower, systemd/power management | truthful battery, suspend, lid, thermal behavior |
| Notifications | Freedesktop service | history, DND, actions and privacy redaction |
| Screenshots and screenshare | PipeWire + XDG desktop portals | conferencing, recording, permission surfaces |
| File dialogs and volumes | XDG portal, UDisks/GVfs/GIO | open/save and drive mounting |
| Authentication and lock | PAM, polkit, display/session manager | secure users and fallback |
| App installation | Nix/NixOS, Flatpak | app discovery without reinventing store |
| External displays | Niri outputs, hardware/driver stack | arrangement, profiles, hotplug, DisplayLink where tested |
| Virtual machines | QEMU/KVM, libvirt, virt-manager alternatives | performance and guest UI integration |

**Bluetooth/AirPods:** basic headphones and microphones should be a priority to validate on hardware. macOS-grade automatic device switching, Apple-only controls and firmware features are **not promised**.

**AirDrop/nearby share:** preferred early solution is LocalSend (installed on both devices); explore KDE Connect for cross-device clipboard/notifications depending on platform support. Native AirDrop protocol interoperability is research only. Share sheet/nearby UI should use standard services and permissions.

---

## 16. Flow Sync and cross-device continuity

**LONG TERM, architecture-ready now.** Inspired by Luma Sync's concept, but not dependent on a Luma account or undocumented protocol.

Three independently designed layers:

1. **Files:** established sync such as Syncthing or Nextcloud; selective sync, conflict control and sharing.
2. **User configuration:** pinned apps/spaces, naming, shortcuts, accessibility, preferences, saved Groups (exclude device secrets).
3. **Semantic workspace restoration:** restore intended app/group membership and document context where apps cooperate, reflowing layout to available displays. **No pixel-perfect cross-device restore promise.**

Requirements: per-user identity, device registration/revocation, encryption and key handling, offline-first operation, conflict resolution, version migration, user-controlled data export, and no inter-user leakage. A self-hostable coordinator (possibly on an always-on Raspberry Pi 5) is plausible but **not required** for a functional Flow client. Different device capabilities may prevent some apps from launching; represent them honestly.

---

## 17. Devices, hardware reality and cross-form-factor design

### 17.1 Phased targets

| Class | Role / expected behavior | Feasibility gate |
|---|---|---|
| x86-64 desktop/laptop | First true daily-driver validation, real GPU, VMs, multiple monitors | Native NixOS and kernel hardware support |
| Apple Silicon Mac ARM64 VM | Early Flow development/test host (UTM/QEMU) | **3D acceleration and Niri launch must be demonstrated**; VM graphics uncertain |
| Linux-compatible tablet/convertible | First physical touch/rotation validation | working drivers, gestures, keyboard, pen, virtual keyboard |
| Raspberry Pi 5 | Optional ARM shell experiments, sync server; not current main workstation | GPU support and app parity; don't replace production homelab |
| iPad Pro A12X (12.9 in) | Separate experimental Linux hardware enablement project | boot chain, graphics, touch and kernel drivers; **not** solved by Flow |
| M4 Pro MacBook native | Not an immediate replacement target | Asahi/platform readiness and drivers must be verified |
| Phone with external display | Long-term portable desktop mode | Linux phone drivers, cellular, battery, cameras, USB-C video, modem/suspend |

### 17.2 Workstation requirements

- Browser with dozens of tabs, stable scaling, GPU compositing and Figma performance.
- Figma is browser-supported; Linux local-font workflows (font helper compatibility) must be independently tested, including real documents; assess third-party font-agent security/maintenance.
- Development editors, terminal, Git, containers and host-level KVM/libvirt on **native** hardware. Nested virtualization in ARM UTM does not prove daily-driver VM performance.
- AirPods/microphone, webcam, conferencing, screen sharing, external screens, dock unplug/replug, suspend/resume, battery and network transitions.
- Plan for 32 GB RAM as a sensible high-productivity workstation target, not Flow's minimum system requirement.

### 17.3 VM testing caveats

The initial NixOS VM setup on an Apple Silicon Mac has had networking/installer challenges; Codex is handling that task. UTM graphics acceleration on macOS must be **measured**, not assumed. The native compositor may fail in an unaccelerated VM. Keep a working alternate desktop and SSH access. VM tests validate UX and code paths; device-specific Bluetooth, notches, display docks, real touch and suspend require physical hardware.

---

## 18. Visual language and interaction finish

**COMMITTED default direction:** refined IceBear-inspired dark interface, calm, precise, premium but not decorative. Do not copy proprietary design assets or force themes into third-party apps.

Suggested exploratory token set (not final brand identity):

| Token | Candidate |
|---|---|
| Near-black background | `#0B1015` |
| Primary surface | `#131B21` |
| Secondary graphite | `#172128` |
| Primary text | `#ECF2F0` |
| Subtle text | `#91A6A7` |
| Restrained mint accent | `#B8DDCB` |

Typography is editorial and legible. Use restrained borders, compact status controls, accessible focus rings, high contrast and meaningful motion, with reduced-motion support. Less dashboard clutter; more direct relationship to content. Avoid distracting glassmorphism and large glowing gradients. Responsive density: large touch targets in tablet context; efficient controls on desktop. The always-on shell should have a small footprint and low idle CPU/GPU power draw.

---

## 19. Performance, reliability, accessibility and quality gates

The product should **work reliably before being visually perfect**. Establish measured baselines on named hardware and test after changes. Suggested targets are provisional, not proven results.

- Critical availability: successful graphical login, ability to recover from shell crash, working network/audio/portal/file dialog, functioning lock/unlock.
- Responsiveness: no long UI stalls when opening Overview/search, reacting to Island, plugging displays or launching apps. Measure input-to-feedback and dropped frames under browser/VM load.
- Memory/idle use: quantify shell/controller resident memory and idle CPU/GPU; avoid duplicate long-running helpers and uncontrolled provider indexing.
- Accessibility: keyboard-only complete paths, visible focus, screen reader semantics, input method editor, large type, reduced motion, contrast, target sizes and touch/pen equivalents.
- Security: per-user isolation, sandbox permissions, no spoofed system UI, safe D-Bus method authorization, robust IPC/error handling.
- Observability: structured logs, crash diagnostics without secrets, version metadata, compatibility/status panel, rollback and recovery guide.
- Testing: unit state reducer tests, Niri IPC contract/integration tests, UI snapshots where feasible, VM smoke tests, physical hardware matrix and long-run workday test.

**Status disclosure:** show actual tests executed, not a checklist of imagined success. Do not mark features Done because HTML demos display them.

---

## 20. Required acceptance journeys

Use stable IDs for Codex issue tracking; each has setup, expected behavior and evidence required. Early milestones can implement subsets, but unimplemented journeys remain Open.

| ID | Journey | Acceptance definition |
|---|---|---|
| F-001 | Native session starts | NixOS boots, Niri session launches, Quickshell visible, real apps operable; fallback login available |
| F-002 | Three real apps | Browser, terminal, file manager appear as Niri columns; navigate without focus loss |
| F-003 | Pinned positions | Main/1 Mail and Main/2 Studio+Notes stay leftmost after restart; dynamic windows follow |
| F-004 | Shortcut accuracy | Super+N targets main pinned; multi-display Super+Alt display+space sequence jumps directly without intermediate hop |
| F-005 | Group identity | Two apps grouped and reflowed portrait/landscape while remaining one logical space |
| F-006 | Layer dismissal | Temporary window opens over a space and closes without changing underlying Group |
| F-007 | Desk/Overview | App windows survive reveal/return; focus, IDs and positions preserved, even if animation initially simple |
| F-008 | Search | Finds installed Linux apps, spaces and settings; can launch/focus correctly |
| F-009 | Island/system | Notification/settings/lock controls remain usable regardless of active app; activity status not spammy |
| F-010 | Real accounts | Two local users and Guest isolated; lock/unlock/session switching secure |
| F-011 | Display hotplug | External Flow survives unplug and reconnect as coherent pinned spaces; no lost windows/data |
| F-012 | Portal correctness | File chooser, screenshots, screenshare, clipboard and drag/drop work in representative apps |
| F-013 | Audio/peripherals | Bluetooth headset playback + mic and reconnect verified on actual hardware |
| F-014 | Figma day | Large real Figma file, fonts, prototypes, video-call screenshare and browser load behave acceptably |
| F-015 | Development day | Editor/terminal/Git/containers and VMs operate on supported native host |
| F-016 | Touch day | Real touch navigation, keyboard, rotation and Group selection work without mouse |
| F-017 | API app test | A tiny non-Qt client uses versioned Flow API to query and focus its own space; unsafe request denied |
| F-018 | Shell restart | Quickshell restart does not terminate app windows or corrupt pinned state |
| F-019 | DisplayLink hardware | Named tested dock and monitor layout reconnect reliably with known limitations documented |
| F-020 | App menu fallback | Focused app menu works only for supported menu exports; unsupported native menu remains accessible |
| F-021 | Sync conflict | Offline changes are retained, conflicts visible, user data isolated and restoration semantics honest |

For each, include host/VM model, GPU, compositor version, Nix revision, keyboard/input type, commands, screenshots/recordings, before/after state and known failures.

---

## 21. Delivery phases and release gates

### Phase 0 — Feasibility and baseline (NOW)

- Audit current Codex repo, VM/network, NixOS architecture and graphic acceleration; document what is real.
- Launch Niri + Quickshell on a reproducible test VM or supported native machine; keep fallback desktop and SSH.
- Verify normal Linux app launching, a screen-sharing portal, basic audio/device services and controller access to Niri events.
- Prove a **small Flow API** (CLI + backend) can focus a real space without Quickshell as mediator.

**Exit:** native session and three real apps (F-001/F-002) with reproducible instructions and evidence.

### Phase 1 — Useful desktop MVP

- First real Island and lightweight Desk/search; basic notifications/settings, keyboard shortcuts, real pinned spaces, single display; app focus and restoration.
- Record action errors and crash recovery; make System UI independent of sample apps.
- Basic Groups/Layers and Niri Overview; no high-risk compositor fork.

**Exit:** pinned spaces survive shell restart, real app workflow and recoverability (F-003/F-007/F-008/F-009/F-018 partial).

### Phase 2 — Daily-driver baseline

- Complete portals, browser/Figma fonts, files, audio/Bluetooth, secure users, lock, power, multi-display profiles, stable app/Group lifecycle.
- Run real design/research/development workflows and fix critical breakage before polishing animations.

**Exit:** sustained native-host workdays and a known-issues list; F-010–F-015 substantial pass.

### Phase 3 — Native adaptive touch

- Physical touch hardware, on-screen keyboard, rotation, pen, gesture arbitration, magnetic snap and Group reflow; Niri improvements upstream where possible.

**Exit:** work with touch alone and no regressions for keyboard/desktop use.

### Phase 4 — Ecosystem and device expansion

- Stable public Flow API and docs, app/menu/activities integrations, Flea feasibility/adoption, nearby sharing, per-user sync, Windows bridges, ARM64 expansion and phone desktop mode research.

**Exit:** reproducible multi-device behavior and explicitly supported integrations.

**Engineering discipline:** prioritize **vertical slices**, not a checklist of disconnected UI elements. Implement a capability end-to-end, verify, then expand.

---

## 22. Decision register (for ADR tracking)

| Decision | State | Rationale / qualification |
|---|---|---|
| D-001 NixOS as OS base | COMMITTED | reproducible and adaptable; retain rollback |
| D-002 Niri as compositor | COMMITTED | spatial scroll model; explicit preference over Hyprland |
| D-003 Quickshell as system shell | COMMITTED | mature QML/layer-shell integrations; don't migrate to GPUI now |
| D-004 Stable framework-independent Flow API | COMMITTED | separate apps/shell/compositor; D-Bus preferred, exact contract TBD |
| D-005 Magnetic Flow | COMMITTED concept | native gesture/physics feasibility still to validate |
| D-006 Pinned spaces leftmost on each display | COMMITTED | predictable spatial memory and numeric shortcuts |
| D-007 `Super+N`, `Super+Alt` display/space | COMMITTED intent | key capture & modifier sequence need native spike |
| D-008 `Super+Shift` family for moves | RESERVED | move/overwrite/swap semantics TBD |
| D-009 Desk/Overview/Work spatial model | COMMITTED product concept | real compositor animation degree TBD |
| D-010 Minimal Home and dark default | COMMITTED | IceBear-inspired, avoid busy dashboard |
| D-011 Island adapts to cutouts | COMMITTED | physical notch on Mac, small float on tablet |
| D-012 Contextual app menu | APPROVED TO TEST | native menu export and fallback vary by toolkit |
| D-013 Multi-user on every device class | REQUIRED | OS account isolation/real authentication |
| D-014 DisplayLink | REQUIRED track | explicit hardware matrix; not universal guarantee |
| D-015 Flea/GPUI | RESEARCH | evaluate Files; GPUI allowed for apps |
| D-016 Windows/macOS compatibility | LONG TERM | Windows via Wine/VM; macOS GUI uncertain |
| D-017 Flow Sync | LONG TERM | independent sync modules, self-hostable preference |
| D-018 Phone DeX-like mode | LONG TERM | unify space semantics across form factors |
| D-019 Omarchy as implementation reference | COMMITTED reference only | learn Quickshell/polish; not Hyprland |
| D-020 First release on standard Linux hardware | COMMITTED sequencing | Mac VM as development host; native touch/hardware testing later |

---

## 23. Unknowns and research questions (explicit, not silent assumptions)

1. Does current Niri permit our desired touchscreen gestures and magnetic settling without a long-lived fork? Which upstream patches would be needed?
2. Can Niri columns/stacked windows map safely to logical Groups under relaunches, tabbing, fullscreen, and external monitors? What is the stable identity strategy?
3. How to perform real Work→Overview→Desk animations while preserving application surfaces and interaction? Accept simple behavior at first.
4. What keybinding parser/shortcut daemon can safely recognize `Super+Alt` then two digits without OS conflicts and focus glitches?
5. Which GPUI/Flea versions build on our pinned ARM/x86 NixOS and how well do they support touch/portals?
6. How reliable is Figma font support and browser GPU rendering under native Linux for the actual professional workflow?
7. What is the physical DisplayLink hardware compatibility baseline for Niri, EVDI and kernels?
8. Does a global app menu exporter offer enough consistent Qt/GTK/Electron coverage to justify visible chrome?
9. Which secure login/lock and screen-sharing portal configurations should be selected on NixOS/Niri?
10. What is the first true touchscreen/pen hardware target? Which devices are realistic for native test coverage?
11. What state and which user secrets may sync, and how do device conflicts and account revocations work?
12. How should a pinned space handle multiple app instances, manually moved windows, dead apps, or closed documents?
13. How will Flatpak integrate permission-aware Flow API capabilities without overbroad bus access?
14. What are measured shell/controller battery, memory, startup and animation costs on modest hardware?
15. Should Flow support separate named workspace profiles (work/personal/travel) per account in a later phase?

---

## 24. Inspirations and reference implementation watchlist

These are **research inputs, not technology choices or verified compatibility commitments**:

| Project | URL | What to investigate |
|---|---|---|
| Niri | https://github.com/niri-wm/niri | compositor IPC, native scrolling, outputs, touch and overview |
| Quickshell | https://quickshell.org/ | Wayland shell surfaces, system services, declarative QML |
| NixOS | https://nixos.org/ | stable packaging, modules, service integration, rollback |
| Pond (Butterfly) | https://www.butterfly.so/pond | spatial Niri UX, Quickshell patterns, app navigation |
| Omarchy | https://omarchy.org/ | polished integration of existing Linux tools, Quickshell shell/plugins, monitors, Windows VM; **do not adopt Hyprland** |
| Omarchy source | https://github.com/omacom/omarchy | modularity, IPC, notifications, display configuration, practical hardware services |
| Flea | https://github.com/thisisgm/flea | Rust/GPUI file manager, previews, shelf, portals, IPC, touch viability |
| GPUI | https://github.com/zed-industries/zed/tree/main/crates/gpui | optional high-performance app framework, not shell replacement |
| Luma | https://simplyluma.com/ | cross-device app/sync UX and system polish; not a base dependency |
| Aurora Silicon | https://aurorasilicon.org/ | user-supplied research bookmark, inspect separately |
| LiberNT | https://libernt.com/ | user-supplied research bookmark, inspect separately |
| ShojiMac | https://shojimac.com/ | user-supplied research bookmark, inspect separately |
| WildOS | https://wildos.io/ | user-supplied research bookmark, inspect separately |
| LocalSend | https://localsend.org/ | cross-platform nearby file sharing |
| KDE Connect | https://kdeconnect.kde.org/ | phone-device clipboard/notifications/file exchange |
| Syncthing | https://syncthing.net/ | local-first file synchronization |
| Nextcloud | https://nextcloud.com/ | file/share/account backend candidate |
| PipeWire | https://pipewire.pages.freedesktop.org/pipewire/ | audio and capture services |
| BlueZ | https://www.bluez.org/ | Bluetooth stack |
| XDG Desktop Portals | https://flatpak.github.io/xdg-desktop-portal/ | sandboxed access, file dialogs, capture |
| DisplayLink EVDI | https://github.com/DisplayLink/evdi | USB GPU driver integration |
| Wine | https://www.winehq.org/ | Windows app compatibility candidate |
| Bottles | https://usebottles.com/ | managed Wine prefixes |
| Darling | https://www.darlinghq.org/ | macOS compatibility research only |
| UTM | https://mac.getutm.app/ | Apple Silicon ARM64 VM development/test |

**Design reference distinction:** Pond is particularly valuable for spatial interactions; Omarchy for a coherent everyday product and Quickshell integration; Luma for device/adaptive-application ambitions. Neither changes Niri/NixOS/Quickshell as the chosen stack.

---

## 25. Collaboration contract: Product ⇄ Codex

**Product/design:** owns intent, priorities, accessibility decisions, behavior and UX acceptance.  
**Codex/engineering:** audits actual repository, proposes ADRs for uncertain mechanisms, implements and validates full vertical slices, documents tradeoffs and rollback.  
**Review cycle:** concrete user journey → proposed implementation and constraints → runnable build/test evidence → UX review → fix → decision/status update.

For every engineering PR/milestone, provide:

1. Summary of actual changes and files affected.
2. Feature status: working / partial / blocked / not attempted, **including hardware/VM environment**.
3. Tests run (exact commands + outcome), screenshots or short screen recordings where relevant.
4. Known limitations, security/accessibility notes and untested assumptions.
5. NixOS installation, upgrade and rollback procedure.
6. Updated `docs/IMPLEMENTATION_STATUS.md`, acceptance IDs and ADRs where an architectural decision changed.
7. Next highest-priority blocking issue, not a speculative roadmap overload.

**First requested engineering deliverable:** Audit the current Codex implementation and NixOS VM, create a truthful status matrix against F-001 through F-021, then make a working native session with three real applications, Niri focus navigation and the first minimal Quickshell System surface. Define the smallest Flow API contract to query/focus spaces independent of the shell. No new mock apps and no new compositor selection debate.

---

## Appendix A — Conceptual Flow state data (illustrative, not frozen schema)

```json
{
  "version": 1,
  "userProfile": "local-user-opaque-id",
  "displays": [
    {
      "role": "main",
      "stableId": "hardware-or-profile-identity",
      "shortcutNumber": 0,
      "spaces": [
        {"id":"mail","label":"Mail","pinnedOrder":1,"members":[{"desktopId":"mail.desktop"}],"restore":"best-effort"},
        {"id":"studio-notes","label":"Studio + Notes","pinnedOrder":2,"members":[{"desktopId":"studio.desktop"},{"desktopId":"notes.desktop"}],"layout":{"landscape":"split-horizontal","portrait":"split-vertical","ratio":0.7},"restore":"best-effort"}
      ]
    },
    {
      "role": "external-1",
      "stableId": "dock-or-edid-identity",
      "shortcutNumber": 1,
      "spaces": [{"id":"terminal","label":"Terminal","pinnedOrder":1,"members":[{"desktopId":"terminal.desktop"}],"restore":"best-effort"}]
    }
  ]
}
```

**Notes:** Persistent IDs are per-user; secure device registration and provider permissions are separate. `desktopId` is not sufficient to identify a unique app instance. Don't store raw compositor IDs as long-term keys. Monitor serials can be absent/duplicated and require recovery logic. This is a conversation aid, not a production schema migration contract.

## Appendix B — What is real versus simulated (as of this version)

- HTML UX prototypes through **Flow v1.0** demonstrated visual ideas for spaces, gestures, island, notch, Desk, Overview, search, mock users, displays and contextual app menus.
- They **do not** provide secure authentication, Wayland IPC, actual display management, real file operations, real audio/Bluetooth/network, real Windows/macOS app runtime, or native compositor animation.
- Codex has been tasked with a real NixOS/Niri implementation, but **the actual repository/VM status has not been independently inspected for this document**. Every `IMPLEMENTED` label must be established from the code and runtime evidence.

---

*End of Flow Master Specification v1.1. This is a living anchor document. Preserve committed decisions; clarify hypotheses with ADRs and tests before converting them into code.*
