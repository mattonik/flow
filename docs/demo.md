# Recording a Flow demo

This walkthrough uses the real Niri session and installed applications. It does not require a reboot or a specially prepared snapshot. Save any work in the applications first.

1. Open two or three applications, for example Files, a browser and a text editor. Put recognizable content in them so their surfaces are easy to distinguish. `Super+Return` opens a terminal; **Apps** in the island opens the application launcher.
2. Use `Super+Left` and `Super+Right` to move between app spaces. Open the Desk with `Super+H`; its search finds installed applications, open windows and available pins. Escape dismisses search.
3. With a single, unpinned app focused, use `Super+P` to pin it. Open the Desk to show the pinned space and select it to return. **Edit pins** reveals reorder and unpin controls.
4. Choose **Overview** at the bottom to see Niri's real application surfaces. Choose **Desk** in the same spot, then **Return to work** or the Floe button in the Island. For a continuity check, compare `niri msg -j focused-window` and `niri msg -j windows` before and after this round trip. You can also select a live window directly from Overview.
5. Open the Spaces switcher with **Spaces** at the bottom or `Super+Tab`. To create a two-app Group, focus an unpinned tiled window with another single, unpinned window immediately to its right and choose **Stack with next**, then **Create Group**. The switcher shows the Group as one space with controls to focus either member. Use **Rename Group** to give it a name. **Close** and Escape both dismiss the switcher.
6. Open quick settings from the island or with `Super+I`; the network status and volume controls reflect the guest system. Escape dismisses the panel.

The Spaces switcher uses window titles. Group orientation changes manually, and file search uses recent-file history. For portrait validation, enable the experimental application menu, open an app with a compatible menu, and check that its trigger sits below the Island and the popup fits the output. Physical touch and automatic Group reflow remain open.
