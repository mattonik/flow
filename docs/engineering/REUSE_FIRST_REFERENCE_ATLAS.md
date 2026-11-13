# Flow — Reuse-First Reference Atlas & Implementation Guidance

**Version:** 1.0  
**Research reviewed:** 13 November 2026  
**Audience:** Codex and other Flow engineering agents  
**Proposed repository path:** `docs/engineering/REUSE_FIRST_REFERENCE_ATLAS.md`  
**Status:** Engineering guidance and research inventory. Not evidence that any candidate works in the Flow NixOS VM.

> **Flow must differentiate through its interaction model, not through reimplementing Bluetooth, file I/O, screenshots, network controls, notifications, virtual machines, or existing Linux desktop services.**
>
> Search for mature components, reuse or adapt them behind stable interfaces, and build only what is genuinely specific to Flow.

This document complements, rather than replaces, `FLOW_MASTER_SPEC.md` and `AGENTS.md`. If they disagree on product behavior, flag the conflict; do not silently reinterpret the user experience.

---

## 1. Immutable architecture and the reuse decision

**Committed platform:** NixOS + **Niri** Wayland compositor + **Quickshell/QML** system shell + a toolkit-independent **Flow Controller / Flow Platform API**. Niri is a product decision, not a placeholder for Hyprland. First-class touch/tablet interaction is a requirement, not a later theme variation.

- **NixOS** owns reproducible system packaging/configuration; ordinary Linux system services own their established responsibilities.
- **Niri** owns real application surfaces, focus, output mapping, and window/compositor behavior. Use its documented IPC and event stream before inventing polling or a parallel window model.
- **Flow Controller** owns semantic spaces, Groups, pinning, per-display anchors, restore intentions and application integration policy. It must hide Niri-specific details behind stable adapters.
- **Quickshell** renders trusted System UI (Island, Desk, Overview UI, notifications, menus, controls, status, lock where feasible). It is **not** the required UI toolkit for applications.
- **Flow Platform API** exposes versioned, toolkit-independent operations/events (D-Bus user-session APIs as a starting point; CLI and SDK adapters). GPUI, GTK, Qt, Electron and ordinary applications must all work without importing Quickshell. Sandboxed apps need appropriate portal/access controls.
- **Existing apps** work without Flow-awareness. Optional command providers, progress/activity providers and space-intent integrations enrich them; they must not be prerequisites for launching an app.

**Not authorized by this document:** switching to Hyprland; importing an entire foreign distribution; shipping an unreviewed plugin with privileged access; replacing NixOS with Arch; claiming a researched project is production-ready on Flow hardware; turning the prototype's mock apps into a custom app suite.

### Reuse preference, in order

1. **Use an existing supported service or app as-is** (e.g., BlueZ, NetworkManager, PipeWire, Dolphin).
2. **Configure/package it for NixOS** with an ordinary Nix module and a minimal Flow presentation or launch integration.
3. **Adapt an existing UI/service with a narrow patch or upstream contribution** if its assumptions are clearly isolated.
4. **Implement a small Flow-specific adapter/provider** around an upstream IPC, CLI or D-Bus contract.
5. **Create new software** only when no viable solution exists, the gap is intrinsic to Flow, and an ADR compares alternatives and maintenance costs.

The goal is **integrated reliability**, not a high number of reused repositories. A small proven component with graceful fallback is better than a flashy abandoned one.

### Candidate maturity labels

- **A — Existing Linux infrastructure:** established upstream software/service. Still requires packaging, integration and tests on Flow.
- **B — Real implementation to evaluate/port:** public implementation with relevant features, but NixOS/Niri/touch compatibility not proven.
- **C — Product/UX/architecture reference:** lessons are useful; copying implementation would be a poor or prohibited fit.
- **D — Long-term research only:** separate hardware or compatibility track; should not block the desktop MVP.

---

## 2. Researched projects: what to extract and what *not* to copy

### 2.1 Niri — committed compositor (**A**, adapt via documented interfaces)

**Source:** https://github.com/niri-wm/niri  
**IPC:** https://github.com/niri-wm/niri/blob/main/docs/wiki/IPC.md

Niri's horizontally scrollable columns align with Flow's horizontal spatial-navigation model. Its IPC includes a state/event stream; subscribe and reconcile real window, workspace, output and focus events instead of running a new query on every animation frame.

**For Flow:** use Niri window/output identities and actions as the source of *actual compositor state*, add semantic IDs for pinned spaces and Groups in the Controller. Prototype native actions before proposing compositor patches. Recognize that exact magnetic touch physics, cross-application gestures, pinned-semantic groups and true object-continuity animations are **not guaranteed** by having a scrolling compositor.

