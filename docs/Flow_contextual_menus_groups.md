# Flow — Contextual Application Menus in Groups

**Type:** Product & Interaction Specification  
**Status:** Approved UX direction; native implementation pending  
**Priority:** P1 — Core interaction architecture  
**Applies to:** NixOS + Niri + Quickshell  
**Related systems:** Flow Groups, Flow Controller, Flow Platform API, application menus, universal search

## 1. Core principle

**Navigation belongs to the Group. Commands belong to the focused application. Arrangement belongs to the Group.**

In Flow, a Group represents a single spatial navigation destination containing multiple independent application windows.

Although users navigate to a Group as one space, every application retains its own focus, menu commands, keyboard shortcuts and lifecycle.

Flow must never combine unrelated application menus into one ambiguous menu.

## 2. Expected desktop behavior

Consider a Group containing two applications:

- Studio — design application
- Notes — note-taking application

The Group is labeled **Design + Notes**.

When Studio has keyboard focus, the global application menu shows:

`Studio | File | Edit | View | Object | Help`

When the user activates Notes, the global menu changes to:

`Notes | File | Edit | Format | View | Help`

The Group itself remains unchanged.

### Focus rules

1. The global application menu represents the currently focused application window.
2. Switching focus between applications updates the menu.
3. Hovering over a window must not change the menu by itself.
4. Keyboard focus changes must produce the same result as pointer-initiated focus changes.
5. Opening an application menu freezes the displayed menu context until that interaction ends.
6. Quickshell receiving input focus must not cause the underlying application context to disappear unexpectedly.
7. Returning to a Group restores the previously focused application when possible.
8. Closing or minimizing one application must not destroy the Group or close its other members.

Use actual Niri focus and window-state events as the source of truth. Do not infer application focus solely from pointer location.

## 3. Group actions are separate

Application menus contain commands specific to the active application.

Group actions affect the arrangement or identity of the Group.

Examples of Group actions:

- Rename Group
- Pin or unpin Group
- Change layout
- Resize application regions
- Reorder applications
- Add an application
- Remove an application
- Move Group to another display
- Split or dissolve Group

These commands should be presented through a dedicated Group control rather than mixed into the focused application's File/Edit menu.

**Important:** An application's Close command closes its own window, not the entire Group.

Destructive Group operations should provide clear confirmation or an undo mechanism where appropriate.

## 4. Desktop interface

On desktop and laptop displays, Flow may expose a compact global application menu along the upper-left edge.

The menu must:

- Respect the physical camera notch or cutout.
- Remain visually separate from the central System Island.
- Use the currently focused application as its context.
- Avoid permanently covering substantial application content.
- Hide or simplify itself when no application is focused.
- Provide access to Group actions when the active application belongs to a Group.
- Preserve application-native menus when Flow cannot safely replace them.

A Group selector may show the Group's members and let the user activate a particular application.

This is an additional navigation affordance, not a substitute for normal focus behavior.

## 5. Tablet and touch interface

Tablet layouts should not permanently display a traditional desktop menu bar.

Instead:

- Each application region can expose a compact contextual control containing its application icon or name.
- Tapping the control opens a touch-friendly application command menu.
- The central System Island remains dedicated to system-level interactions.
- Group arrangement controls are accessible through a separate Group interface.
- Portrait and landscape layouts preserve the same application/menu relationships.
- Touch targets should generally be at least approximately 44 × 44 logical pixels, with accessible alternatives.

When a Group changes from side-by-side to vertically stacked, its menus and focus behavior remain logically identical.

**Input modality must not change the identity or membership of the Group.**

## 6. Application compatibility

Flow must support applications written using GTK, Qt, GPUI, Electron and other frameworks.

However, not every Linux application exposes its menu commands through a common API.

### Compatibility tiers

**Tier A — Flow-aware applications**

Applications optionally publish structured commands through the versioned Flow Platform API.

Potential capabilities include:

- Application identity
- Menu categories
- Command IDs and labels
- Enabled/disabled states
- Keyboard shortcut metadata
- Context-sensitive commands
- Secure command invocation

These commands may also be discoverable through universal search.

**Tier B — Existing menu-export mechanisms**

Where reliable, Flow may integrate established mechanisms such as DBusMenu and supported toolkit-specific action exports.

Codex must research actual toolkit behavior and supported protocols rather than assuming universal compatibility.

**Tier C — Applications without exported menus**

Preserve the application's native menu bar, header-bar menu or in-window command interface.

Never hide an application menu unless the replacement has been verified to provide equivalent functionality.

Never invent menu commands that the application cannot execute.

