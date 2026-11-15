# Floe demo film v2 — Room to Think

**Format:** 78-second product-vision video, 16:9, 1280×720. **Language:** English. **Video status:** conceptual animation, not a native Niri/Quickshell recording. **Date:** 2026-11-15.

## What the film is for

Lead with the *human problem*, not Linux or a list of interface features.

> Your computer may have reached the end of its official OS upgrade path. That doesn't mean it has reached the end of its useful life.

Floe explores a calmer spatial way to work, built from NixOS, Niri and Quickshell, and a possible longer useful life for **compatible** older laptops/desktops. Neither hardware compatibility nor a performance advantage has yet been demonstrated by the video itself.

**Positioning:** “Room to think.”  
**Value to a person:** Less arranging windows, more focus on real work. Keep related applications together and return to where you left off.  
**Value to existing hardware:** A maintained open OS stack *may* provide a route forward after a vendor OS stops supporting a machine, if the hardware, GPU, firmware and peripherals work reliably and meet performance/security requirements.

Do **not** imply Floe is already a fully supported daily-driver OS, a generally compatible drop-in Windows/macOS replacement, a guaranteed performance upgrade, or an accessibility-certified tablet environment.

## Storyboard and visual proof boundary

| Time | Beat | Screen story | Evidence boundary |
|---|---|---|---|
| 0:00–0:07 | Still capable | Older laptop, “Still capable.” | Device illustrated, no specific model claimed compatible |
| 0:07–0:14 | The opportunity | “Support can end. Potential does not.” | Unsupported commercial OS ≠ inadequate hardware |
| 0:14–0:21 | Meet Floe | Floe mark, NixOS + Niri + Quickshell | Real chosen engineering stack |
| 0:21–0:30 | Work in Flow | Three app spaces, horizontal navigation | Niri navigation and multiple real apps demonstrated in VM; animation illustrative |
| 0:30–0:38 | Groups | Browser and Notes in one workspace destination | Experimental Groups implemented; orientation-aware reflow incomplete |
| 0:38–0:46 | Desk | Search, pinned spaces, return to work | Native Desk and pins partially tested |
| 0:46–0:54 | System Island | Small system plane with Quick Settings | Native controls partly tested; responsive top-chrome work pending |
| 0:54–1:02 | Two themes | Polar Night → Arctic Daylight | Design tokens and a Quickshell switch exist; all-device visual checks pending |
| 1:02–1:10 | Hardware longevity | Laptop with Floe and compatibility caveat | No old-device benchmark or validated compatibility list yet |
| 1:10–1:18 | Close | Floe. Room to think. | Clearly marked IN DEVELOPMENT / CONCEPT PREVIEW |

The conceptual master was rendered independently of the running NixOS guest. The actual MP4, subtitle SRT, and poster are delivered as conversation artifacts. They are not committed as binary assets to this repository. The rendered animation is **not** a substitute for native recording.

## Suggested short narration for a later voiced version

> A computer doesn't stop being useful just because its upgrade path ends.
>
> We believe computing can feel calmer—and capable hardware deserves a longer life.
>
> Meet Floe: an open Linux environment where applications have a place, related work stays together, and the Desk is always within reach.
>
> Built with NixOS, Niri and Quickshell, Floe is still in development. We're testing what it takes to make it a reliable daily driver—including on older hardware.
>
> Floe. Room to think.

Voice-over and soundtrack should be original or properly licensed; no copied commercial music. The conceptual video delivered here uses an original ambient synthesizer bed and no voice narration.

## Replace conceptual scenes with genuine footage

Record the actual NixOS/UTM guest using three distinct real applications and safe demo content. Maintain the same narrative but show observed capabilities only.

- **Spaces / work:** with Firefox, Kate and Files visible, navigate using `Super+Left` and `Super+Right`, then capture the actual Niri app textures.
- **Overview:** `Super+O` invokes Niri's *live* Overview. `Super+Tab` opens Floe's separate *text-based* Spaces switcher. Don't edit the switcher to look like live compositor previews.
- **Groups:** show an actual two-app Group and member focus/Group layout toggle only when verified in that build. Do not portray automatic portrait reflow as shipped.
- **Desk:** `Super+H`; show search, pins, and Escape/return to the same window and focus.
- **Island:** use `Super+I` or tested UI control to open Quick Settings; don't claim physical tablet touch was tested without it.
- **Themes:** switch Polar Night/Arctic Daylight in Desk Settings only once validated in that guest.
- **Continuity:** record Niri window IDs, focus and app states before/after to demonstrate that navigation does not destroy work.

Record the source commit, Niri/Quickshell version, hardware and output scale, and keep screenshots/screencasts free of private documents, usernames, file paths and notifications. A preview from a browser HTML mockup must be labeled conceptual.

### Older-device proof is a separate acceptance task

Before saying a hardware generation “runs Floe well,” choose **named devices** and record: CPU, RAM, storage, active GPU driver/renderer (not llvmpipe), display/scaling, Wi‑Fi/Bluetooth, audio/microphone/webcam, suspend/resume, lock/unlock, browser+office+editor workloads, memory/swap pressure, package updates, device firmware status, battery/thermals (if relevant) and repeatable measurements for startup/app switching. Also test secure maintenance/update flows and peripherals. Unsupported hardware cannot be made secure simply by installing a different desktop shell. Preserve vendor firmware/driver limitations honestly.

## Suggested publication formats

- **78s main film:** Why Floe exists + concept design + development disclaimer.
- **30s cutdown:** “Your computer may be older. Your ideas aren't.” → horizontal spaces → Desk → Floe.
- **2–3min engineering demo:** Unedited recordings on a documented test machine; links to repo, build instructions, supported hardware evidence and known limitations.

### Future content improvements

After the Island/app-menu collision audit is fixed and the native dark/light screenshot matrix passes, capture high-quality real footage and replace the simulated middle shots. Publish the real test hardware and an explicit early-access disclaimer in the description. Do not promise release dates or universal compatibility.
