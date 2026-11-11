{ config, lib, pkgs, ... }:
let
  cfg = config.services.flow;
  niriConfig = pkgs.replaceVars ../session/niri.kdl {
    terminal = lib.getExe pkgs.foot;
    launcher = lib.getExe pkgs.fuzzel;
    lock = lib.getExe pkgs.swaylock;
    shell = lib.getExe pkgs.quickshell;
    shellConfig = ../session/shell.qml;
  };
in {
  options.services.flow.enable = lib.mkEnableOption "the experimental Flow session";
  config = lib.mkIf cfg.enable {
    programs.niri.enable = true;
    security.pam.services.swaylock = {};
    hardware.graphics.enable = true;
    security.polkit.enable = true;
    environment.systemPackages = with pkgs; [
      foot fuzzel quickshell swaylock firefox nautilus
      xwayland-satellite mesa-demos wayland-utils
    ];
    environment.etc."niri/config.kdl".source = niriConfig;
    environment.etc."xdg/fuzzel/fuzzel.ini".text = ''
      [main]
      font=sans:size=12
      prompt=Search apps:
      [colors]
      background=0b1015f2
      text=ecf2f0ff
      match=b8ddcbff
      selection=172128ff
      selection-text=ecf2f0ff
      border=b8ddcbff
      [border]
      radius=12
    '';
    environment.sessionVariables.NIXOS_OZONE_WL = "1";
  };
}