## 7. Architecture and ownership

The implementation must preserve Flow's architectural boundaries.

**Niri**
- Owns actual window focus, activation and compositor-managed layout.
- Exposes window and focus state through supported IPC mechanisms.

**Flow Controller**
- Tracks semantic Group membership.
- Maintains the relationship between Groups, spaces and member windows.
- Resolves the active application context.
- Publishes relevant state through the Flow Platform API.

**Quickshell**
- Renders application-menu surfaces.
- Renders Group controls.
- Adapts menu presentation to desktop and touch layouts.
- Must not become the authoritative owner of Group or application state.

**Flow Platform API**
- Allows toolkit-independent applications to expose commands and request supported actions.
- Uses stable, versioned interfaces.
- Must not require applications to import Quickshell or interact directly with Niri.

Suggested conceptual API responsibilities:

- `Applications.GetFocusedContext`
- `Applications.ListCommands`
- `Applications.InvokeCommand`
- `Groups.GetMembership`
- `Groups.ListActions`
- `Groups.RequestAction`
- `Events.ActiveApplicationChanged`
- `Events.GroupMembershipChanged`

These are proposed API concepts, **not finalized D-Bus methods**. Codex should propose a minimal, implementable contract before committing to interface names or schemas.

### Security

Applications must not gain unrestricted control over unrelated windows merely by registering a command provider.

Validate command ownership and caller identity. Preserve normal Wayland focus restrictions, sandbox boundaries and permission controls.

## 8. Important edge cases

Codex must account for:

- Two windows from the same application inside one Group.
- Several different applications inside one Group.
- An application opening a dialog or secondary window.
- A Group member closing or crashing.
- An application with no exportable menu.
- Focus moving between Groups.
- Focus changing while a menu is open.
- Quickshell popups temporarily taking input focus.
- A Group moving between displays.
- Multiple displays with different focused windows.
- Touch interaction, keyboard-only navigation and screen readers.
- Applications with their own menu bars or header-bar controls.

Menu ownership should follow the actual application/window context, not merely an application name inferred from a process.

## 9. Universal search integration

Flow's universal search should eventually discover and execute application commands where available.

For example:

`Studio → Export selection`

`Notes → Create new note`

Results must distinguish which application owns a command.

When multiple windows of the same application exist, Flow must resolve or explicitly ask for the intended target if ambiguous.

Search must respect application permissions, command availability and user-session boundaries.

## 10. Acceptance criteria

The implementation can be considered functionally correct when:

- [ ] A Group containing two real Linux applications is displayed.
- [ ] Clicking or keyboard-focusing either application changes the active application context correctly.
- [ ] Only the focused application's menu is shown.
- [ ] Opening a menu does not produce unexpected context switching.
- [ ] Application commands are correctly routed to their originating application.
- [ ] Group actions remain separate from application commands.
- [ ] Closing one member does not close the entire Group.
- [ ] Returning to a Group restores its previous application focus where possible.
- [ ] Unsupported applications retain access to their native menus.
- [ ] Moving the Group between displays preserves its identity and menu behavior.
- [ ] Keyboard-only and touch interaction paths are usable.
- [ ] An application with no Flow-specific integration still works normally.

Tests must use real application windows and actual Niri events. HTML mock behavior is not sufficient evidence of a native implementation.

## 11. Implementation guidance for Codex

Do not immediately implement a universal application menu exporter.

Start with a small technical investigation:

1. Inspect the existing Flow Controller and Niri IPC integration.
2. Verify reliable focus tracking for multiple real application windows in one Group.
3. Implement a minimal Quickshell menu-context indicator using actual focus events.
4. Handle focus preservation when Quickshell menus open.
5. Add an independently accessible Group action menu.
6. Investigate menu discovery support across GTK, Qt, Electron and GPUI.
7. Propose a versioned Flow command-provider interface.
8. Add real command invocation only for verified providers.
9. Preserve existing application menus as the fallback.
10. Document gaps, tests, limitations and any compositor-level requirements.

Avoid modifying Niri or introducing a new UI framework unless the investigation demonstrates a concrete need.

## 12. Design decision summary

**Approved direction:**

- One active application menu per Group.
- Application menu follows real focus.
- Group arrangement commands remain independent.
- Desktop and touch use different presentations of the same underlying command model.
- Quickshell renders the system-level interface.
- Flow Controller manages semantic context.
- Flow Platform API supports optional framework-independent application commands.
- Existing application menus remain available whenever interoperability is incomplete.

**Final rule:**

Navigation belongs to the Group. Commands belong to the focused application. Arrangement belongs to the Group.