**Do not:** create a second window manager, pretend Quickshell can directly animate/reparent arbitrary client surfaces, or use fake screenshots to claim real Overview transitions work.

### 2.2 Quickshell — committed system UI (**A**, retain)

**Source:** https://quickshell.org/  
**Documentation and examples:** start from https://quickshell.org/ and follow its upstream links; pin the actual version used in the Nix flake.

Its QML/QtQuick layer surfaces and system-service integrations make it the most practical choice for Flow's System. Reuse the framework's service wrappers and its Qt ecosystem. Prefer one reliable long-running shell process with shared services, rather than independently launched QML processes for every panel.

**For Flow:** Island, notifications/quick settings, Desk/search shell, overlay surfaces, system status, trusted lock/auth UI, adaptable desktop/tablet presentations. Implement public app integration in a separate controller/API, not by exposing arbitrary Quickshell objects.

### 2.3 Omarchy / Quattro — **the leading integration reference** (**B/C**)

**Project:** https://omarchy.org/  
**Code:** https://github.com/omacom/omarchy  
**Shell architecture:** https://github.com/omacom/omarchy/blob/quattro/docs/omarchy-shell.md  
**First-party plugin inventory:** https://github.com/omacom/omarchy/blob/quattro/shell/plugins/README.md  
**Engineering guidance:** https://github.com/omacom/omarchy/blob/quattro/agents/skills/shell-dev.md  
**Manual:** https://github.com/omacom/omarchy/tree/quattro/manual

Omarchy demonstrates how curated Linux components, native configuration, small launcher scripts, a cohesive theme, CLI access and polished feedback become a complete desktop product. Its Quattro shell is one long-running Quickshell process hosting lazy panels/overlays, bar widgets, services, notifications, polkit/lock facilities, plus plugin manifests and IPC.

**Directly useful patterns:**

- **One shell host and shared services.** Panels can be summoned quickly without launching a new shell.
- **Small modular plugin types:** `panel`, `overlay`, `menu`, `service`, `bar-widget`, `bar`. Flow's UI may use the same *pattern*, but need not copy Omarchy's manifest verbatim.
- **Service singletons plus event-driven state:** avoid one repeating subprocess per widget per monitor.
- **IPC and shell health checks:** an external command can call into the already-running shell; failure modes are explicit.
- **Theme tokens:** central colors, typography, spacing, focus treatment, accessibility/reduced motion; propagate to related apps only where safe.
- **Complete utility workflows:** screenshot -> selection -> annotate/OCR -> clipboard/save -> notification, or network panel -> change connection -> authoritative state -> feedback.
- **Polished failure handling:** authentication, permissions, install/enable checks, screenshots of actual UI, regression guidance and reversible update flows.

**Critical limits:** Omarchy uses **Arch/Hyprland**; Flow uses **NixOS/Niri**. A script calling `hyprctl`, the Arch package manager, or an Omarchy-specific system path needs replacement with Niri IPC, a shared service, or a Nix module. Do not vendor its entire shell blindly. Omarchy's third-party QML plugins share process privileges; limited facades are not a security sandbox. External plugin installation, network data and dynamic shell commands require review and trust boundaries.

**Source-grounded caution:** Omarchy's own shell documentation explicitly warns that installed plugins run unsandboxed in-process; inspect licensing **per file/dependency**, not just top-level MIT.

### 2.4 Pond / Butterfly — closest Niri product comparison (**B/C**)

**Overview:** https://www.butterfly.so/pond  
**Engineering:** https://www.butterfly.so/pond/engineering  
**Design:** https://www.butterfly.so/pond/design

Pond is an Arch-based Linux distribution that explicitly uses **Niri** and combines it with Nix integration, a curated package experience, installation/recovery work and hardware configuration. It is especially relevant because it pursues polished whole-desktop ergonomics without Hyprland. Its published memory and performance figures are project claims, not Flow test results.

**For Flow:** analyze how a Niri desktop boots reliably, how the ISO/installer, first-run, package selection, input defaults, theming, update recovery and device support are presented. Review touchscreen use and accessibility separately; do not assume Pond implements Flow's Magnetic Flow or adaptive Group semantics. **Borrow design patterns and integration lessons**, not its Arch-centric distro machinery.

### 2.5 Flea — strong file-manager candidate (**B**, integration spike)

**Code/README:** https://github.com/thisisgm/flea  
**Development notes:** https://github.com/thisisgm/flea/blob/main/AGENTS.md

