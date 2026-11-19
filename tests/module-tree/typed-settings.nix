{ ... }: {
  nixlab.settings.maintenance.timeZone = "UTC";
  nixlab.incus.instances.fixture = { image = "images:fixture-only"; };
}
