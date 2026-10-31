{ ... }: {
  nixlab.settings = {
    cockpit.enable = true;
    apps = { enable = true; portainerPort = 9443; };
    maintenance.laptopMode = true;
  };
}
