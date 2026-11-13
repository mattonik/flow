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
  launcher = pkgs.writeShellScriptBin "flow-apps" ''
    user_name="$(${pkgs.coreutils}/bin/id -un)"
    export XDG_DATA_DIRS="''${XDG_DATA_DIRS:+$XDG_DATA_DIRS:}$HOME/.nix-profile/share:$HOME/.local/state/nix/profile/share:/etc/profiles/per-user/$user_name/share:/nix/var/nix/profiles/default/share:/run/current-system/sw/share:/usr/local/share:/usr/share"
    exec ${lib.getExe pkgs.fuzzel} "$@"
  '';
  search = pkgs.writeShellScriptBin "flow-search" ''
    user_name="$(${pkgs.coreutils}/bin/id -un)"
    export XDG_DATA_DIRS="''${XDG_DATA_DIRS:+$XDG_DATA_DIRS:}$HOME/.nix-profile/share:$HOME/.local/state/nix/profile/share:/etc/profiles/per-user/$user_name/share:/nix/var/nix/profiles/default/share:/run/current-system/sw/share:/usr/local/share:/usr/share"
    export GI_TYPELIB_PATH="${pkgs.glib}/lib/girepository-1.0:${pkgs.gobject-introspection}/lib/girepository-1.0''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
    exec ${pkgs.python3.withPackages (p: [ p.pygobject3 ])}/bin/python3 ${../session/search.py}
  '';
  wifiSettings = pkgs.writeShellScriptBin "flow-wifi-settings" ''
    export XDG_CURRENT_DESKTOP=GNOME
    export XDG_DATA_DIRS="${pkgs.gnome-control-center}/share:''${XDG_DATA_DIRS:-/run/current-system/sw/share:/usr/share}"
    export PATH="${lib.makeBinPath [ pkgs.gnome-control-center ]}:$PATH"
    exec ${lib.getExe pkgs.gnome-control-center} wifi
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
    environment.systemPackages = with pkgs; [
      foot fuzzel quickshell swaylock firefox nautilus gnome-control-center networkmanagerapplet spaces launcher appmenu keyboard wvkbd wifiSettings
      xwayland-satellite mesa-demos wayland-utils recovery search wireplumber networkmanager
    ];
    environment.etc."niri/config.kdl".source = niriConfig;
    environment.etc."flow/shell.qml".source = ../session/shell.qml;
    environment.etc."flow/FloeTheme.qml".source = ../session/FloeTheme.qml;
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
