#!/usr/bin/env bash
# Cut a release, npm-version style:
#   ./release.sh patch|minor|major   # bump from version in meson.build
#   ./release.sh 0.3.0               # or set an explicit version
# Bumps version in meson.build, creates a commit titled "0.3.0" and the tag
# v0.3.0, then pushes main + tag. The tag triggers the Release Flatpak workflow
# (.github/workflows/release.yml), which builds the bundle and publishes the
# GitHub Release.
#
# This version-bump commit is the one sanctioned direct-to-main commit (same
# convention as `npm version` in the JewelBox-Music-Library repo and as the
# Android JewelBox-Music-Player release.sh): it makes each release visible in
# the commit history.
set -euo pipefail
cd "$(dirname "$0")"

ARG="${1:?usage: ./release.sh patch|minor|major|X.Y.Z}"
[[ "$ARG" =~ ^(major|minor|patch|[0-9]+\.[0-9]+\.[0-9]+)$ ]] \
  || { echo "❌ Argument invalide : « $ARG » (attendu patch|minor|major ou X.Y.Z)"; exit 1; }

[[ "$(git branch --show-current)" == "main" ]] || { echo "❌ À lancer depuis main"; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "❌ Working tree non propre — committe ou stash d'abord"; exit 1; }
git pull --ff-only

# Resolve major/minor/patch against the version in meson.build (after the pull,
# so the base version is the freshest one from main).
if [[ "$ARG" =~ ^(major|minor|patch)$ ]]; then
  CUR="$(sed -n "s/^[[:space:]]*version:[[:space:]]*'\([0-9]\+\.[0-9]\+\.[0-9]\+\)'.*/\1/p" meson.build)"
  [[ "$CUR" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]] || { echo "❌ version introuvable ou invalide dans meson.build : « $CUR »"; exit 1; }
  MAJ="${BASH_REMATCH[1]}"; MIN="${BASH_REMATCH[2]}"; PAT="${BASH_REMATCH[3]}"
  case "$ARG" in
    major) V="$((MAJ + 1)).0.0" ;;
    minor) V="$MAJ.$((MIN + 1)).0" ;;
    patch) V="$MAJ.$MIN.$((PAT + 1))" ;;
  esac
  echo "ℹ️  $CUR → $V ($ARG)"
else
  V="$ARG"
fi

if git rev-parse "v$V" >/dev/null 2>&1; then
  echo "❌ Le tag v$V existe déjà"; exit 1
fi

# Bump the project version in meson.build (keeps local Flatpak builds coherent ;
# the CI builds from the git tag anyway).
sed -i "s/^\([[:space:]]*version:[[:space:]]*'\)[0-9]\+\.[0-9]\+\.[0-9]\+'/\1$V'/" meson.build
grep -q "version: '$V'" meson.build || { echo "❌ Échec du bump de version dans meson.build"; exit 1; }

git add meson.build
git commit -m "$V"
git tag "v$V"
git push origin main "v$V"

echo "✅ Release v$V poussée — GitHub Actions construit et publie le bundle Flatpak."
