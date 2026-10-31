# Deployment inventory. Empty values deliberately prevent activation.
# Fill from the physical host; documentation examples are not discovered facts.
{
  ready = false;
  system = "x86_64-linux";
  hostName = "nixlab";
  cpuVendor = ""; # intel or amd
  adminKeys = [ ];
  management = {
    interface = "";
    address = "";
    prefixLength = 24;
    gateway = "";
    dns = [ ];
    trustedCIDRs = [ ];
    configure = false; # preserve installer networking until explicitly enabled
  };
  guests = {
    cidr = "";
    gatewayCIDR = "";
    dhcpRange = ""; # e.g. 10.77.0.100-10.77.0.199 (not an actual LAN)
    appsAddress = "";
  };
  storage = {
    driver = "dir"; # dir is the non-formatting baseline; zfs requires an existing dataset
    source = "/var/lib/incus/storage-pools/guests";
    confirmedExistingZfsDataset = false;
    hostId = ""; # eight hexadecimal characters, required for ZFS
  };
  cockpit = { enable = false; port = 9090; managePassword = false; };
  apps = {
    enable = false; # opt in to Docker/Portainer; no idle VM on a hypervisor-only host
    cpu = "2";
    memory = "2GiB";
    disk = "64GiB";
    # Version verified against upstream LTS releases 2026-10-27.
    # For immutable registry content replace with image@sha256:digest.
    portainerImage = "portainer/portainer-ce:2.45.3";
    portainerPort = 9441; # retained for existing hosts; homelab recipe selects 9443
    enableDemo = false; # requires /var/lib/nixlab-secrets/demo-db-password in the VM
  };
  # Additional instances: { name; project = "lab"; image = "images:<fingerprint>";
  # type = "container"; profiles = [ "base" ]; config = {}; devices = {}; }
  instances = [ ];
  mounts = [ ]; # existing UUID filesystems only, managed under /srv/nixlab
  connectivity = {
    bridges = [ ]; # { name = "br-lan"; interfaces = [ "enp2s0" ]; }
    vlans = [ ]; # { name = "vlan20"; interface = "enp2s0"; id = 20; }
    guestInterfaces = [ "incusbr0" ]; # Host firewall integration, not guest network definitions.
    trustedLANAccess = false;
    privateAccess = [ ]; # { cidr = "192.168.1.2/32"; protocol = "udp"; ports = [ 53 ]; }
  };
  hardware.serialAliases = [ ]; # Explicit { name; vendorId; productId; serial; } selections only.
  helpers.enable = true; # CLI only, no background service or browser script execution.
  radios = {
    wifi = { enable = false; interface = ""; ssid = ""; manageProfile = false; };
    bluetooth.enable = false;
  };
  monitoring = { collection = false; history = false; retentionDays = 14; scrapeSeconds = 60; };
  maintenance = { trim = true; optimiseStore = true; timeZone = "UTC"; laptopMode = false; };
  setup = { enable = false; port = 9440; confirmationSeconds = 600; };
  secrets = {
    enable = false; # enable sops-nix after adding encrypted files
    hostFile = ""; # repository-relative encrypted YAML
    guestFile = "";
    ageKeyFile = "/var/lib/sops-nix/key.txt"; # private key deployed out of band
  };
  tunnel = {
    enable = false;
    uuid = "";
    credentialsFile = "/var/lib/nixlab-secrets/cloudflared.json";
    ingress = { }; # "app.example.com" = "http://<appsAddress>:8080";
  };
  backup = {
    enable = false;
    repository = ""; # independent storage, e.g. sftp:backup@server:/backups/nixlab
    passwordFile = "/var/lib/nixlab-secrets/restic-password";
    environmentFile = "/var/lib/nixlab-secrets/restic-environment";
    stagingDirectory = "/var/lib/nixlab/exports";
    initialize = false; # explicitly opt in after verifying the remote repository
    schedule = "*-*-* 03:00:00";
    groups = [ ]; # Lists of project/guest identities that must stop together.
    exclude = [ ]; # Explicit maintenance exclusions; also honors user.nixlab.backup=manual.
    reserveMiB = 1024;
  };
}
