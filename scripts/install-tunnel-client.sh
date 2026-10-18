#!/usr/bin/env bash
# Install the latest official OpenAI tunnel-client for Windows Git Bash.
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python="$root/.venv/Scripts/python.exe"
target_dir="$root/.tools/tunnel-client"
target="$target_dir/tunnel-client.exe"
release_api="${TUNNEL_CLIENT_RELEASE_API:-https://api.github.com/repos/openai/tunnel-client/releases/latest}"

if [[ ! -f "$python" ]]; then
  printf 'Python virtual environment is missing. Run first:\n' >&2
  printf 'source scripts/setup-real-agents.sh codex\n' >&2
  exit 1
fi
for command_name in curl unzip sha256sum; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'Required Git Bash command is missing: %s\n' "$command_name" >&2
    exit 1
  fi
done

case "$(uname -m)" in
  x86_64|amd64) platform="windows-amd64" ;;
  arm64|aarch64) platform="windows-arm64" ;;
  *)
    printf 'Unsupported Windows architecture: %s\n' "$(uname -m)" >&2
    exit 1
    ;;
esac

tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "$tmp_dir"' EXIT

printf 'Finding the latest official OpenAI tunnel-client release...\n'
curl --fail --location --silent --show-error --retry 3 \
  -H 'Accept: application/vnd.github+json' \
  -o "$tmp_dir/release.json" "$release_api"

mapfile -t release_info < <("$python" - "$tmp_dir/release.json" "$platform" <<'PY' | tr -d '\r'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    release = json.load(handle)
platform = re.escape(sys.argv[2])
pattern = re.compile(rf"^tunnel-client-v[^/]+-{platform}\.zip$")
assets = {asset["name"]: asset["browser_download_url"] for asset in release["assets"]}
matches = [(name, url) for name, url in assets.items() if pattern.match(name)]
if len(matches) != 1 or "SHA256SUMS.txt" not in assets:
    raise SystemExit("Official release does not contain the expected Windows archive/checksum")
print(release["tag_name"])
print(matches[0][0])
print(matches[0][1])
print(assets["SHA256SUMS.txt"])
PY
)

version="${release_info[0]}"
archive_name="${release_info[1]}"
archive_url="${release_info[2]}"
checksums_url="${release_info[3]}"

printf 'Downloading tunnel-client %s for %s...\n' "$version" "$platform"
curl --fail --location --silent --show-error --retry 3 \
  -o "$tmp_dir/$archive_name" "$archive_url"
curl --fail --location --silent --show-error --retry 3 \
  -o "$tmp_dir/SHA256SUMS.txt" "$checksums_url"

expected="$(awk -v file="$archive_name" '$2 == file || $2 == "*" file {print tolower($1); exit}' "$tmp_dir/SHA256SUMS.txt")"
actual="$(sha256sum "$tmp_dir/$archive_name" | awk '{print tolower($1)}')"
if [[ -z "$expected" || "$actual" != "$expected" ]]; then
  printf 'SHA256 verification failed for %s; nothing was installed.\n' "$archive_name" >&2
  exit 1
fi

unzip -q "$tmp_dir/$archive_name" -d "$tmp_dir/unpacked"
executable="$(find "$tmp_dir/unpacked" -type f -name tunnel-client.exe -print -quit)"
if [[ -z "$executable" ]]; then
  printf 'Downloaded archive does not contain tunnel-client.exe.\n' >&2
  exit 1
fi

mkdir -p "$target_dir"
cp -- "$executable" "$target"
printf 'Installed and verified: %s\n' "$target"
"$target" --version