Flea provides real file-manager features (list/grid/column/dual-pane views, Quick Look, batch file operations, tags, search, network filesystems, device handling, file chooser integration). Its **Shelf** is particularly compatible with Flow's Desk: collect files, keep selected items available, drag them into other apps without breaking current Flow navigation. The active 0.4.x GUI moved to **GPUI** from earlier QML; old source references can be misleading.

**For Flow:** attempt a clean NixOS build/launch under Niri **without installing Omarchy**; identify hard dependencies on Omarchy CLI, status bar, theming, default handlers and portal integration. Verify desktop-file IDs, mime associations, Open/Save dialogs, network drives, thumbnails, drag-and-drop across applications and display scale. Test touch selection and tablet portrait behavior. Then decide **adopt/patch upstream/fork narrowly/reject**.

**Fallback:** install and integrate Dolphin or another proven Linux file manager immediately; never hold basic file access hostage to Flea porting. Flea's published speed measurements belong to its test environment and do not prove its performance on Flow hardware.

**Do not:** build a competing file manager in early milestones; promise Flea's file chooser will work across every sandbox before portal testing.

### 2.6 GPUI — valid application framework, not a shell replacement (**B**)

**Source:** https://github.com/zed-industries/zed/tree/main/crates/gpui  
**Working application candidate:** Flea (above).

A Rust GPU UI toolkit suitable for highly interactive, performance-sensitive **apps**. Flow can host GPUI alongside GTK, Qt and Electron. The **Flow API must be independent of GUI toolkit**; a GPUI Files app should receive the same events/actions as a Qt app.

**Decision:** keep the system shell on Quickshell. Evaluate GPUI for Flea, search-heavy utilities and future custom apps if there is a concrete need. Pre-1.0 API change risk, Wayland layer shell, accessibility, touch, IME and ARM64 behavior should be tested rather than assumed. Native GPUI touch gestures within a file manager cannot move unrelated Niri-managed client windows.

### 2.7 Omarchy-Nix — historical porting pattern only (**C**)

**Source:** https://github.com/henrysipp/omarchy-nix

Community NixOS/Home Manager reinterpretation of Omarchy-style setup. Its author says active development has stopped, and it is **Hyprland-oriented**. Read for packaging, flake and Home Manager patterns; do not treat it as an updated Quattro port or a base for Flow.

### 2.8 Wild OS — onboarding and Windows-app experience (**C**, verify source before adoption)

**Website:** https://wildos.io/

Wild OS markets a low-friction Linux desktop with ready-to-use apps and a **Windows mode** in which genuine Windows runs in a VM and selected apps appear as normal desktop windows. Its website offers a browser demo and claims integrated install/licensing paths. Verify actual released code, maintainers and licensing before basing Flow on any feature; a marketing demo is not proof of NixOS/Niri compatibility.

**For Flow:** study the user journey: choose an app -> explain requirements -> install/configure Windows VM -> launch via ordinary app affordance -> shared files/clipboard -> suspend/stop without wasting resources. Use mature VM/RDP infrastructure where supported; don't write a Windows emulator or promise all Windows apps work.

### 2.9 Shoji (macOS) — adaptive layouts and saved setups (**C**, UX only)

**Site:** https://shojimac.com/

Shoji is a commercial macOS automatic window arranger. Useful interaction ideas: saved sets of apps and relative positions, layout adaptation to ultrawide vs portrait screens, stacking/reflow as windows open/close, quick focus, shortcut customization/conflict feedback.

**For Flow:** treat as a behavioral reference for Groups and per-display arrangements, not as reusable Linux source. Recreate only *general interaction principles* implemented with Niri/Flow semantics; do not copy proprietary code, art or branding.

### 2.10 Aurora Silicon / Asahi — Apple Silicon device enablement (**B/D**, independent hardware track)

**Site:** https://aurorasilicon.org/  
**Device matrix:** https://aurorasilicon.org/devices/  
**Asahi:** https://asahilinux.org/

Focus is the **hardware/kernel/graphics boot path** on Apple Silicon; website publishes a per-device support matrix and mentions Omarchy Mac and NixOS installer options. Read the matrix for the **exact chip and model** before planning deployment. A working M1/M2/M3 device or a generic ARM64 build does **not** demonstrate usable native Flow on a particular M4 Pro. **Current research update (13 Nov 2026):** Aurora's device matrix specifically reports substantial M4 Pro progress, including boot, GPU acceleration and Vulkan on named MacBook Pro models, but lists important model-dependent gaps such as speakers, dock/external-display handling and suspend. Do not rely on older generic claims that M4 support is uniformly absent, or newer generic claims that it is fully ready. Verify the exact 14-inch/16-inch machine and released installer/kernel stack.

