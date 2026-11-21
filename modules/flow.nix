{ config, lib, pkgs, ... }:
let
  cfg = config.services.flow;
  spaces = pkgs.writeShellScriptBin "flow-spaces" ''
    export PYTHONPATH="${../session}"
    user_name="$(${pkgs.coreutils}/bin/id -un)"
    export XDG_DATA_DIRS="''${XDG_DATA_DIRS:+$XDG_DATA_DIRS:}$HOME/.nix-profile/share:$HOME/.local/state/nix/profile/share:/etc/profiles/per-user/$user_name/share:/nix/var/nix/profiles/default/share:/run/current-system/sw/share:/usr/local/share:/usr/share"
    export PATH="${pkgs.glib}/bin:$PATH"
    exec ${pkgs.python3}/bin/python3 ${../session/spaces.py} "$@"
  '';
  recovery = pkgs.writeShellScriptBin "flow-recovery" ''
    export PYTHONPATH="${../session}"
    export PATH="${pkgs.glib}/bin:$PATH"
    exec ${pkgs.python3}/bin/python3 ${../session/restore.py} "$@"
  '';
  appmenu = pkgs.writeShellScriptBin "flow-appmenu" ''
    user_name="$(${pkgs.coreutils}/bin/id -un)"
    export XDG_DATA_DIRS="''${XDG_DATA_DIRS:+$XDG_DATA_DIRS:}$HOME/.nix-profile/share:$HOME/.local/state/nix/profile/share:/etc/profiles/per-user/$user_name/share:/nix/var/nix/profiles/default/share:/run/current-system/sw/share:/usr/local/share:/usr/share"
    export GI_TYPELIB_PATH="${pkgs.at-spi2-core}/lib/girepository-1.0:${pkgs.glib}/lib/girepository-1.0:${pkgs.gobject-introspection}/lib/girepository-1.0''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
    exec ${pkgs.python3.withPackages (p: [ p.pyatspi p.pygobject3 ])}/bin/python3 ${../session/appmenu.py} "$@"
  '';
  appLaunch = pkgs.writeShellScriptBin "flow-app-launch" ''
    ${lib.getExe pkgs.quickshell} ipc -p /etc/flow/shell.qml call desk leave >/dev/null 2>&1 || true
    # Launched apps must not inherit the launcher's closed output pipes.
    exec "$@" </dev/null >/dev/null 2>&1
  '';
  launcher = pkgs.writeShellScriptBin "flow-apps" ''
    user_name="$(${pkgs.coreutils}/bin/id -un)"
    export XDG_DATA_DIRS="''${XDG_DATA_DIRS:+$XDG_DATA_DIRS:}$HOME/.nix-profile/share:$HOME/.local/state/nix/profile/share:/etc/profiles/per-user/$user_name/share:/nix/var/nix/profiles/default/share:/run/current-system/sw/share:/usr/local/share:/usr/share"
    exec ${lib.getExe pkgs.fuzzel} --layer=overlay --keyboard-focus=on-demand --override=main.exit-on-keyboard-focus-loss=yes --launch-prefix=${lib.getExe appLaunch} "$@"
  '';
  search = pkgs.writeShellScriptBin "flow-search" ''
    user_name="$(${pkgs.coreutils}/bin/id -un)"
    export XDG_DATA_DIRS="''${XDG_DATA_DIRS:+$XDG_DATA_DIRS:}$HOME/.nix-profile/share:$HOME/.local/state/nix/profile/share:/etc/profiles/per-user/$user_name/share:/nix/var/nix/profiles/default/share:/run/current-system/sw/share:/usr/local/share:/usr/share"
    export GI_TYPELIB_PATH="${pkgs.glib}/lib/girepository-1.0:${pkgs.gobject-introspection}/lib/girepository-1.0''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
    exec ${pkgs.python3.withPackages (p: [ p.pygobject3 ])}/bin/python3 ${../session/search.py}
  '';
  # The shell's files, as one store directory: /etc/flow, and the restart trigger of the shell's unit.
  shellFiles = pkgs.linkFarm "flow-shell" [
    { name = "shell.qml"; path = ../session/shell.qml; }
    { name = "FloeTheme.qml"; path = ../session/FloeTheme.qml; }
  ];
  shellLauncher = pkgs.writeShellScriptBin "flow-shell" ''
    # A user unit gets a minimal PATH; the shell and the apps it opens need the session's.
    export PATH="/run/wrappers/bin:/etc/profiles/per-user/$(${pkgs.coreutils}/bin/id -un)/bin:/run/current-system/sw/bin:$PATH"
    # One installed shell per user. This also ends a shell Niri started before the shell was a unit.
    ${pkgs.procps}/bin/pkill -u "$(${pkgs.coreutils}/bin/id -u)" -f -x '(/[^ ]*/)?quickshell -p /etc/flow/shell\.qml' || true
    exec ${lib.getExe pkgs.quickshell} -p /etc/flow/shell.qml
  '';
  # Flow Platform API (docs/engineering/ADR-0009-platform-api-v1.md): a session D-Bus service and its CLI.
  apiPython = pkgs.python3.withPackages (p: [ p.pygobject3 ]);
  apiTypelibs = "${pkgs.glib}/lib/girepository-1.0:${pkgs.gobject-introspection}/lib/girepository-1.0";
  flowApi = pkgs.writeShellScriptBin "flow-api" ''
    export PYTHONPATH="${../session}"
    export GI_TYPELIB_PATH="${apiTypelibs}"
    export PATH="${spaces}/bin:${config.programs.niri.package}/bin:${pkgs.quickshell}/bin:${pkgs.util-linux}/bin:$PATH"
    export FLOW_SHELL_CONFIG=/etc/flow/shell.qml
    exec ${apiPython}/bin/python3 ${../session/flow_api.py}
  '';
  flowCli = pkgs.writeShellScriptBin "flow" ''
    export PYTHONPATH="${../session}"
    export GI_TYPELIB_PATH="${apiTypelibs}"
    exec ${apiPython}/bin/python3 ${../session/flow_cli.py} "$@"
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
    terminal = lib.getExe pkgs.foot;
    lock = lib.getExe pkgs.swaylock;
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
    environment.systemPackages = (with pkgs; [
      foot fuzzel quickshell swaylock firefox nautilus gnome-control-center networkmanagerapplet spaces launcher appmenu keyboard wvkbd wifiSettings
      xwayland-satellite mesa-demos wayland-utils recovery search wireplumber networkmanager flowCli
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
    system.userActivationScripts.flow-shell = ''
      if ${pkgs.systemd}/bin/systemctl --user is-active --quiet niri.service; then
        ${pkgs.systemd}/bin/systemctl --user start flow-shell.service || true
      fi
    '';
    systemd.user.services.flow-shell = {
      description = "Flow shell";
      wantedBy = [ "niri.service" ];
      partOf = [ "niri.service" ];
      after = [ "niri.service" ];
      restartTriggers = [ shellFiles pkgs.quickshell ];
      serviceConfig = {
        ExecStart = "${shellLauncher}/bin/flow-shell";
        Restart = "on-failure";
        # Apps opened from the shell are its children; stopping or restarting the shell must not end them.
        KillMode = "process";
      };
    };
    environment.etc."niri/config.kdl".source = niriConfig;
    environment.etc."flow".source = shellFiles;
    environment.etc."xdg/fuzzel/fuzzel.ini".text = ''
      [main]
      font=sans:size=12
      prompt="Search apps: "
      [colors]
      background=0b1015f2
      text=ecf2f0ff
      prompt=b8ddcbff
      input=ecf2f0ff
      match=b8ddcbff
      selection=172128ff
      selection-text=ecf2f0ff
      selection-match=b8ddcbff
      border=b8ddcbff
      [border]
      radius=12
    '';
    environment.sessionVariables.NIXOS_OZONE_WL = "1";
  };
}
