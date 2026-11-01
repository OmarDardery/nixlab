#!/usr/bin/env bash
# shellcheck disable=SC2016 # the single-quoted program runs in the child shell
set -euo pipefail
# Download to a file and run with sudo; never send credentials on this command line.
if (( EUID != 0 )); then
  echo 'Run this installer with sudo on the target NixOS host.' >&2
  exit 1
fi
if [[ ! -e /etc/NIXOS ]]; then
  echo 'This installer requires an installed NixOS system.' >&2
  exit 1
fi
repository=https://github.com/OmarDardery/nixlab.git
revision=main
args=()
while (($#)); do
  case "$1" in
    --repo) repository=$2; shift 2 ;;
    --ref) revision=$2; shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
pin=github:NixOS/nixpkgs/718f670e845328e9c2cecdd832835712b2cbd065
exec nix --extra-experimental-features 'nix-command flakes' shell \
  "$pin#git" "$pin#python3" "$pin#openssl" --command bash -c '
    set -euo pipefail
    repo=$1; ref=$2; shift 2
    checkout=$(mktemp -d)
    trap '\''rm -rf -- "$checkout"'\'' EXIT
    git clone --single-branch --branch "$ref" "$repo" "$checkout/repository"
    nix --extra-experimental-features '\''nix-command flakes'\'' run "path:$checkout/repository#install" -- --checkout "$checkout/repository" --repo "$repo" --ref "$ref" "$@"
  ' nixlab-install "$repository" "$revision" "${args[@]}"