**For Flow:** keep NixOS ARM64 VM development separate from native Apple Silicon bring-up. Use Aurora/Asahi's upstream drivers/toolchains where supported rather than attempting new hardware enablement inside Flow. This also applies to the independent A12X iPad Linux research—Wayland cannot replace absent boot, GPU or touch drivers.

### 2.11 LiberNT — separate NT operating system (**D**, compatibility research only)

**Organization:** https://github.com/LiberNT  
**Source:** https://github.com/LiberNT/LiberNT

LiberNT is a Windows NT-compatible operating-system effort descended from ReactOS, targeting modern NT/Windows compatibility and several architectures. It is **not** a Linux desktop environment, a drop-in Windows compatibility layer for NixOS, or a candidate compositor/shell for Flow. Its kernel, driver, testing and clean-room compatibility strategy may be intellectually useful when studying compatibility, but **not** an MVP dependency or codebase to graft into Linux.

### 2.12 Luma, device convergence and adjacent references (**C/D**)

**Luma:** https://simplyluma.com/  
**Other previously examined references:** https://www.butterfly.so/pond · https://aurorasilicon.org/ · https://libernt.com/ · https://shojimac.com/ · https://wildos.io/

Luma is useful as a product-direction comparison for a tablet-friendly Linux experience and continuity aspirations. Its delivery model, hardware support, system architecture and sync capabilities must be independently verified before making technical assumptions. None of these projects changes Flow's decision to use Niri/Quickshell/NixOS.

---

## 3. Omarchy utility atlas: what Flow should reuse instead of inventing

**Treat Omarchy's plugins and command recipes as implementations to examine, not installation instructions for NixOS.** The following map pairs an experience to a proven Linux responsibility and to a proposed Flow treatment. “Study/port” still requires successful actual tests.

| User capability | Omarchy example / source location | Preferred foundation for Flow | Flow-specific work | Priority |
|---|---|---|---|---|
| Audio output, volume, devices | `omarchy.audio`, shell panel | PipeWire + WirePlumber; Quickshell/PipeWire integrations | Minimal audio panel + activity/status | P0 |
| Bluetooth and AirPods basics | `omarchy.bluetooth` | BlueZ, PipeWire, Bluetooth system services | Discover, pair, connect, device profiles, actionable error states | P0 |
| Wi-Fi / Ethernet / VPN | `omarchy.network`; networking setup | NetworkManager / D-Bus; VPN integrations | Quick settings, identity, connection and troubleshooting | P0 |
| Battery / charging / power profiles | `omarchy.battery`, `omarchy.power` | UPower, power-profiles-daemon or platform support | Status and user-facing power choices | P0 |
| Media controls | `omarchy.media` | MPRIS + PipeWire, player APIs | Island live activity, playback controls | P1 |
| Notifications | `omarchy.notifications` | freedesktop notifications, Quickshell service | History, focus/silence controls, privacy | P0 |
| On-screen display | `omarchy.osd` | Shell surfaces + service events | Volume/brightness feedback without focus theft | P1 |
| Clipboard manager | `omarchy.clipboard` | Wayland clipboard protocols; `wl-clipboard`, `cliphist` or other maintained backend | History UI, privacy rules, keyboard/touch | P1 |
| Emoji picker | `omarchy.emojis` | Existing Unicode picker/index | Summoned compact picker, search, copy/insert | P2 |
| Screenshot + annotate + OCR | **Omasnap**, Capture manual | Omasnap if Niri-compatible; else established Wayland capture + annotation/OCR | One integrated capture workflow; no bespoke encoder | P1 |
| Screen recording | `gpu-screen-recorder` path in Omarchy | PipeWire/screencast portal + compatible recorder | Picker/status/stop, recording permissions | P1 |
| Color picker / QR / text extraction | Omarchy capture utilities | Existing OCR, image sampling, QR tools | Unified command/menu exposure | P2 |
| Fast launcher / commands | `omarchy.menu` + menu JSONC | `.desktop` discovery, search backend and Quickshell palette | Flow-wide results incl. spaces and allowed app commands | P0/P1 |
| App switcher/dock | `omarchy.dock`, bar | Niri app/window IPC | **Do not import permanent dock**; adopt lazy visibility and efficient app selection only | P1 |
| Displays/monitor control | `omarchy.monitor` | Niri outputs/IPC; kernel/DRM data; optional display-profile service | Saved per-display Flow anchors, connect/reconnect, portrait | P0/P1 |
| Lock screen | `omarchy.lock` | Wayland session lock, PAM, logind | Secure per-user lock UI with fallback | P0 |
| Elevated permissions | `omarchy.polkit` | Polkit agent via Quickshell | Branded secure dialog, no custom auth implementation | P0 |
| Idle, night light and suspend | `omarchy.idle`, `omarchy.nightlight` | logind / idle tools / supported color-temperature service | Unified idle policies, tablet/desktop ergonomics | P1 |
| Wallpaper/image selector | `omarchy.image-picker` | QtQuick image display + existing selection/index tools | Simple settings, avoid elaborate wallpaper engine | P2 |
| Reminders | `omarchy.reminders` | Existing reminder/calendar apps | Optional panel/provider—not a built-in Flow app priority | P3 |
| Tailscale | `omarchy.tailscale` | Tailscale daemon/CLI | Status/connection controls if installed | P2 |
| App/task/agent status | `omarchy.agents`, status widgets | Application activity providers via Flow API | Island progress/ongoing activities with ownership checks | P2 |
| Windows VM | `omarchy windows vm`, Dockur/FreeRDP | KVM/QEMU or supported VM manager; FreeRDP; file sharing | Guided install, launch, lifecycle and clear isolation | P3 |
| File manager / Quick Look / Shelf | **Flea** (not guaranteed default Omarchy install) | Flea port candidate, Dolphin fallback | Integration with Desk and Flow Groups | P1 |
| Apps/package choices | Omarchy curated defaults/installer | NixOS/Nixpkgs; Flatpak; existing software center | Curated install/discover UI, not custom package manager | P1/P2 |
| Sharing to nearby devices | Omarchy sends via **LocalSend** | LocalSend (standalone app / integration) | Share action in Files/Island | P2 |
| Themes/typography/shell configuration | `shell.toml`, theme tokens | QtQuick/Quickshell tokens + Nix/Home Manager | Flow visual system, reduced motion, consistent preferences | P1 |
| First-run, updates, rollback | Omarchy setup/update; Pond snapshots | NixOS generations, recovery/rollback, transactional user config | Clear first-run, update status, repair flow | P0/P1 |

