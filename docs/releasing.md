# Releasing

**TL;DR.** A pushed, annotated tag `vMAJOR.MINOR[-beta[.N]]` that names `headset.json`'s version makes the
release overpressure downloads (`python -m overpressure headset add phoenix`, the window's *Download*): the
source zip and its SHA-256, nothing uploaded by hand. Its image is checked against the
[release checklist](release-checklist.md) first. overpressure's own release is
[its docs/releasing.md](https://github.com/overpressure/overpressure/blob/main/docs/releasing.md).

## Versions

`MAJOR.MINOR[-beta[.N]]`, as a Git tag `vMAJOR.MINOR...`, the same as `version` in
[headset.json](../headset.json). A beta is a pre-release: `headset add phoenix` takes it while there is no stable
release, later only with `--channel beta` or `--version`. `api` in `headset.json` names the overpressure headset interface the release was made for
(`MAJOR.MINOR`): every overpressure that runs it installs the release, also later ones, so a release that needs
a new minor or major comes after the overpressure release that brings it. The image reports this version and commit, and overpressure's version, in
`/usr/share/pico4/version`; a release build's commit carries no `-dirty`.

## A release

1. **Ready.** Everything is on `main`, CI is green (it runs against overpressure's `main`), and no issue the
   release depends on is open.
2. **The version.** Set it in `headset.json`, by pull request like every change to `main`.
3. **The image.** Build from clean checkouts of this repository and overpressure, the whole path of the
   [README](../README.md#install), with the package lock on, in a new build root: a second such build gives the
   same archive and system image ([userspace.md](userspace.md#reproducible-builds)). Run the
   [checklist](release-checklist.md) on that image.
4. **The tag.** An annotated tag on that commit of `main`: its first line the release's title, the rest the
   release's own words, the checklist's results and what is known not to work. Then push it. The first beta of a
   version is `-beta`, the next `-beta.2` (`-beta.1` is refused: it would name the first again).

   ```sh
   git tag -a v1.0-beta -F tag-message.md
   git push origin v1.0-beta
   ```

5. **CI** ([release.yml](../.github/workflows/release.yml), on GitHub):
   - runs every check of [ci.yml](../.github/workflows/ci.yml) on the tagged commit, unless the same tree passed
     already (on its pull request or on `main`);
   - refuses a tag that is not annotated, not on `main`, or names another version than `headset.json`;
   - writes the notes and the zip's `CHANGELOG.md` with overpressure's `scripts/release.py notes`, from the
     conventional commits (git-cliff, [cliff.toml](../cliff.toml)): the changelog starts at the first release,
     whose notes are its tag's own words; a beta's notes list what changed since the release before it, a stable
     release's what changed since the stable release before it (or the first release), its betas included. Both
     are scanned for private data before anything is published;
   - makes the zip with `git archive`: the tagged tree under `phoenix-VERSION/`, with `CHANGELOG.md`, and
     `headset.json`'s commit and date filled in (`export-subst`, [.gitattributes](../.gitattributes)); and its
     SHA-256;
   - creates the GitHub release titled by the tag's first line, with the notes, the zip and its checksum, as a
     pre-release for a beta.

   No image, package or vendor file is attached ([distribution.md](distribution.md)): everything is built on the
   owner's computer or the headset.

A failed check stops the release before anything is published. Fix it on `main`, then delete the tag
(`git push origin :refs/tags/v1.0-beta` and `git tag -d v1.0-beta`) and tag the fixed commit. Only a tag that never
got a release is deleted: a published version never moves.

Between releases `headset.json` keeps the version of the release before; a preview of `main` is told apart by
its commit, which `headset.json` carries in a download (export-subst) and the image reports in
`/usr/share/pico4/version`.

## Moving to new upstream versions

Valve's packages, the kernel and Steam's client hold still between releases
([userspace.md](userspace.md#package-lock)). The weekly upgrade check says what Valve has moved and whether the
image still builds on it ([development.md](development.md#upgrade-check)). To move:

| What | Change | Then |
| --- | --- | --- |
| Holo and Frame packages, SteamVR, Mesa | `build-userspace.sh --refresh --update-lock`, review `OUTPUT_DIR/packages.lock`, copy it to overpressure/packages' `headsets/phoenix/packages.lock`, tag a release there and pin it in `userspace/packages.json` | the whole checklist: `driver_monado`, the patches and masks depend on SteamVR's behaviour |
| PipeWire | in overpressure/packages: `pkgver` of `overpressure-spa-alsa` to the new version, its patch rebased (the build stops otherwise), a new tag; then `userspace/packages.json` pinned to it | suspend with the microphone capturing |
| A project package | in overpressure/packages: the change, CI green, a new tag `YYYY.MM.DD`, whose release takes the packages CI built for the pull request; then `userspace/packages.json` pinned to its tag and commit | the parts of the checklist the package touches |
| Valve's kernel | in overpressure/linux: the Valve workflow's weekly pull request reviewed and merged, and those up the layers after it, a new tag `phoenix/7.2-N` released; then `kernel/release.json` pinned to it (CI downloads it against the pin, and the contracts check the names both sides share) | the whole checklist |
| PICO OS firmware | a new entry in `VERIFIED_FIRMWARES`, only after the whole path has run on it | the whole checklist |
