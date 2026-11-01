#!/usr/bin/env bash
# Called only by the version-tag release job, after validation and ISO boot tests.
set -euo pipefail
shopt -s nullglob

artifact_dir=$(realpath "${1:?expected installer artifact directory}")
tag=${GITHUB_REF_NAME:?missing release tag}
revision=${GITHUB_SHA:?missing source revision}
repository=${GITHUB_REPOSITORY:?missing repository}
version=$(cat VERSION)
[[ "$tag" == "v$version" && "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || {
  echo 'Release tag must be v<VERSION> (for example v0.1.0-rc1).' >&2
  exit 1
}
[[ "$(git rev-parse HEAD)" == "$revision" ]] || {
  echo 'Checkout does not match the workflow revision.' >&2
  exit 1
}
python3 - "$artifact_dir/manifest.json" "$revision" "$version" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text())
if manifest.get("revision") != sys.argv[2] or manifest.get("version") != sys.argv[3]:
    raise SystemExit("Installer provenance does not match this release.")
PY
(
  cd "$artifact_dir"
  sha256sum --check --strict SHA256SUMS
)
images=("$artifact_dir"/*.iso)
[[ ${#images[@]} == 1 && -s "${images[0]}" ]] || {
  echo 'Expected exactly one nonempty installer ISO.' >&2
  exit 1
}
assets=("${images[0]}" "$artifact_dir/SHA256SUMS" "$artifact_dir/manifest.json" "$artifact_dir/TRIAL.md")
for asset in "${assets[@]}"; do test -s "$asset"; done
[[ "$(gh api "repos/$repository/commits/$tag" --jq .sha)" == "$revision" ]] || {
  echo 'Remote release tag no longer points to the tested revision.' >&2
  exit 1
}

notes=$(mktemp)
trap 'rm -f "$notes"' EXIT
cat > "$notes" <<EOF
Nixlab $version — single-host NixOS homelab installer (x86_64).

Download the .iso, SHA256SUMS, manifest.json and TRIAL.md into one directory.
Run \`sha256sum -c SHA256SUMS\`, then write the ISO to a USB drive.
Boot it, connect to the Internet and run \`sudo nixlab-setup\`.

Source: $revision
Validation: https://github.com/$repository/actions/runs/${GITHUB_RUN_ID:?missing run ID}
Passed source/system validation and BIOS/UEFI installer boot smoke tests.
Physical installation, first-boot setup, application stacks and an off-host restore
remain commissioning checks. This image is not hardware-certified.

Installation: https://github.com/$repository/blob/$tag/docs/iso.md
Management: https://github.com/$repository/blob/$tag/docs/homelab.md
EOF

release_flags=()
latest_flag=--latest
if [[ "$version" == *-* ]]; then
  release_flags+=(--prerelease)
  latest_flag=--latest=false
fi
# A failed upload leaves an unpublished draft. Never overwrite an existing release.
gh release create "$tag" "${assets[@]}" --repo "$repository" --verify-tag \
  --draft --title "Nixlab $version" --notes-file "$notes" "${release_flags[@]}"
gh release edit "$tag" --repo "$repository" --draft=false "$latest_flag"
