# Nixlab homelab trial

This release candidate is for a disposable or recoverable single host. Verify
`SHA256SUMS`, flash the ISO, and follow [ISO installation](iso.md). Record the
manifest revision, hardware model, firmware mode, disk, and results below.

1. Install on the selected disk, remove the media, boot, finish the portal review,
   confirm access, and reboot. Verify other data disks retain their contents.
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
