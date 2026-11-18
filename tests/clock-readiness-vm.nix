{ pkgs }:
pkgs.testers.runNixOSTest {
  name = "nixlab-clock-readiness";
  globalTimeout = 240;
  nodes.machine = { lib, ... }: {
    system.stateVersion = "26.05";
    virtualisation.memorySize = 512;
    networking.useDHCP = false;
    services.timesyncd.enable = lib.mkForce false;
    environment.systemPackages = [ pkgs.python3 pkgs.systemd ];
  };
  testScript = ''
    start_all()
    machine.wait_for_unit("multi-user.target")
    machine.fail("systemctl is-active systemd-timesyncd.service")
    machine.succeed("PYTHONPATH=${../admin}:${../scripts} python3 - <<'PY'\nfrom unittest.mock import Mock, patch\nimport diagnostics\nnow = 1800000000\n# No NTP service is installed in this disposable NixOS guest.\nassert diagnostics.clock_status(now)['state'] == 'plausible-but-unsynchronized'\nfor response, expected in [('yes', 'synchronized'), ('no', 'plausible-but-unsynchronized')]:\n    with patch.object(diagnostics.subprocess, 'run', return_value=Mock(returncode=0, stdout=response)):\n        assert diagnostics.clock_status(now)['state'] == expected\n        diagnostics.require_plausible_clock() if response == 'yes' else None\nwith patch.object(diagnostics.subprocess, 'run') as command:\n    assert diagnostics.clock_status(0)['state'] == 'invalid-clock'\n    command.assert_not_called()\nwith patch.object(diagnostics, 'clock_status', return_value={'state': 'invalid-clock'}):\n    try:\n        diagnostics.require_plausible_clock()\n    except ValueError:\n        pass\n    else:\n        raise AssertionError('Invalid clock was allowed to issue new identity')\nPY")
  '';
}
