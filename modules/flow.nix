{ config, lib, pkgs, ... }:
let
  cfg = config.services.flow;
  # Flow's Python, compiled once into the store. Each helper runs its module with `python3 -P -m`, which loads
  # the compiled bytecode (a script named by its path is compiled again on every run, and the shell runs
  # flow-spaces after Niri events) and, with -P, never imports from the caller's working directory.
  # flow-spaces and flow-recovery need only the standard library, so -S skips the site module.
  # docs/performance.md has the measurements.
  sessionPython = pkgs.runCommand "flow-session-python" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    mkdir $out
    cp ${../session}/*.py $out/
    python3 -m compileall -q --invalidation-mode unchecked-hash $out
  '';
  # Where Flow's helpers find apps' desktop entries: the session's, then the Nix profiles'. flow-recovery too, so
  # that the automatic save (in the controller) and the restore after it look in the same folders.
  dataDirs = ''
    user_name="''${USER:-$(${pkgs.coreutils}/bin/id -un)}"
    export XDG_DATA_DIRS="''${XDG_DATA_DIRS:+$XDG_DATA_DIRS:}$HOME/.nix-profile/share:$HOME/.local/state/nix/profile/share:/etc/profiles/per-user/$user_name/share:/nix/var/nix/profiles/default/share:/run/current-system/sw/share:/usr/local/share:/usr/share"
  '';
  spaces = pkgs.writeShellScriptBin "flow-spaces" ''
    export PYTHONPATH="${sessionPython}"
    ${dataDirs}
    export PATH="${pkgs.glib}/bin:$PATH"
    exec ${pkgs.python3}/bin/python3 -S -P -m spaces "$@"
  '';
  # The resident controller (session/flow_controller.py, ADR-0011): the shell starts it. It runs Flow's refresh
  # in-process, so it needs what flow-spaces needs.
  controller = pkgs.writeShellScriptBin "flow-controller" ''
    export PYTHONPATH="${sessionPython}"
    ${dataDirs}
    export PATH="${pkgs.glib}/bin:$PATH"
    exec ${pkgs.python3}/bin/python3 -S -P -m flow_controller "$@"
  '';
  recovery = pkgs.writeShellScriptBin "flow-recovery" ''
    export PYTHONPATH="${sessionPython}"
    ${dataDirs}
    export PATH="${pkgs.glib}/bin:$PATH"
    exec ${pkgs.python3}/bin/python3 -S -P -m restore "$@"
  '';
  appmenu = pkgs.writeShellScriptBin "flow-appmenu" ''
    export PYTHONPATH="${sessionPython}"
    ${dataDirs}
    export GI_TYPELIB_PATH="${pkgs.at-spi2-core}/lib/girepository-1.0:${pkgs.glib}/lib/girepository-1.0:${pkgs.gobject-introspection}/lib/girepository-1.0''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
    exec ${pkgs.python3.withPackages (p: [ p.pyatspi p.pygobject3 ])}/bin/python3 -P -m appmenu "$@"
  '';
  search = pkgs.writeShellScriptBin "flow-search" ''
    ${dataDirs}
    export GI_TYPELIB_PATH="${pkgs.glib}/lib/girepository-1.0:${pkgs.gobject-introspection}/lib/girepository-1.0''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
    export PYTHONPATH="${sessionPython}"
    # gio opens the chosen app or file (--open): the shell's own PATH, the session's, has no gio.
    export PATH="${pkgs.glib}/bin:$PATH"
    exec ${pkgs.python3.withPackages (p: [ p.pygobject3 ])}/bin/python3 -P -m search "$@"
  '';
  # The shell's files, as one store directory: /etc/flow, and the restart trigger of the shell's unit.
  # Niri's appearance files are here because the shell copies the active one for Niri to include.
  shellFiles = pkgs.linkFarm "flow-shell" [
    { name = "shell.qml"; path = ../session/shell.qml; }
    { name = "FloeTheme.qml"; path = ../session/FloeTheme.qml; }
    { name = "niri-dark.kdl"; path = ../design/generated/niri-dark.kdl; }
    { name = "niri-light.kdl"; path = ../design/generated/niri-light.kdl; }
  ];
  # Keeps ~/.config/flow/niri-appearance.kdl pointing at the current appearance's Niri colours.
  niriAppearanceSync = pkgs.writeShellScriptBin "flow-niri-appearance" ''
    export PATH="${lib.makeBinPath [ pkgs.coreutils pkgs.gnugrep pkgs.util-linux ]}:$PATH"
    exec ${pkgs.bash}/bin/sh ${../session/flow-niri-appearance.sh} "$@"
  '';
  # The lock screen in Floe's theme, following the appearance switch (session/flow-lock.sh).
  flowLock = pkgs.writeShellScriptBin "flow-lock" ''
    export FLOW_SWAYLOCK_BIN=${lib.getExe pkgs.swaylock} FLOW_SWAYLOCK_THEMES=${../design/generated}
    export PATH="${pkgs.gnugrep}/bin:$PATH"
    exec ${pkgs.bash}/bin/sh ${../session/flow-lock.sh} "$@"
  '';
  shellLauncher = pkgs.writeShellScriptBin "flow-shell" ''
    # A NixOS unit gets its own PATH; the shell and the apps it opens need the login session's, which
    # Niri imported into the user manager.
    session_path="$(${pkgs.systemd}/bin/systemctl --user show-environment | ${pkgs.gnused}/bin/sed -n 's/^PATH=//p')"
    export PATH="''${session_path:-/run/wrappers/bin:/etc/profiles/per-user/$(${pkgs.coreutils}/bin/id -un)/bin:/run/current-system/sw/bin}"
    # Apps the shell opens get app scopes of their own (session/launching.py).
    export FLOW_APP_SCOPES=1
    # One installed shell per user. This also ends a shell Niri started before the shell was a unit.
    shell_line='(/[^ ]*/)?quickshell -p /etc/flow/shell\.qml'
    if ${pkgs.procps}/bin/pkill -u "$(${pkgs.coreutils}/bin/id -u)" -f -x "$shell_line"; then
      for _ in $(${pkgs.coreutils}/bin/seq 50); do
        ${pkgs.procps}/bin/pgrep -u "$(${pkgs.coreutils}/bin/id -u)" -f -x "$shell_line" >/dev/null || break
        ${pkgs.coreutils}/bin/sleep 0.1
      done
    fi
    exec ${lib.getExe pkgs.quickshell} -p /etc/flow/shell.qml
  '';
  # Flow Platform API (docs/engineering/ADR-0009-platform-api-v1.md): a session D-Bus service and its CLI.
  apiPython = pkgs.python3.withPackages (p: [ p.pygobject3 ]);
  apiTypelibs = "${pkgs.glib}/lib/girepository-1.0:${pkgs.gobject-introspection}/lib/girepository-1.0";
  flowApi = pkgs.writeShellScriptBin "flow-api" ''
    export PYTHONPATH="${sessionPython}"
    export GI_TYPELIB_PATH="${apiTypelibs}"
    export PATH="${spaces}/bin:${config.programs.niri.package}/bin:${pkgs.quickshell}/bin:${pkgs.util-linux}/bin:$PATH"
    export FLOW_SHELL_CONFIG=/etc/flow/shell.qml
    exec ${apiPython}/bin/python3 -P -m flow_api
  '';
  flowCli = pkgs.writeShellScriptBin "flow" ''
    export PYTHONPATH="${sessionPython}"
    export GI_TYPELIB_PATH="${apiTypelibs}"
    exec ${apiPython}/bin/python3 -P -m flow_cli "$@"
  '';
  flowApiActivation = pkgs.writeTextDir "share/dbus-1/services/io.github.mattonik.Flow1.service" ''
    [D-BUS Service]
    Name=io.github.mattonik.Flow1
    Exec=${flowApi}/bin/flow-api
    SystemdService=flow-api.service
  '';
  wifiSettings = pkgs.writeShellScriptBin "flow-wifi-settings" ''
    export XDG_CURRENT_DESKTOP=GNOME
    export XDG_DATA_DIRS="${pkgs.gnome-control-center}/share:''${XDG_DATA_DIRS:-/run/current-system/sw/share:/usr/share}"
    export PATH="${lib.makeBinPath [ pkgs.gnome-control-center ]}:$PATH"
    exec ${lib.getExe pkgs.gnome-control-center} wifi
  '';
  kdeSettings = pkgs.writeShellScriptBin "systemsettings" ''
    export QML2_IMPORT_PATH="${lib.makeSearchPath "lib/qt-6/qml" [ pkgs.qt6.qtpositioning pkgs.qt6.qtlocation pkgs.kdePackages.kirigami-addons pkgs.kdePackages.kdeclarative ]}''${QML2_IMPORT_PATH:+:$QML2_IMPORT_PATH}"
    exec ${lib.getExe pkgs.kdePackages.systemsettings} "$@"
  '';
  keyboard = pkgs.writeShellScriptBin "flow-keyboard" ''
    umask 077
    session="''${XDG_SESSION_ID:-$WAYLAND_DISPLAY}"
    session="$(printf '%s' "$session" | ${pkgs.coreutils}/bin/tr -c '[:alnum:]_.-' '_')"
    pidfile="$XDG_RUNTIME_DIR/flow-keyboard-$session.pid"
    exec 9>"$pidfile.lock"
    ${pkgs.util-linux}/bin/flock -x 9
    if [ -r "$pidfile" ]; then
      read -r keyboard_pid < "$pidfile" || true
      owner="$(${pkgs.procps}/bin/ps -o uid= -p "$keyboard_pid" 2>/dev/null | ${pkgs.coreutils}/bin/tr -d '[:space:]')"
      process="$(${pkgs.procps}/bin/ps -o comm= -p "$keyboard_pid" 2>/dev/null | ${pkgs.coreutils}/bin/tr -d '[:space:]')"
      if [ "$owner" = "$(${pkgs.coreutils}/bin/id -u)" ] && [ "$process" = "wvkbd-mobintl" ]; then
        kill -RTMIN "$keyboard_pid"
        exit 0
      fi
      rm -f "$pidfile"
    fi
    ${lib.getExe pkgs.wvkbd} -L 200 -H 280 9>&- &
    printf '%s\n' "$!" > "$pidfile"
  '';
  niriConfig = pkgs.replaceVars ../session/niri.kdl {
    # Polar Night until the shell writes the user's appearance (~/.config/flow/niri-appearance.kdl).
    niriAppearance = ../design/generated/niri-dark.kdl;
    terminal = lib.getExe pkgs.foot;
    lock = lib.getExe flowLock;
    idle = lib.getExe pkgs.swayidle;
    shell = lib.getExe pkgs.quickshell;
    shellConfig = "/etc/flow/shell.qml";
    spaces = lib.getExe spaces;
    keyboard = lib.getExe keyboard;
  };
