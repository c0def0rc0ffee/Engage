#!/usr/bin/env bash
# <summary>
# Packaging build for the Engage Kodi add-on (service.engage). Builds two
# versioned zips straight from the project root, plus the local run copy:
#   Engage Dist/service.engage-<version>.zip  Kodi installable add-on zip.
#       Kodi's "Install from zip file" needs the add-on folder at the zip
#       root and Kodi names its own zips <addon.id>-<version>.zip, so this
#       one keeps that name rather than the usual <name>-v<version>.zip.
#   Engage Git/engage-v<version>-src.zip  GitHub bound source tree
#       (add-on plus repo housekeeping, minus every local only file).
#   Engage App/  mirror of the Dist zip contents. Copy or symlink
#       'Engage App/service.engage' into Kodi's addons folder to run the
#       current build. Never hand edit it; fix the source and rebuild.
# </summary>
# <remarks>
# This script supersedes build-zip.ps1, which is kept beside it for
# reference only and is no longer run.
#
# Run from anywhere:  ./build-zip.sh
#   --no-bump    : ship the VERSION file verbatim instead of moving the build
#                  segment on. For minor or major releases hand edit VERSION
#                  and addon.xml together, build once with --no-bump, and
#                  later builds resume from there.
#   --skip-tests : emergency only. Packages with the test suite unrun and
#                  prints UNVERIFIED BUILD. Never used to ship around a red
#                  suite. The compile step and the house rules gate still run.
# </remarks>
set -euo pipefail

NO_BUMP=0
SKIP_TESTS=0
for arg in "$@"; do
    case "$arg" in
        --no-bump)    NO_BUMP=1 ;;
        --skip-tests) SKIP_TESTS=1 ;;
        *) echo "Unknown option: $arg (only --no-bump and --skip-tests are supported)" >&2; exit 2 ;;
    esac
done

warn() { echo "WARNING: $*" >&2; }
die()  { echo "ERROR: $*" >&2; exit 1; }

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

command -v zip     >/dev/null || die "zip not found. sudo apt install zip"
command -v unzip   >/dev/null || die "unzip not found. sudo apt install unzip"
command -v rsync   >/dev/null || die "rsync not found. sudo apt install rsync"
command -v python3 >/dev/null || die "python3 not found. The checks in tests/ and tools/ need it"

addon_id='service.engage'
name='engage'
prefix='Engage'
addon_dir="$root/$addon_id"
addon_xml="$addon_dir/addon.xml"
dist_dir="$root/$prefix Dist"
git_dir="$root/$prefix Git"
app_dir="$root/$prefix App"
version_tool="$root/tools/addon_version.py"
checker="$root/tests/check_addon.py"
stage="$(mktemp -d "/tmp/$name-build.XXXXXX")"
trap 'rm -rf "$stage"' EXIT

[[ -d "$addon_dir" ]]    || die "add-on folder $addon_dir is missing"
[[ -f "$version_tool" ]] || die "$version_tool is missing"
[[ -f "$checker" ]]      || die "$checker is missing"

# Version scheme is major.minor.BUILD: every packaged build moves the third
# segment on (1.10.1 to 1.10.2). The bumped number is used throughout the
# build but only written back to VERSION and addon.xml at the very end, once
# every packaging step has succeeded: a failed build must not eat a number.
# Major and minor move only when Rob says so. Edit VERSION and addon.xml by
# hand for those and build once with --no-bump.
version_file="$root/VERSION"
[[ -f "$version_file" ]] || die "VERSION is missing. Create it holding the version addon.xml carries"
on_disk="$(tr -d '[:space:]' < "$version_file")"
[[ "$on_disk" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]] \
    || die "VERSION '$on_disk' is not major.minor.build"

# VERSION is the single source of truth and Kodi reads addon.xml, so the two
# must agree before anything starts. A hand edit to one and not the other is
# the drift this catches.
xml_version="$(python3 "$version_tool" get "$addon_xml")"
[[ "$xml_version" == "$on_disk" ]] \
    || die "addon.xml says $xml_version but VERSION says $on_disk. Fix one by hand, then build"

if [[ $NO_BUMP -eq 1 ]]; then
    version="$on_disk"
    echo "Building v$version (hand set, --no-bump)"
else
    version="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.$((BASH_REMATCH[3] + 1))"
    echo "Building v$version (build segment moved on, recorded at the end)"
fi

# Tests first. A red suite means no zips. Every tests/test_*.py is a
# standalone script that exits non zero on failure.
shopt -s nullglob
suite=("$root"/tests/test_*.py)
shopt -u nullglob
if [[ $SKIP_TESTS -eq 1 ]]; then
    warn "UNVERIFIED BUILD: the test suite was skipped (--skip-tests)"
