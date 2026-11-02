# TEST FIXTURE ONLY. No values below describe the user's host or network.
let base = import ../site.nix; in base // {
  # Exercise the optional application platform in full-stack fixtures.
  apps = base.apps // { enable = true; };
  ready = true; cpuVendor = "intel";
  adminKeys = [ "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA fixture-only" ];
  management = { interface = "eth0"; address = "192.0.2.10"; prefixLength = 24;
    gateway = "192.0.2.1"; dns = [ "192.0.2.1" ]; trustedCIDRs = [ "192.0.2.0/24" ]; configure = false; };
  guests = { bridge = "incusbr0"; cidr = "10.77.0.0/24"; gatewayCIDR = "10.77.0.1/24";
    dhcpRange = "10.77.0.100-10.77.0.199"; appsAddress = "10.77.0.10"; };
}

