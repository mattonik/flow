# Luma continuity and Wi-Fi notes

Research date: 2026-11-25. This is a comparison for Floe, not a change to the [Flow Master Specification](../Flow_MASTER_SPEC_v1.1.md) or evidence that Floe has these features. Luma's [20261124.1 nightly release notes](https://simplyluma.com/releases/1.0.0-nightly.20261124.1), published November 25, are the primary source for the new behavior below. They are Luma's claims; we have not run that nightly or independently verified its implementation.

## Cross-device continuation and Sync

Luma describes a prompt after unlocking another computer: the user can continue from the first device, reopening apps and arranging windows with the prior foreground app. It says existing work on the destination remains and the continued work opens on a new desktop. Window data is described as end-to-end encrypted and sent only to the user's own computers and phones, not TVs. Sync ships in the system but starts disabled and, Luma says, contacts no server until enabled. Its Viewer app separately reports reopening the same file on restart or continuation. These are nightly release claims; the notes do not specify the protocol, pairing, trust and revocation model, what metadata is encrypted, conflict behavior, or how arbitrary apps recover documents. [Source](https://simplyluma.com/releases/1.0.0-nightly.20261124.1).

**Comparison:** Floe already has an opt-in local session recovery setting and conservative recovery for specific pinned and Group layouts, with the limitations in [milestones](../milestones.md). Its recovery is not cross-device sync, and the [master specification §16](../Flow_MASTER_SPEC_v1.1.md) explicitly treats sync as long-term, independent of a Luma account or undocumented protocol. Reopening an application does not establish that its unsaved documents, browser tabs or internal state were restored.

**Ideas to evaluate, not implemented:**

- Make remote continuation explicit and opt-in; offer the source device and explain what will be opened before acting. Keep local startup recovery independently configurable.
- Preserve the destination's existing work; preview and restore into a separate workspace when supported. Model Groups and pins as semantic intent and adapt to the destination's displays, instead of promising identical pixels or blindly reordering existing windows.
- Exclude secrets and transient compositor window IDs; use portable app identity and an explicit app-provided document reference only where supported. Show missing apps, unavailable documents and partial restores honestly.
- Keep file sync, preferences and workspace handoff separate. Investigate established, licensed providers first; require device authorization and revocation, encryption/key handling, offline behavior and conflict handling before any implementation.

**Proposed next test:** With two disposable local Niri workspaces and isolated state, simulate an imported semantic snapshot containing an unavailable app and a two-app Group. Check that a preview reports the omission, existing windows and pins remain unchanged, no network request occurs, and no action happens until confirmation. This is a proposed design experiment, not current behavior or a substitute for a two-device security test.

**Status (2026-11-25):** run as a local guest smoke against Floe's step-1 snapshot import ([ADR-0012](ADR-0012-semantic-snapshot.md), `tests/niri_snapshot_import_smoke.py`), and passed; see *Flow Sync step 1* in [implementation status](../IMPLEMENTATION_STATUS.md). It is still not a two-device or security test.

## Wi-Fi band selection and roaming

Luma says its nightly prefers 5 or 6 GHz on multiband routers when signal quality is good, avoids changing bands during calls, streams or downloads on a good connection, avoids repeated attempts at access points that refused it, reduces scanning on good connections, and still moves quickly when a connection weakens. The release lists changed NetworkManager packages and a new wpa_supplicant package, but does not say which component implements each behavior or publish thresholds or a patch reference in the release note. This is a roaming/policy improvement as described, not proof of a generic 5 GHz driver fix. [Source](https://simplyluma.com/releases/1.0.0-nightly.20261124.1).

**Comparison:** Floe's [NixOS module](../../modules/flow.nix) opens GNOME Settings' Wi-Fi panel and includes NetworkManager tooling. [F-009](../IMPLEMENTATION_STATUS.md) records a no-adapter VM check and a simulated WPA2 network found and joined with the right passphrase, with a wrong passphrase refused. Physical-radio scanning, authentication, roaming and 5 GHz operation remain unverified. A UTM guest with virtual Ethernet cannot validate physical radio behavior.

**Recommendation:** Keep NetworkManager and its configured supplicant responsible for connectivity. First inspect Luma's actual patch and compare it with the exact NixOS-pinned upstream versions and configuration; do not assume a preference, fixed band, scan interval, traffic detector or NetworkManager setting reproduces Luma's results. Avoid hardcoded regulatory overrides, forced bands or global changes until device-specific evidence justifies them. If the patch is unavailable or tied to Fedora internals, treat direct reuse as unverified; use maintained upstream capabilities where they demonstrably meet the need.

**Proposed next test:** On named physical hardware with a real Wi-Fi adapter and lawful local regulatory settings, record adapter/driver/firmware, kernel, NetworkManager and supplicant versions, AP band/channel/security and signal strength. Compare 2.4/5 GHz selection on the same SSID, idle scanning, a sustained transfer or call, deliberate signal weakening, failed authentication/backoff, reconnect and suspend/resume. Measure band changes, interruption, packet loss and transfer performance against an unmodified baseline and a second client. Keep credentials and network identifiers out of published logs. A 6 GHz case needs capable hardware and an appropriate AP. Luma's own [device-porting guide](https://simplyluma.com/download/mobile/generic) also calls for sustained real traffic, not just an association indicator.

## Source availability and licensing

Luma's [community page](https://simplyluma.com/community) currently says its GitHub is temporarily unavailable. On 2026-11-25, `git ls-remote https://github.com/SimplyLuma/luma.git HEAD` returned repository not found; consequently neither the current Sync implementation nor the Wi-Fi changes could be inspected or linked to an exact commit. A search-engine-cached [repository overview](https://github.com/SimplyLuma/luma) describes original Luma code as Apache-2.0 and documentation as CC-BY-4.0, with third-party/derived material retaining its own terms. That cached overview is not a license determination for either change: inspect the exact source file, upstream patch ancestry, notices and applicable licenses if source becomes available before copying or distributing code. The Flow repository uses MIT; copying third-party code could add obligations.

## Evidence and priority

- **Announced by Luma:** the nightly behavior above. **Not independently verified:** operation, source, security and device coverage.
- **Verified in Floe source:** local recovery controls and the GNOME Wi-Fi settings launcher; historical guest evidence is limited as linked above.
- **Not yet a Floe feature:** cross-device sync or handoff, and a verified equivalent Wi-Fi policy.
- **Priority:** keep core Groups and local recovery first; treat continuation as a future architecture/design input and radio behavior as a physical-device investigation, not a new shell subsystem.