elif [[ ${#suite[@]} -gt 0 ]]; then
    for test_file in "${suite[@]}"; do
        echo "Running $(basename "$test_file")"
        (cd "$root" && python3 "$test_file") || die "$(basename "$test_file") failed, nothing packaged"
    done
else
    echo "No tests/test_*.py suite, gating on py_compile and the add-on checks"
fi

# Every .py must at least compile. py_compile writes __pycache__ next to the
# source, which both zips exclude below.
while IFS= read -r -d '' py_file; do
    python3 -m py_compile "$py_file" || die "$py_file does not compile"
done < <(find "$addon_dir" "$root/tests" "$root/tools" -name '*.py' -not -path '*/__pycache__/*' -print0)
echo "py_compile: every .py compiles"

# Local-only exclusions are kept in .git/info/exclude, not here and not in
# .gitignore, because both this script and .gitignore ship in the source zip
# and a published file must not carry those names. Read them at build time so
# the zips match what git ignores. No patterns means the protection is gone,
# so the build stops rather than packaging without it.
local_excludes=()
local_patterns=()
if [ -f "$root/.git/info/exclude" ]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        [[ -z "${line// }" || "$line" == \#* ]] && continue
        local_excludes+=(--exclude="$line")
        local_patterns+=("$line")
    done < "$root/.git/info/exclude"
fi
[[ ${#local_patterns[@]} -gt 0 ]] || die "no local exclude patterns found: refusing to package"

# Fail closed. An exclusion that silently stopped working is the whole risk
# here, so check the staged trees rather than trusting the patterns above.
assert_local_excluded() {
    local dir="$1" pat clean
    for pat in "${local_patterns[@]}"; do
        clean="${pat%/}"; clean="${clean#/}"
        if [ -n "$(find "$dir" -name "$clean" -print -quit 2>/dev/null)" ]; then
            die "a locally excluded entry reached the stage ($clean): refusing to package"
        fi
    done
}

# Excluded from BOTH zips. Nothing secret lives in this project (credentials
# are typed into Kodi's add-on settings and stay in Kodi userdata), so the
# credential patterns are a backstop for the day something gets dropped in.
# Both cases are listed because rsync and zip matching is case sensitive.
both_exclude_files=(--exclude='*.zip' --exclude='*.7z' --exclude='*.tmp' --exclude='*.log' --exclude='*.bak'
                    --exclude='*.pyc' --exclude='*.pyo' --exclude='__pycache__/'
                    --exclude='.env' --exclude='.env.*' --exclude='*.pem' --exclude='*.key' --exclude='*.pfx'
                    --exclude='id_rsa*' --exclude='connection.txt' --exclude='*config.local*'
                    --exclude='*credential*' --exclude='*CREDENTIAL*' --exclude='*Credential*'
                    --exclude='*password*' --exclude='*PASSWORD*' --exclude='*Password*'
                    --exclude='*secret*' --exclude='*SECRET*' --exclude='*Secret*'
                    --exclude='*token*' --exclude='*TOKEN*' --exclude='*Token*'
                    --exclude='*cred*' --exclude='*CRED*'
                    --exclude='*sftp*' --exclude='*SFTP*' --exclude='*ftp*' --exclude='*FTP*'
                    --exclude='Thumbs.db' --exclude='desktop.ini' --exclude='.DS_Store')

# Dev only files that sit inside the add-on folder and never ship.
# None exist today. test.py and *_test.py are the names a throwaway check
# script tends to get, so they are dropped on sight.
dev_only_names=('test.py' '*_test.py' 'test_*.py')
# The Dist stage is rooted at the add-on folder, the Git stage at the project
# root, so the same names are anchored differently: the project's own test
# suite in tests/ must never be caught by these patterns.
dev_only_deploy=()
dev_only_src=()
for pattern in "${dev_only_names[@]}"; do
    dev_only_deploy+=(--exclude="$pattern")
    dev_only_src+=(--exclude="/$addon_id/$pattern" --exclude="/$addon_id/**/$pattern")
done

# Never staged into either zip: convention output folders, version control,
# editor and tooling state, generated trees. Every dot folder is dropped
# except .github, matching the .gitignore rule, so the Git zip and the repo
# agree on what ships.
exclude_dirs=(--exclude="$prefix Dist/" --exclude="$prefix Git/" --exclude="$prefix App/"
              --exclude='.git/' --include='/.github/' --exclude='.*/'
              --exclude='$RECYCLE.BIN/' --exclude='@eaDir/'
              --exclude='node_modules/' --exclude='coverage/' --exclude='target/' --exclude='dist/')

# Local only files: release tooling, private notes and Windows era leftovers.
# They are gitignored AND named here, because .gitignore does not feed the
# stage. Kept in one list so the Git zip matches git ls-files.
local_only_files=(--exclude='publish-github.sh' --exclude='push-source.sh'
                  --exclude='publish.conf' --exclude='.publish-allow'
                  --exclude='Github repository' --exclude='GITHUB-RELEASE-GUIDE.md'
                  --exclude='build.conf' --exclude='release-mirror.conf'
                  --exclude='PROJECT_NOTES.md' --exclude='.review/'
                  --exclude='deploy_to_programfiles.ps1' --exclude='deploy_log.txt')

# Stray files: excluded, never deleted, flagged on every build until Rob
# decides what happens to them.
strays=()
for stray in "${strays[@]}"; do
    [[ -e "$root/$stray" ]] && warn "stray file excluded from both zips, decide what to do with it: $stray"
done

mkdir -p "$dist_dir" "$git_dir"

# Zip a staged tree. 'zip -r .' writes forward slashes and an explicit entry
# for every directory, which Linux Kodi needs. The -x is a second line of
# defence behind the rsync exclude: if a .git ever reaches the stage it still
# does not reach the archive. The destination is removed first, so the zip is
# always built fresh and never updated in place (an update over an old
# archive once produced duplicate entries).
make_zip() {
    local source_dir="$1" destination="$2"
    rm -f "$destination"
    (cd "$source_dir" && zip -qr "$destination" . -x '.git/*' '*/.git/*')
}

zip_size_kb() {
    local bytes
    bytes="$(stat -c%s "$1")"
    awk -v b="$bytes" 'BEGIN { printf "%.1f", b / 1024 }'
}

stamp_version() {
    local xml="$1"
    python3 "$version_tool" set "$xml" "$version"
    [[ "$(python3 "$version_tool" get "$xml")" == "$version" ]] \
        || die "could not stamp v$version into $xml"
}

# 1) Dist stage: the add-on folder itself at the zip root, runtime files only,
#    permissions normalised (a 700 stage once reached a docroot and broke a
#    site; Kodi on Linux needs readable directory entries too).
deploy_stage="$stage/deploy"
mkdir -p "$deploy_stage"
rsync -a --chmod=D755,F644 "${local_excludes[@]}" "${both_exclude_files[@]}" "${dev_only_deploy[@]}" \
      "$addon_dir/" "$deploy_stage/$addon_id/"
assert_local_excluded "$deploy_stage"
stamp_version "$deploy_stage/$addon_id/addon.xml"

# 2) Git stage: the full source tree, add-on plus housekeeping, carrying the
#    new number so the src zip matches the version it is named for.
src_stage="$stage/src"
mkdir -p "$src_stage"
rsync -a --chmod=D755,F644 "${local_excludes[@]}" "${both_exclude_files[@]}" "${dev_only_src[@]}" \
      "${exclude_dirs[@]}" "${local_only_files[@]}" "$root/" "$src_stage/"
assert_local_excluded "$src_stage"
printf '%s\n' "$version" > "$src_stage/VERSION"
stamp_version "$src_stage/$addon_id/addon.xml"

# 3) House rules gate over the staged source: addon.xml sanity, every .py
#    compiles, no dash characters, no attribution trailers, no secret looking
#    file names or token shapes, no private addresses. Red means no zips.
python3 "$checker" "$src_stage" || die "add-on checks failed, nothing packaged"

# 4) Dist zip.
dist_zip="$dist_dir/$addon_id-$version.zip"
make_zip "$deploy_stage" "$dist_zip"
echo "Built: $dist_zip ($(zip_size_kb "$dist_zip") KB)"

# 5) Local run copy: exact mirror of the Dist zip contents.
#    --delete removes anything not in the stage. Never hand edit this folder.
mkdir -p "$app_dir"
rsync -a --delete "$deploy_stage/" "$app_dir/"
echo "Mirrored: $app_dir"

# 6) Git zip.
git_zip="$git_dir/$name-v$version-src.zip"
make_zip "$src_stage" "$git_zip"
echo "Built: $git_zip ($(zip_size_kb "$git_zip") KB)"

# 7) Post build verification. Do not trust the staging: read the archives
#    back and refuse to keep a zip that carries a local only file, a secret
#    looking name, version control or a backslash entry.
verify_zip() {
    local archive="$1" listing
    unzip -tq "$archive" >/dev/null || die "$archive fails unzip -t"
    listing="$(unzip -Z1 "$archive")"
    if grep -q '\\' <<< "$listing"; then
        die "$archive stores backslash paths, Kodi refuses those"
    fi
    if grep -Ei 'cred|secret|passw|token|htaccess|(^|/)\.env|(^|/)\.git/|node_modules|__pycache__|\.pyc$' <<< "$listing"; then
        die "$archive carries something that must never ship (listed above)"
    fi
    if grep -E '^(publish-github\.sh|push-source\.sh|publish\.conf|\.publish-allow|Github repository|GITHUB-RELEASE-GUIDE\.md|build\.conf|release-mirror\.conf|PROJECT_NOTES\.md|Packaging\.md|deploy_to_programfiles\.ps1|deploy_log\.txt)$' <<< "$listing"; then
        die "$archive carries a local only file (listed above)"
    fi
    grep -q "^$addon_id/addon.xml$" <<< "$listing" || die "$archive has no $addon_id/addon.xml"
}
verify_zip "$dist_zip"
verify_zip "$git_zip"
echo "Verified: forward slashes, directory entries, no local only or secret looking entries"

# Everything above succeeded, so the bumped number may now be persisted:
# write VERSION and stamp the source addon.xml to match.
if [[ $NO_BUMP -eq 0 ]]; then
    printf '%s\n' "$version" > "$version_file"
    stamp_version "$addon_xml"
    echo "Recorded: VERSION and addon.xml now say $version"
fi
