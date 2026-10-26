#!/usr/bin/env bash
# Move an existing server checkout from ./source into the Compose directory.
set -euo pipefail

root=${CURRENT_MUSIC_SERVER_ROOT:-"$HOME/docker/current-music"}
root=$(cd "$root" && pwd -P)
source_dir="$root/source"

die() { printf 'Setup stopped: %s\n' "$*" >&2; exit 1; }

[[ -d "$source_dir/.git" ]] || die "no Git checkout at $source_dir"
[[ ! -e "$root/.git" && ! -L "$root/.git" ]] || die "$root is already a Git checkout"
[[ -f "$root/docker-compose.yaml" && -f "$root/.env" ]] || die 'docker-compose.yaml or .env is missing'
[[ ! -e "$root/.current-music-local.yaml" ]] || die '.current-music-local.yaml already exists; it was left untouched'
[[ $(git -C "$source_dir" branch --show-current) == main ]] || die 'source is not on main'
git -C "$source_dir" diff --quiet || die 'source has local file changes'
git -C "$source_dir" diff --cached --quiet || die 'source has staged changes'

# These paths hold local configuration or data. A clone should not contain them.
for name in .env docker-compose.yaml data music current_data; do
  [[ ! -e "$source_dir/$name" && ! -L "$source_dir/$name" ]] || die "source/$name exists; local data needs review"
done

shopt -s dotglob nullglob
entries=("$source_dir"/*)
(( ${#entries[@]} > 0 )) || die 'source is empty'
backup=$(mktemp -d "$root/../current-music-old-files-XXXXXX")

# Keep every parent file that would otherwise be replaced, outside the build context.
for entry in "${entries[@]}"; do
  name=${entry##*/}
  if [[ -e "$root/$name" || -L "$root/$name" ]]; then
    mv -- "$root/$name" "$backup/$name"
  fi
done

mv -- "${entries[@]}" "$root/"
rmdir "$source_dir"

cat > "$root/.current-music-local.yaml" <<'YAML'
services:
  current:
    image: current-music:local
    build: .
    pull_policy: build
YAML

if grep -q '^COMPOSE_FILE=' "$root/.env"; then
  sed -i 's|^COMPOSE_FILE=.*|COMPOSE_FILE=docker-compose.yaml:.current-music-local.yaml|' "$root/.env"
else
  printf '\nCOMPOSE_FILE=docker-compose.yaml:.current-music-local.yaml\n' >> "$root/.env"
fi

cd "$root"
docker compose config --quiet
docker compose config --images | grep -Fxq 'current-music:local' || die 'local image override did not apply'
[[ $(git rev-parse --show-toplevel) == "$root" ]] || die 'Git checkout was not established in the parent directory'

# Override any old shell shortcuts that still point Git at ./source. Docker keeps
# using the existing local Compose file, with the new build context layered on.
profile=${CURRENT_MUSIC_SHELL_PROFILE:-"$HOME/.bashrc"}
touch "$profile"
if ! grep -Fq '# current-music checkout in parent directory' "$profile"; then
  cat >> "$profile" <<'BASH'

# current-music checkout in parent directory
git() { command git "$@"; }
docker() {
  if [[ $PWD == "$HOME/docker/current-music" && ${1:-} == compose ]]; then
    shift
    sudo docker compose -f "$PWD/docker-compose.yaml" -f "$PWD/.current-music-local.yaml" "$@"
  else
    command docker "$@"
  fi
}
BASH
fi

printf 'Checkout is now %s\nOld overlapping files are saved at %s\nRun exec bash once, then use git pull and docker compose up -d --build.\n' "$root" "$backup"
