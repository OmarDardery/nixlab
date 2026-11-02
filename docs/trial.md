# Nixlab homelab trial

Start with an ordinary installed NixOS server and follow [single-command
installation](plain-nixos.md). Record the Git revision, hardware model, firmware
mode, disks and results below.

1. Run the canonical install command; interrupt and rerun it once. Verify existing
   users, disk data and guests are preserved. Reboot twice without restarting any
   service manually. Check `sudo nixlab doctor`, SSH password login, Cockpit login
   and terminal, Incus UI and Portainer sign-in using the printed credentials.
2. Enroll an Incus browser certificate. Create and reboot a Linux container and VM;
   verify their data survives and native Incus edits survive a host rebuild.
3. Create a LAN bridge or private network as appropriate. Run `sudo nixlab-helpers
   doctor --project lab` and `sudo nixlab-helpers run adguard --project lab` in a
   terminal. Verify DNS and web access from the intended LAN, reboot and test data
   persistence. Scripts remain unverified until these checks and restore pass.
4. Test any selected VLAN on the actual switch and router. For a USB serial device,
   declare its stable alias, attach it in Incus, then disconnect/reconnect and reboot.
5. Configure an independent backup repository. Back up a disposable guest, recover
   the archive from that repository and restore it under a new name. Verify the data.
6. Test a reviewed update and console-backed network-loss rollback. Confirm the
   previous configuration returns and guest disks remain intact.

Export redacted diagnostics and note failures, source/helper revisions, network
layout and restore results. Do not share passwords, private keys or raw backup data.