**Primary Omarchy sources:**
- First-party plugin catalog: https://github.com/omacom/omarchy/blob/quattro/shell/plugins/README.md
- Quickshell architecture and plugin security: https://github.com/omacom/omarchy/blob/quattro/docs/omarchy-shell.md
- Screenshot, recording, OCR, colors, transcode, LocalSend: https://github.com/omacom/omarchy/blob/quattro/manual/12-screenshots-recording.md
- Windows VM lifecycle and security: https://github.com/omacom/omarchy/blob/quattro/manual/28-windows-vm.md
- Documented default hotkeys: https://github.com/omacom/omarchy/blob/quattro/manual/07-hotkeys.md
- Visual QA approach: https://github.com/omacom/omarchy/blob/quattro/agents/skills/visual-verification.md

### Why a copy-paste Omarchy port is not the answer

1. Its **Hyprland-specific window/workspace APIs** differ from Niri's supported commands/events.
2. Its **Arch packages, package names, update scripts and filesystem assumptions** are not NixOS derivations/modules.
3. Its **bar-first interactions** conflict with Flow's minimal floating Island and fullscreen-first Desk.
4. Its plugin architecture has **in-process trust and security concerns** unsuitable for unrestricted third-party app integrations.
5. Its UX defaults are largely keyboard/desktop-oriented; **tablet touch/rotation/IME** need independent validation.

What to borrow instead: *the orchestration layer, error handling, keyboard grammar discipline, concrete utility workflows, performance practices, first-party QML components when legally and technically suitable, and acceptance-test discipline*.

---

## 4. Existing Linux infrastructure we should not rewrite

These are the *first candidates to evaluate* for service responsibilities. Versions, NixOS module options, platform-specific dependencies and package licensing must be verified during implementation.

