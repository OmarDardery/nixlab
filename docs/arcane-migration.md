# Portainer → Arcane migration (not yet automatic)

NixLab intends to replace its optional Portainer CE dashboard with
[Arcane](https://getarcane.app/docs/) inside the *existing* Docker applications
VM. Track the implementation and qualification in
[issue #123](https://github.com/OmarDardery/nixlab/issues/123).

**Do not stop Portainer, remove Docker containers, or recreate guest disks to
try Arcane.** Existing Portainer-managed stacks can live in Portainer's own
database rather than \`/srv/compose\`; switching only the image would lose
visibility of those stack definitions. Docker volumes and existing running
containers must be preserved independently of a dashboard change.

## First phase: inventory only

From the NixLab host, using root's existing authenticated Portainer access:

\`\`\`sh
sudo nixlab-admin portainer migration-inventory
\`\`\`

This performs HTTPS GETs to the local Portainer stack API (after authenticating
with the retained private credential). It cannot deploy, stop, prune, rename
or delete anything. It writes a **root-only** file at:

\`\`\`text
/var/lib/nixlab-admin/portainer-to-arcane-inventory.json
\`\`\`

The file contains stack names/IDs, type, active state, whether Git backs the
stack, environment **variable names only**, and a SHA-256 of each returned
Compose file. It never includes environment values, Portainer JWTs, Compose
contents or Git credentials. Command output contains only the report path,
count and readiness flags, never credentials. If the Portainer administrator
password has changed and no longer matches NixLab's stored credential, the
inventory fails without changing that password. It does **not** trigger
Portainer password recovery.

An inventory marked \`complete: true\` means all Portainer stack **files**
could be read, *not* that any stack can safely be imported or redeployed.
\`safeToSwitch\` is deliberately always \`false\`.

## Migration prerequisites

1. Take an independent restorable backup of the applications VM and verify
   application data, bind mounts, named volumes and Portainer's database.
2. Compare the inventory against Portainer's UI and actual Docker resources.
   Review Git-based stacks, build contexts, stopped stacks, custom Compose
   file names, external networks and volume names, bind paths, and every
   secret or environment variable. Export actual Compose definitions
   separately under private access; a SHA-256 alone is not an export.
3. Keep the original project name and Compose labels. Recreating a project
   under a different name can create *different* named volumes. Never run
   \`docker compose down -v\` during the transition.
4. Stage reviewed Compose files under \`/srv/compose/<project>/compose.yaml\`
   with matching private env files. Arcane can discover compatible on-disk
   projects; **do not** launch a second copy over running services.
5. Configure Arcane with persistent \`/srv/arcane\`, a separately protected
   encryption key, its own administrator, proper TLS/HTTPS proxying and
   restricted access. Confirm it can read projects before retiring Portainer.
6. Only after tested login, project management, backup/restore and rollback
   should the Portainer container be retired. Preserve its historical database
   in the backup, not as active state.

This initial inventory tool is **not a migration switch**. It intentionally
does not alter the current Portainer service or NixLab settings.

## Upstream references

- [Arcane installation](https://getarcane.app/docs/get-started/installation)
- [Arcane projects](https://getarcane.app/docs/docker/projects)
- [Arcane 3.x environment and secrets](https://getarcane.app/docs/reference/environment-variables)
- [Arcane proxy security](https://getarcane.app/docs/networking/reverse-proxy)
- [Portainer stack endpoints](https://github.com/portainer/portainer-skills/blob/main/portainer-api/references/stacks.md)
