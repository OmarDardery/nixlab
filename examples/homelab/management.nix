{ ... }: {
  nixlab.settings = {
    cockpit.enable = true;
    apps = { enable = true; arcanePort = 9443; };
    maintenance.laptopMode = true;
  };
}
