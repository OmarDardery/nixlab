# Evaluate NixLab's host listeners once for collision checks and input policy.
{ config, lib, site, ... }:
let
  inherit (lib) concatMap optional optionals;
  stripBrackets = address: lib.removeSuffix "]" (lib.removePrefix "[" address);
  listener = service: exposure: address: port:
    let bind = stripBrackets address;
    in {
      inherit service exposure port;
      address = bind;
      protocol = "tcp";
      family = if builtins.elem bind [ "" "*" ] then "dual"
        else if lib.hasInfix ":" bind then "ipv6"
        else if builtins.match "[0-9]+(\\.[0-9]+){3}" bind != null then "ipv4"
        else "dual";
    };
  nginx = name: service:
    let vhost = config.services.nginx.virtualHosts.${name} or null;
    in optionals (config.services.nginx.enable && vhost != null)
      (map (socket: listener service "trusted" socket.addr socket.port) vhost.listen);
  ssh = config.services.openssh;
  sshBindings = if ssh.listenAddresses == [ ] then [{ addr = "*"; port = null; }]
    else ssh.listenAddresses;
  listeners = optionals ssh.enable (concatMap (binding:
      map (listener "SSH" "trusted" (if binding.addr == null then "*" else binding.addr))
        (if binding.port == null then ssh.ports else [ binding.port ])
    ) sshBindings)
    ++ nginx "nixlab-entry" "Portal redirect"
    ++ optional site.setup.enable (listener "Portal HTTPS" "trusted" "0.0.0.0" site.setup.port)
    ++ optional config.services.cockpit.enable (listener "Cockpit" "trusted" "0.0.0.0" config.services.cockpit.port)
    ++ nginx "nixlab-portainer" "Portainer proxy"
    ++ optional (config.virtualisation.incus.enable && incusBind != null)
      (listener "Incus HTTPS" "trusted" (builtins.elemAt incusBind 0) (lib.toInt (builtins.elemAt incusBind 1)))
    ++ optional prom.enable (listener "Prometheus" "local" prom.listenAddress prom.port)
    ++ optional prom.exporters.node.enable (listener "Node exporter" "local"
      prom.exporters.node.listenAddress prom.exporters.node.port);
  incusBind = builtins.match "(.*):([0-9]+)"
    (config.virtualisation.incus.preseed.config."core.https_address" or "");
  prom = config.services.prometheus;

  overlaps = a: b:
    a.protocol == b.protocol && a.port == b.port
    && (a.family == b.family || a.family == "dual" || b.family == "dual")
    && (a.address == b.address || a.family == "dual" || b.family == "dual"
      || builtins.elem "0.0.0.0" [ a.address b.address ]
      || builtins.elem "::" [ a.address b.address ]);
  conflicts = concatMap (a: map (b: {
      assertion = false;
      message = "Management listener collision on ${a.protocol}/${toString a.port}: ${a.service} (${a.address}) and ${b.service} (${b.address}).";
    }) (builtins.filter (b: a.service < b.service && overlaps a b) listeners)) listeners;

  interface = site.management.interface;
  sourceCIDRs = site.management.trustedCIDRs;
  trustedPorts = lib.unique (map (item: item.port) (builtins.filter
    (item: item.exposure == "trusted" && item.family != "ipv6" && item.address != "127.0.0.1") listeners));
in {
  options.nixlab.managementPolicy.enforceFirewall = lib.mkEnableOption "the NixLab host management input firewall";

  config = {
    assertions = conflicts;
    environment.etc."nixlab/management-exposure.json".text = builtins.toJSON {
      inherit interface sourceCIDRs listeners;
      ipv6TrustedIngress = false;
      firewallManaged = config.nixlab.managementPolicy.enforceFirewall;
    };
    networking.firewall.extraInputRules = lib.mkIf
      (config.nixlab.managementPolicy.enforceFirewall && trustedPorts != [ ] && sourceCIDRs != [ ]) ''
        iifname ${builtins.toJSON interface} ip saddr { ${lib.concatStringsSep ", " sourceCIDRs} } tcp dport { ${lib.concatMapStringsSep ", " toString trustedPorts} } accept
      '';
  };
}
