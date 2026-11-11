{ ... }: {
  # Evaluation fixture only. Do not install this as a machine configuration.
  services.flow.enable = true;
  boot.loader.grub.enable = false;
  fileSystems."/" = { device = "/dev/disk/by-label/nixos"; fsType = "ext4"; };
  system.stateVersion = "25.11";
}