| Responsibility | Default direction | Adaptation / caveat |
|---|---|---|
| Network and VPN connections | NetworkManager, nmcli/D-Bus, relevant VPN plugins | Flow Quick Settings is a client, not a second network stack |
| Bluetooth | BlueZ / D-Bus | AirPods pairing/audio profiles via PipeWire; Apple auto-switch isn't assumed |
| Audio/video routing | PipeWire + WirePlumber | Screen sharing/calls must be exercised with browser/portal tests |
| Power and batteries | UPower + logind + relevant power profile daemon | Some hardware lacks full readings/profiles |
| Auth, lock, privilege escalation | PAM, Polkit, logind, supported Wayland session lock | Do not create password stores or an ad hoc lock protocol |
| Notifications | freedesktop notifications + Quickshell | Maintain per-user privacy and history; test DND and crash recovery |
| Application discovery | `.desktop` / XDG MIME / Flatpak exports | Search builds an index/provider layer, not an app store framework |
| File selection & sandbox access | XDG desktop portals + stable file chooser backend | Flea's advertised chooser must be verified; preserve working fallback |
| Filesystems and mounts | udisks2, GVfs/KIO, standard Linux filesystems | File manager remains a client, not a new filesystem layer |
| Printing | CUPS / IPP Everywhere | Settings can provide a friendly client on top |
| Screensharing and screen capture | PipeWire, XDG screencast/screenshot portal, Wayland compositing APIs | Niri/portal compatibility is a release test, not an assumption |
| Clipboard | Wayland clipboard + maintained history tool | Treat clipboard contents as sensitive; do not log by default |
| Cross-device file sending | LocalSend | AirDrop-compatible Apple protocol support is not presumed |
| Device/notification connectivity | KDE Connect (where useful) | Optional bridge; avoid duplicating sync logic |
| File synchronization | Syncthing or Nextcloud | Flow Sync coordinates intent/settings separately; no new storage engine |
| Linux application install | NixOS/Nixpkgs, Flatpak | Nix configuration owns system, no manual pacman steps |
| Legacy X11 | XWayland | Maintain support without forcing old apps into new framework |
| Windows desktop/apps | Wine/Bottles; KVM/RDP VM where appropriate | Publish compatibility tiers; no guarantee for every app |
| DisplayLink | upstream EVDI/driver and NixOS support where licensed | Test named dock/kernel/GPU; build manager UI only on working backend |
| OS updates and rollback | NixOS generations / bootloader recovery | Never put recovery in experimental Quickshell UI exclusively |

**Guideline:** shell interfaces can be distinctively Flow; the backend protocols should remain established Linux standards wherever possible.

---

## 5. Flow's *actual* unique software — build this, not everything

This is where new code is justified because the behavior is specific to Flow:

1. **Semantic Flow model:** stable `spaceId` and `groupId` distinct from raw Niri workspace/window IDs; ordered pinned spaces at the beginning of each display; dynamic spaces afterward.
2. **Flow Controller and versioned Platform API:** window/group intent, per-user persistence, display preferences, action requests, events and capability-checked app providers.
3. **Magnetic navigation semantics:** Niri-backed navigation, strong tablet snapping vs gentle desktop trackpad alignment, consistent focus and gesture arbitration. Implement compositor patches only when verified necessary.
4. **Desk ↔ Overview ↔ Work relationship:** a coherent spatial model with native surfaces and real focus/state restoration, not unrelated fullscreen webpages.
5. **Group lifecycle/adaptive presentation:** one navigation target can contain multiple apps; landscape split, portrait stack, move to another display without destroying membership.
6. **System Island and context-sensitive shell:** quiet, adaptive UI that respects camera cutouts, uses legitimate service information, and doesn't reserve a big permanent bar.
7. **Per-display pinned-space navigation:** `Super+1..9` main; `Super+Alt`, display digit then space digit external navigation, with stable display identities and focus restoration.
8. **Group-aware application menu context:** app commands follow keyboard focus, Group arrangement actions are separate; unsupported apps keep native menus. Optional `Flow.Applications` command providers.
9. **Flow Sync's semantic workspace metadata layer:** optional, later; file transfer/data sync should reuse established tools.
10. **App/platform integration UX:** permissions, shell events, search providers, progress/activity providers, fallback paths that make normal third-party apps feel like good Flow citizens.

Implement these over real apps and services. Do **not** equate a Quickshell visual mock with completed functionality.

---

## 6. Proposed subsystem boundary (not finalized public API)

```text
GTK / Qt / GPUI / Electron / Flatpak / ordinary Linux apps
     | (optional, versioned Flow D-Bus / SDK / CLI; XDG portals)
     v
+------------------------------------------+
| Flow Platform API (user session)          |
| Spaces | Groups | App commands | Search   |
| Displays | Events | Activities/Progress   |
+----------------------+-------------------+
                       |
             +---------v-----------+
             | Flow Controller     |
             | semantic state      |
             | policies & restore  |
             +----+------------+---+
                  |            |
             Niri IPC      normal Linux services
                  |            |
       Niri compositor   NetworkManager/BlueZ/etc.
                  ^            ^
                  |            |
             +----+------------+---+
             | Quickshell System   |
             | Island/Desk/menus   |
             +--------------------+
```

**API design constraints:**