in {
  options.services.flow.enable = lib.mkEnableOption "the experimental Flow session";
  config = lib.mkIf cfg.enable {
    programs.niri.enable = true;
    security.pam.services.swaylock = {};
    hardware.graphics.enable = true;
    fonts.packages = with pkgs; [
      noto-fonts
      noto-fonts-cjk-sans
      noto-fonts-color-emoji
    ];
    fonts.fontconfig.defaultFonts = {
      sansSerif = [ "Noto Sans" ];
      monospace = [ "Noto Sans Mono" ];
      emoji = [ "Noto Color Emoji" ];
    };
    security.polkit.enable = true;
    # Screen readers reach the shell and apps over AT-SPI, and so does the opt-in app menu (flow-appmenu).
    # Without it the session bus cannot start the accessibility bus and nothing is exposed.
    services.gnome.at-spi2-core.enable = true;
    environment.systemPackages = (with pkgs; [
      foot quickshell swaylock firefox nautilus gnome-control-center networkmanagerapplet spaces appmenu keyboard wvkbd wifiSettings niriAppearanceSync grim
      # Search's Lock screen runs it as Super+L does.
      flowLock
      xwayland-satellite mesa-demos wayland-utils recovery search wireplumber networkmanager flowCli controller
    ]) ++ lib.optionals config.services.desktopManager.plasma6.enable [ (lib.hiPrio kdeSettings) ];
    services.dbus.packages = [ flowApiActivation ];
    # Started on first use by D-Bus activation; it reads Flow state through flow-spaces.
    systemd.user.services.flow-api = {
      description = "Flow Platform API";
      # Bound to the graphical session, so a user manager that outlives a login never keeps a stale Niri socket.
      partOf = [ "graphical-session.target" ];
      after = [ "graphical-session.target" ];
      serviceConfig = {
        Type = "dbus";
        BusName = "io.github.mattonik.Flow1";
        ExecStart = "${flowApi}/bin/flow-api";
        # Also after a crash: the shell must not keep showing activities nobody owns.
        ExecStopPost = "${pkgs.coreutils}/bin/rm -f %t/flow-activities.json";
        Restart = "on-failure";
      };
    };
    # The shell is a user unit of the Niri session. A switch restarts it when its files or Quickshell
    # change, as it does any changed user unit: Quickshell's own live reload cannot load a
    # configuration that adds an inline component, and does not reliably notice a switch.
    # A switch restarts a changed unit but does not start a new one wanted by niri.service, so the
    # first switch to this module would leave a shell Niri spawned before; start the unit while Niri
    # runs (a no-op when it is running). Its launcher replaces the older shell.
    # Only a unit that has never run in this user manager: a user who stopped it to run a development
    # shell keeps it stopped.
    system.userActivationScripts.flow-shell = ''
      if ${pkgs.systemd}/bin/systemctl --user is-active --quiet niri.service \
        && [ "$(${pkgs.systemd}/bin/systemctl --user show flow-shell.service -p ActiveEnterTimestampMonotonic --value)" = 0 ]; then
        ${pkgs.systemd}/bin/systemctl --user start flow-shell.service || true
      fi
    '';
    systemd.user.services.flow-shell = {
      description = "Flow shell";
      wantedBy = [ "niri.service" ];
      partOf = [ "niri.service" ];
      after = [ "niri.service" ];
      # The controller the shell starts runs Flow's Python: a switch that changes it restarts the shell, so a
      # controller never keeps running code older than the flow-spaces actions beside it.
      restartTriggers = [ shellFiles pkgs.quickshell controller ];
      # A shell that keeps failing stops trying after five starts in a minute, two seconds apart.
      unitConfig = { StartLimitIntervalSec = 60; StartLimitBurst = 5; };
      serviceConfig = {
        ExecStart = "${shellLauncher}/bin/flow-shell";
        Restart = "on-failure";
        RestartSec = 2;
        # Apps get scopes of their own; this is the safeguard for any launch path that missed one, so
        # that stopping or restarting the shell never ends an app.
        KillMode = "process";
      };
    };
    environment.etc."niri/config.kdl".source = niriConfig;
    environment.etc."flow".source = shellFiles;
    environment.sessionVariables.NIXOS_OZONE_WL = "1";
  };
}
