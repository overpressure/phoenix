# Releasing

**TL;DR.** A pushed, annotated tag that is `headset.json`'s calendar version (`2026.12.0`, a beta `2026.12.0b0`)
makes the release overpressure downloads (`python -m overpressure headset add phoenix`, the window's *Download*): the
source zip and its SHA-256, nothing uploaded by hand. Its image is checked against the
[release checklist](release-checklist.md) first. overpressure's own release is
[its docs/releasing.md](https://github.com/overpressure/overpressure/blob/main/docs/releasing.md).

## Versions

Calendar versions, as overpressure's
([versions across the project](https://github.com/overpressure/overpressure/blob/main/docs/releasing.md#versions-across-the-project)):
a release is `YYYY.M.PATCH` (the month without a leading zero, that month's releases counted from 0), a beta the
coming release with `bN` from `b0`, and `main` between releases the next one as a development version
(`2026.12.0.dev0`); a download of `main` and an image built from it are named by the commit's time
(`2026.12.0.dev202612031154`). The tag is the version itself, the same as `version` in
[headset.json](../headset.json). The releases before the switch (`v1.0-beta` to `v1.0-beta.5`) keep their tags and sort
before every calendar version. A beta is a pre-release: `headset add phoenix` takes it while there is no stable
release, later only with `--channel beta` or `--version`. `api` in `headset.json` names the overpressure headset
interface the release needs (`MAJOR.MINOR`): every overpressure that runs it installs the release, also later ones,
so a release that needs a new minor or major comes after the overpressure release that brings it, and its notes name
that release. The image reports this version and commit, overpressure's version, the kernel's tag and the packages'
release in `/usr/share/pico4/version`; a release build's commit carries no `-dirty`.

## A release

1. **Ready.** Everything is on `main`, CI is green (it runs against overpressure's `main`), and no issue the
   release depends on is open.
2. **The version.** From overpressure's checkout root,
   `python scripts/release.py bump beta --version-file devices/phoenix/headset.json` (or `stable`) writes the
   release's version and prints it: a new beta series takes this month and its first free patch, a running one its
   next `bN`. That change, with the README's *Newest release* row, goes to `main` by pull request like every change.
3. **The image.** Build from clean checkouts of this repository and overpressure, the whole path of the
   [README](../README.md#install), with the package lock on, in a new build root: a second such build gives the
   same archive and system image ([userspace.md](userspace.md#reproducible-builds)). Run the
   [checklist](release-checklist.md) on that image.
4. **The tag.** An annotated tag on that commit of `main`, named exactly as the version: its first line the
   release's title, the rest the release's own words, the checklist's results and what is known not to work. Then
   push it.

   ```sh
   git tag -a 2026.12.0b0 -F tag-message.md
   git push origin 2026.12.0b0
   ```

5. **CI** ([release.yml](../.github/workflows/release.yml), on GitHub):
   - runs every check of [ci.yml](../.github/workflows/ci.yml) on the tagged commit, unless the same tree passed
     already (on its pull request or on `main`);
   - refuses a tag that is not annotated, not on `main`, not a calendar release, or another version than
     `headset.json`'s;
   - writes the notes and the zip's `CHANGELOG.md` with overpressure's `scripts/release.py notes`, from the
     conventional commits (git-cliff, [cliff.toml](../cliff.toml)): the changelog starts at the first release,
     whose notes are its tag's own words; a beta's notes list what changed since the release before it, a stable
     release's what changed since the stable release before it (or the first release), its betas included. Both
     name the contributors: each feature and fix the account that opened its pull request, each release everyone
     whose pull requests it brings (from GitHub, with the workflow's token). The
     notes open with the oldest overpressure release that runs `api` (`notes --api`), and the release stops while
     none does: overpressure is released first. Both are scanned for private data before anything is published;
   - makes the zip with `git archive`: the tagged tree under `phoenix-VERSION/`, with `CHANGELOG.md`, and
     `headset.json`'s commit and date filled in (`export-subst`, [.gitattributes](../.gitattributes)); and its
     SHA-256;
   - creates the GitHub release titled by the tag's first line, with the notes, the zip and its checksum, as a
     pre-release for a beta.
   - then closes the issues the release brings: every open issue whose Stage is Awaiting release gets a comment
     linking the release and is closed as completed (`python scripts/release.py close TAG --repo OWNER/NAME`,
     `--dry-run` lists them; [overpressure's CLAUDE.md](https://github.com/overpressure/overpressure/blob/main/CLAUDE.md#backlog)). A failure there leaves the release published.

   No image, package or vendor file is attached ([distribution.md](distribution.md)): everything is built on the
   owner's computer or the headset.

6. **The next version.** Right after the release,
   `python scripts/release.py bump next --version-file devices/phoenix/headset.json` writes the next development
   version (after `2026.12.0b0`, `2026.12.0b1.dev0`), by pull request: a preview of `main` then says so, with its
   commit's time, in the window and in `/usr/share/pico4/version`, and is never taken for the release.

A failed check stops the release before anything is published. Fix it on `main`, then delete the tag
(`git push origin :refs/tags/2026.12.0b0` and `git tag -d 2026.12.0b0`) and tag the fixed commit. Only a tag that
never got a release is deleted: a published version never moves.

## Moving to new upstream versions

Valve's packages, the kernel and Steam's client hold still between releases
([userspace.md](userspace.md#package-lock)). The weekly upgrade check says what Valve has moved and whether the
image still builds on it ([development.md](development.md#upgrade-check)). To move:

| What | Change | Then |
| --- | --- | --- |
| Holo and Frame packages, SteamVR, Mesa | `build-userspace.sh --refresh --update-lock`, review `OUTPUT_DIR/packages.lock`, copy it to overpressure/packages' `headsets/phoenix/packages.lock`, release it there (*Run workflow*) and pin it in `userspace/packages.json` | the whole checklist: `driver_monado`, the patches and masks depend on SteamVR's behaviour |
| PipeWire | in overpressure/packages: `pkgver` of `overpressure-spa-alsa` to the new version, its patch rebased (the build stops otherwise), a release (*Run workflow*); then `userspace/packages.json` pinned to it | suspend with the microphone capturing |
| A project package | in overpressure/packages: the change, CI green, a release started by hand (*Actions*, *Packages*, *Run workflow* on `main`), which tags `main` with the next date and takes the packages CI built for the pull request; then `userspace/packages.json` pinned to its tag and commit | the parts of the checklist the package touches |
| Valve's kernel | in overpressure/linux: the Valve workflow's weekly pull request reviewed and merged, and those up the layers after it, a new tag `phoenix/7.2-N` released; then `kernel/release.json` pinned to it (CI downloads it against the pin, and the contracts check the names both sides share) | the whole checklist |
| PICO OS firmware | a new entry in `VERIFIED_FIRMWARES`, only after the whole path has run on it | the whole checklist |