- Distinguish **read-only discovery** from **state-changing requests**; arbitrary apps cannot move other users' windows or enumerate private surfaces.
- Use session bus ownership, caller identity validation, scoped capability model, and appropriate portal mediation for sandboxed callers.
- Define explicit versioning, stable IDs, typed errors and event subscriptions. Don't publish internal Niri handles as permanent public IDs.
- Provide graceful behavior when Controller, shell or a provider crashes; no permanent loss of running Linux apps.
- Keep Quickshell UI plugins **trusted** and distinct from **untrusted third-party application providers**.
- The API is defined by user experiences and security guarantees, not by whichever toolkit is currently easiest to call.

Suggested proof-of-concept CLI (conceptual; not yet implemented):

```text
flow spaces list
flow spaces focus <space-id>
flow spaces pin <space-id>
flow groups members <group-id>
flow overview show
flow events watch
```

Actual executable names/method signatures must come from a reviewed interface definition and tests.

---

## 7. Codex: required decision process for any new feature

### Reuse-first discovery protocol

Before writing a new subsystem, produce a compact **Implementation Decision Record**:

1. **User job to be done:** state the real Flow journey, device/input and failure case.
2. **Native Linux options:** identify OS services, maintained apps, CLI tools, Nix packages, portals and upstream references.
3. **Relevant examples:** specifically check Omarchy's shell/plugins and Pond's Niri experience when relevant.
4. **Feasibility:** Does the candidate work on supported NixOS architecture and Niri? What actual version/commit was checked? Is hardware required? Is first-class touch feasible?
5. **Integration scope:** launch as-is vs package vs adapter vs patch. What remains Flow-specific? What is omitted?
6. **Risks:** licensing, maintenance, security, Sandboxed/Flatpak portal access, privacy, focus, battery/process count, output scaling, translation/accessibility.
7. **Fallback:** what ordinary reliable app/service works if this candidate fails?
8. **Evidence:** actual build/test logs and screenshot/short screen recording for visible changes. Marketing pages or examples alone are not validation.
9. **Decision:** `ADOPT`, `WRAP`, `PORT`, `PROTOTYPE`, `RESEARCH ONLY`, or `REJECT`, with reason and re-evaluation trigger.

Use a small table for each implementation record:

| Option | License / upstream | NixOS | Niri | ARM64 | Touch | Security | Decision |
|---|---|---|---|---|---|---|---|
| Candidate A | Verify | Tested/unknown | Tested/unknown | Tested/unknown | Tested/unknown | Audited/unknown | TBD |
| Fallback B | Verify | Tested/unknown | Tested/unknown | Tested/unknown | Tested/unknown | Audited/unknown | TBD |

**Avoid unverified claims** such as “tested on NixOS” when no VM or machine was actually run. Maintain `docs/IMPLEMENTATION_STATUS.md` with a separate status for design prototype, built, launched, manually verified and hardware verified.

### Prioritized research/porting work

**P0 — Foundation / real working desktop**

- Audit existing repository against this atlas. **Do not overwrite existing code or AGENTS.md without merging.**
- Establish NixOS + Niri + Quickshell system session with browser, terminal and file manager; prove clean restart and fallback login.
- Establish event-driven Niri IPC adapter, semantic IDs and minimal Flow API; verify live focus/output events and failure handling.
- Hook Quickshell to real network/audio/Bluetooth/power/notifications; do not create custom backend daemons for these.
- Integrate secure lock and policy agent using established PAM/Polkit/session-lock methods.
- Run basic global shortcut and app focus tests in the real session.

**P1 — Reuse spikes and product differentiators**

- **Flea feasibility:** package isolated 0.4.x GPUI version on NixOS + Niri; test Open/Save portals, network shares, Quick Look, shelf and touch; keep Dolphin working as fallback.
- **Omarchy utility study:** choose one end-to-end service path (audio/network/notifications) and one capture workflow; separate OS service and UI/client layers; implement Flow-specific presentation only.
- **Group focus/menu path:** real Group of two windows, focus tracking, menu context and independent Group actions; native menus unchanged when unsupported.
- **Display persistence:** discover/reconnect physical displays with stable mapping and maintain pinned spaces; validate disconnect with no lost app windows.
- **Tablet interaction feasibility:** real touch/rotation/IME test hardware; document Niri gaps with upstream issue/patch candidates instead of UI simulations.

**P2+ — After proof of reliable foundation**

- Island activities API, advanced command providers, clipboard history, emoji picker and capture/OCR UI polish.
- Flow Sync metadata and LocalSend/KDE Connect surfaces; only after per-user state is reliable.
- Windows VM guided setup/launch, per-hardware DisplayLink manager, alternative app frameworks and native Apple Silicon/iPad tracks.

### Mandatory acceptance evidence

For each adapted component:

