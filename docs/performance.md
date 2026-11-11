# UTM performance observations

2026-11-11, existing ARM64 NixOS VM, UTM QEMU/virgl, 8 GiB RAM, one display, with Nautilus, Kate, KWrite, Ark, two terminals and the Flow shell open. This is a small interactive development VM, not a controlled benchmark or a measure of physical-device performance.

- At the sampling point, Linux reported 7,897 MiB RAM total and 6,626 MiB available; the root filesystem had 9.4 GiB free (71% used). No swap was configured.
- Process RSS at that point: Quickshell about 158 MiB, Niri about 156 MiB, Nautilus about 206 MiB, Kate about 135 MiB. RSS includes shared pages and these figures must not be summed to estimate total use.
- Three `flow-spaces refresh` invocations took 53–73 ms wall time. This is a process-launch-plus-IPC measurement, not a frame-time measurement; the shell currently refreshes every three seconds.
- Reading the focused Kate accessibility menu took 887–936 ms across three runs before the change and 907–926 ms after it. The expensive full traversal remains on demand when opening the menu.
- The new focused-application identity request, which does not traverse accessibility menus, took 41–55 ms over three runs. Flow uses it on focus changes instead of reading the entire menu eagerly.

Measurements were taken with the VM running interactively and its caches warm, using wall-clock timestamps; they are indicative only. CPU percentages from `ps` are lifetime averages and are not an idle-load benchmark. No controlled startup latency, frame pacing, GPU utilization, battery usage, touch latency or physical hardware measurements were collected. For useful comparisons, repeat with the same guest configuration, workload and sample method; measure frame times separately before making smoothness claims.

## Event-driven space refresh

The shell now schedules a debounced refresh for Niri window and workspace events, with a 30-second fallback while the event stream is running and a three-second fallback if it stops. It retries the event stream after five seconds. In the ARM64 VM, a temporary wrapper counted two startup refreshes and no further refreshes over the next ten idle seconds; changing focus caused new refreshes. The switcher displayed the existing applications and stack. Killing the event-stream process resulted in a new event-stream process after the retry. This is a process-count smoke test, not a CPU or battery benchmark.
