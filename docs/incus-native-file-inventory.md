# Incus-native migration decisions — exhaustive source manifest

This manifest covers **every file GitHub compared** between the pre-Portainer root commit and `main` at `c12dcd897dff081b1973d5039edea6da9e8ee817` (**297 paths**). These are *planned* decisions, not claims that each file is implemented. `COPY` = reuse exact contents initially; `TRANSLATE` = preserve useful logic but remove old assumptions and qualify; `REMOVE` = do not carry old implementation into the new app runtime. A later reconciliation should compare branch HEAD against this manifest and reclassify any new paths.

Totals: **21 COPY / 255 TRANSLATE / 21 REMOVE**.

| Existing source path | Action | Reason |
|---|---|---|
| `.github/actionlint.yaml` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `.github/workflows/commissioning.yml` | TRANSLATE | CI must validate native guest, OCI, UI, recovery and migrations |
| `.github/workflows/guest-deployments.yml` | TRANSLATE | CI must validate native guest, OCI, UI, recovery and migrations |
| `.github/workflows/installer.yml` | TRANSLATE | CI must validate native guest, OCI, UI, recovery and migrations |
| `.github/workflows/pages.yml` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `.github/workflows/portal-entry.yml` | TRANSLATE | CI must validate native guest, OCI, UI, recovery and migrations |
| `.github/workflows/release.yml` | TRANSLATE | CI must validate native guest, OCI, UI, recovery and migrations |
| `.github/workflows/validate.yml` | TRANSLATE | CI must validate native guest, OCI, UI, recovery and migrations |
| `.gitignore` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `README.md` | TRANSLATE | Rebuild root/flake settings and platform wiring for native Incus |
| `VERSION` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `admin/accounts.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/arcane.py` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `admin/browser_bootstrap.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/browser_certificates.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/catalog.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/commissioning.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/controller.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/deployments.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/diagnostics.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/disk_tools.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/disks.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/enrollment.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/guest_network.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/host_proof.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/impact.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/local_access.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/main.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/model.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/operations.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/package.nix` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/recipes/applications.json` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `admin/recipes/demo.json` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/recipes/disk-health.json` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/recipes/history.json` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/recipes/homelab-management.json` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/recipes/laptop.json` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/recipes/private-access.json` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/recovery.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/replacement.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/runtime.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/settings_reader.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/source.nix` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/static/api.js` | TRANSLATE | Native Incus-first Web UI; remove Arcane routes and retain auth/accessibility |
| `admin/static/app.js` | TRANSLATE | Native Incus-first Web UI; remove Arcane routes and retain auth/accessibility |
| `admin/static/brand.css` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `admin/static/commands.js` | TRANSLATE | Native Incus-first Web UI; remove Arcane routes and retain auth/accessibility |
| `admin/static/events.js` | TRANSLATE | Native Incus-first Web UI; remove Arcane routes and retain auth/accessibility |
| `admin/static/favicon.svg` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `admin/static/fonts/OFL-DM-Sans.txt` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `admin/static/fonts/OFL-Space-Grotesk.txt` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `admin/static/fonts/dm-sans-latin.woff2` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `admin/static/fonts/space-grotesk-latin.woff2` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `admin/static/foundation.js` | TRANSLATE | Native Incus-first Web UI; remove Arcane routes and retain auth/accessibility |
| `admin/static/index.html` | TRANSLATE | Native Incus-first Web UI; remove Arcane routes and retain auth/accessibility |
| `admin/static/operations.js` | TRANSLATE | Native Incus-first Web UI; remove Arcane routes and retain auth/accessibility |
| `admin/static/projects.js` | TRANSLATE | Native Incus-first Web UI; remove Arcane routes and retain auth/accessibility |
| `admin/static/style.css` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `admin/upgrades.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `admin/worker.py` | TRANSLATE | Privileged API/broker review for Incus-only ownership and transactions |
| `configuration-bootstrap.nix` | TRANSLATE | Rebuild root/flake settings and platform wiring for native Incus |
| `configuration.nix` | TRANSLATE | Rebuild root/flake settings and platform wiring for native Incus |
| `docs/administration.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/arcane.md` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `docs/architecture.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/artifact-audit.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/beta.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/commissioning.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/configuration-contract.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/credential-lifecycle.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/deployment.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/file-modules.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/guest-deployments.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/hardware.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/helpers.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/home-server-scenario.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/homelab.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/kvm-qualification.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/migration.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/networking.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/operations.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/ownership.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/plain-nixos.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/projects.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/qualification.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/recovery.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/roadmap.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/setup.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/staged-transaction-secrets.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/start.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/target-architecture.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/trial.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/troubleshooting.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/validation.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `docs/website.md` | TRANSLATE | Rewrite documentation to the Incus-only architecture and migration rules |
| `examples/demo/app.py` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `examples/demo/compose.yaml` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `examples/homelab/management.nix` | TRANSLATE | Update examples for native projects and verbatim source files |
| `examples/project-workspace/README.md` | TRANSLATE | Update examples for native projects and verbatim source files |
| `examples/project-workspace/flake.nix` | TRANSLATE | Update examples for native projects and verbatim source files |
| `examples/project-workspace/sample/default.nix` | TRANSLATE | Update examples for native projects and verbatim source files |
| `examples/project-workspace/sample/modules/storage.nix` | TRANSLATE | Update examples for native projects and verbatim source files |
| `flake.lock` | TRANSLATE | Rebuild root/flake settings and platform wiring for native Incus |
| `flake.nix` | TRANSLATE | Rebuild root/flake settings and platform wiring for native Incus |
| `guests/apps.nix` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `hardware-configuration.nix` | TRANSLATE | Rebuild root/flake settings and platform wiring for native Incus |
| `helpers/compatibility.json` | TRANSLATE | Keep minimal pinned helpers; retire Docker-specific packages |
| `helpers/package.nix` | TRANSLATE | Keep minimal pinned helpers; retire Docker-specific packages |
| `helpers/runner.py` | TRANSLATE | Keep minimal pinned helpers; retire Docker-specific packages |
| `helpers/sources.json` | TRANSLATE | Keep minimal pinned helpers; retire Docker-specific packages |
| `installer/bootstrap.py` | TRANSLATE | Installer must provision Incus-only host and Web UI account safely |
| `installer/direct.py` | TRANSLATE | Installer must provision Incus-only host and Web UI account safely |
| `installer/install.py` | TRANSLATE | Installer must provision Incus-only host and Web UI account safely |
| `installer/intent.py` | TRANSLATE | Installer must provision Incus-only host and Web UI account safely |
| `installer/network_repair.py` | TRANSLATE | Installer must provision Incus-only host and Web UI account safely |
| `installer/package.nix` | TRANSLATE | Installer must provision Incus-only host and Web UI account safely |
| `installer/update.py` | TRANSLATE | Installer must provision Incus-only host and Web UI account safely |
| `installer/workflow.py` | TRANSLATE | Installer must provision Incus-only host and Web UI account safely |
| `lib/foundation-types.nix` | TRANSLATE | Typed platform/project schemas and validation for Incus-native |
| `lib/machine.nix` | TRANSLATE | Typed platform/project schemas and validation for Incus-native |
| `lib/platform.nix` | TRANSLATE | Typed platform/project schemas and validation for Incus-native |
| `lib/project-workspace.nix` | TRANSLATE | Typed platform/project schemas and validation for Incus-native |
| `lib/settings-migrations.nix` | TRANSLATE | Typed platform/project schemas and validation for Incus-native |
| `lib/settings.nix` | TRANSLATE | Typed platform/project schemas and validation for Incus-native |
| `lib/site-modules.nix` | TRANSLATE | Typed platform/project schemas and validation for Incus-native |
| `modules/admin-ui.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/apps-onboarding.nix` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `modules/arcane-cookies.js` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `modules/backups.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/cloudflared.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/cockpit.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/connectivity.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/console.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/deployments.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/docker-host.nix` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `modules/file-disks.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/guest-secrets.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/hardware-support.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/helpers.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/incus.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/installed-base.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/instance-schema.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/management.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/monitoring.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/mount-schema.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/mounts.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/networking.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/notifications.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/patches/.gitattributes` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `modules/patches/dhcpcd-ipv6nd-interface-lifetime.patch` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `modules/quality-of-life.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/radios/bluetooth.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/radios/wifi.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/recovery-mode.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/secrets.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/security.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/settings.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/storage.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `modules/virtualization.nix` | TRANSLATE | Host module: remove Docker VM coupling, preserve host features |
| `scripts/arcane_credentials.py` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `scripts/arcane_key.py` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `scripts/artifact_audit.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/backup.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/backup_health.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/backup_plan.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/backup_verification.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/console_status.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/deploy-apps.py` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `scripts/foundation.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/guest_deploy.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/homelab.nix` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/homelab.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/import-app-image.py` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `scripts/incus_client.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/infrastructure.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/legacy_apps.py` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `scripts/locking.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/notify.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/preflight.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/project.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/project_workspace.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/reconcile.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/recovery_guard.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `scripts/release.py` | TRANSLATE | Host/Incus helper: remove Docker assumptions and enforce ownership |
| `secrets/README.md` | TRANSLATE | Keep SOPS/age; document secret references and no plaintext uploads |
| `site-defaults.nix` | TRANSLATE | Rebuild root/flake settings and platform wiring for native Incus |
| `site-modules/README.md` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `site.nix` | TRANSLATE | Rebuild root/flake settings and platform wiring for native Incus |
| `tests/apps-integration.nix` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `tests/arcane-client.py` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `tests/arcane-vm.nix` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `tests/backup-verification-integration.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/bootstrap-transaction-fixture.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/bootstrap-transaction-vm.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/browser_foundation.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/check_mount_projection.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/cockpit-client.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/console-client.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/credential_fixtures.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/enrollment-fixture.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/evaluate_direct_install.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/fixtures/architecture-contract.json` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/fixtures/defaults.json` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/foundation-vm.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/guest-deployment-fixture.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/guest-deployments-kvm-fixture.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/guest-deployments-kvm.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/guest-deployments-vm.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/guest-vm-diagnostics.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/hardware.nix` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `tests/impact-integration.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/incus-certificate-integration.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/installed-base.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/management-vm.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/module-tree/_disabled/ignored.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/module-tree/disks/archive.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/module-tree/typed-settings.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/network-inventory-integration.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/network-repair-integration.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/portal-client.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/portal-fixture.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/portal-vm.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/project-workspace-bad-claim/alpha/default.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/project-workspace-bad-claim/beta/default.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/project-workspace-duplicates/one/default.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/project-workspace-duplicates/two/default.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/project-workspace-invalid-secret/alpha/default.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/project-workspace-plaintext/alpha/.env` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/project-workspace-plaintext/alpha/default.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/project-workspace/alpha/default.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/project-workspace/alpha/files/not-imported.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/project-workspace/alpha/modules/_disabled/do-not-import.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/project-workspace/alpha/modules/nested/resources.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/project-workspace/alpha/modules/network.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/project-workspace/beta/default.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/project-workspace/flake.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/replacement-integration.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/settings-checks.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/site-source.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/site.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/staged-secret-fixture.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/startup-vm.nix` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_admin_enrollment.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_app_image_import.py` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `tests/test_apps_onboarding.py` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `tests/test_arcane_credentials.py` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `tests/test_arcane_runtime.py` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `tests/test_architecture_contract.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_artifact_audit.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_backup_plan.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_backup_verification.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_bootstrap_transaction.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_browser_bootstrap.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_browser_certificates.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_clock_readiness.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_commands_ui.js` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_commissioning.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_compatibility_surface.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_completion.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_console_status.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_credential_contract.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_credential_retirement.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_deployments.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_deployments_ui.js` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_direct_install.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_enrollment_atomic.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_foundation.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_foundation_ui.js` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_guest_network.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_homepage.js` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_http_limits.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_impact.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_install_intent.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_installer_workflow.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_job_storage.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_key_enrollment.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_legacy_apps.py` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `tests/test_management.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_network_repair.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_operations_ui.js` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_platform.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_project_workspace.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_proxy_cookies.js` | REMOVE | Legacy Docker/Arcane implementation, onboarding, proxy, or test |
| `tests/test_redesign_ui.js` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_release.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_replacement.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_secret_cleanup.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_settings_reader.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_setup.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_update_command.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `tests/test_worker_inspection.py` | TRANSLATE | Adjust/add tests for native Incus runtime, Web UI and data preservation |
| `website/assets/site.js` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `website/assets/style.css` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `website/build.py` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
| `website/index.html` | TRANSLATE | Remove obsolete Portainer/Arcane instructions and rebuild docs |
| `website/requirements.txt` | TRANSLATE | Remove obsolete Portainer/Arcane instructions and rebuild docs |
| `website/templates/doc.html` | COPY | Reusable unchanged asset, neutral example, fixture, or tool |