- Real app or service launches in Niri; fallback still works.
- No incorrect focus theft; Group, Desk and Overview semantics remain stable.
- Shell restart doesn't kill ordinary applications or lose saved intent.
- On at least one desktop configuration, the feature works from keyboard/mouse; touch path is either tested or **explicitly marked pending**.
- Normal multiuser permissions and sandbox limitations respected.
- Tests actually run are written down; log the precise environment, commit and Nix flake lock state.
- Rollback or disable is documented before merging a privileged/background service.

---

## 8. Source, licensing and security rules

- Use upstream documentation and the repository's pinned commit/tag as primary evidence; a fast-changing `main` branch may diverge after this research date.
- **Before code reuse**, inspect repo-wide license **and per-file headers**, third-party license manifests, linked assets, packaging scripts and trademark restrictions. Compatibility with Flow's chosen licensing requires review.
- Prefer configuring an existing package or upstream contribution before maintaining a large long-lived fork.
- Do not run remote `curl | bash`, Arch postinstall scripts, or privileged launchers simply because an example from Omarchy uses them.
- Keep guest VMs, screen capture, clipboard history and remote-transfer tools permission-bound. Never silently mount the entire home directory into a VM or persist private clipboard items without consent.
- Don't grant third-party QML plugins access to authentication services, unrestricted shell commands, full file system or the full live shell object tree. In-process plugin facades cannot be treated as genuine sandboxes.
- Sanitize untrusted text in QML; use plain-text rendering for names/notifications/search results unless validated.
- Keep hardware support claims **per exact device model**; ARM64 CPU support does not establish GPU/touch/boot readiness.

---

## 9. Fast source index (verification map)

| Topic | Primary source |
|---|---|
| Niri compositor | https://github.com/niri-wm/niri |
| Niri IPC and event stream | https://github.com/niri-wm/niri/blob/main/docs/wiki/IPC.md |
| Quickshell | https://quickshell.org/ |
| Omarchy code | https://github.com/omacom/omarchy |
| Omarchy Quattro plugin/IPC contract | https://github.com/omacom/omarchy/blob/quattro/docs/omarchy-shell.md |
| Omarchy first-party plugin inventory | https://github.com/omacom/omarchy/blob/quattro/shell/plugins/README.md |
| Omarchy shell development | https://github.com/omacom/omarchy/blob/quattro/agents/skills/shell-dev.md |
| Omarchy screenshot/recording/share | https://github.com/omacom/omarchy/blob/quattro/manual/12-screenshots-recording.md |
| Omarchy Windows VM | https://github.com/omacom/omarchy/blob/quattro/manual/28-windows-vm.md |
| Pond engineering/Niri | https://www.butterfly.so/pond/engineering |
| Flea file manager | https://github.com/thisisgm/flea |
| Flea agent/developer notes | https://github.com/thisisgm/flea/blob/main/AGENTS.md |
| GPUI | https://github.com/zed-industries/zed/tree/main/crates/gpui |
| Omarchy-Nix historical port | https://github.com/henrysipp/omarchy-nix |
| Wild OS | https://wildos.io/ |
| Shoji | https://shojimac.com/ |
| Aurora Silicon hardware support | https://aurorasilicon.org/devices/ |
| Asahi Linux | https://asahilinux.org/ |
| LiberNT / ReactOS descendant | https://github.com/LiberNT/LiberNT |
| Luma | https://simplyluma.com/ |
| LocalSend | https://localsend.org/ |
| KDE Connect | https://kdeconnect.kde.org/ |
| Syncthing | https://syncthing.net/ |
| XDG portals | https://flatpak.github.io/xdg-desktop-portal/ |

---

## 10. Hand-off directive (copy into an engineering task)

> Read `FLOW_MASTER_SPEC.md` (product direction), this `REUSE_FIRST_REFERENCE_ATLAS.md` (integration strategy), and the repository's `AGENTS.md` before implementing the next feature. Audit the existing code first. For every planned system capability, identify whether an established Linux service, Quickshell integration, Niri IPC primitive, existing app, or Omarchy/Pond reference already provides the underlying functionality. Build Flow's distinctive spatial model and presentation; don't replicate proven backends. Submit a small adoption/porting/adapter decision with exact source, license, version, actual test evidence, NixOS/Niri/touch gaps, and fallback. Keep Niri and Quickshell; expose stable, versioned toolkit-independent Flow interfaces. Show a real working vertical slice before expanding scope.

**Definition of success:** Flow behaves unlike any other desktop **because of the experience**, while its everyday capabilities work reliably **because they leverage proven Linux software**.

---

*Research and engineering guidance as of 13 November 2026. Links and capabilities may change; agents must verify code and hardware at the exact version chosen for implementation.*
