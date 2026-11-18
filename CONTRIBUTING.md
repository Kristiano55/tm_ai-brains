# Contributing

## Share a brain pack
1. Train in tm_ai until you're happy with the champion.
2. In tm_ai's settings set **GitHub user** (and optionally a token, see below), then run
   `python tm_ai.py publish <run name>` (or `publish base:<name>` for a base driver; add `--wr 41.234` if you know
   the world record). This exports the pack, computes its sha256 and size, and opens two pre-filled pages:
   - a **new release in your fork** of this repo (tag `pack-<id>`): attach the `.tmbrain.zip` it names and publish;
   - a **new file** `packs/<id>.json` in this repo: press "Propose new file", then "Create pull request".
3. The check runs on your pull request (see below). A maintainer merges it; the pack then shows as `unverified`.

With the optional **GitHub token** setting (a fine-grained token with "Contents: read and write" on your fork only),
tm_ai uploads the release asset itself. Without it nothing is stored and nothing is uploaded by tm_ai.

## What the check validates (`tools/validate_pr.py`)
- exactly one new or changed file: `packs/<pack_id>.json` or `verifications/<pack_id>/<you>.json`
- the entry matches `schema/pack.schema.json`, its status is `unverified`, you are its `author_github`
- the zip is in **your own** GitHub releases; its size and sha256 match the entry
- zip safety: only `.json .txt .npz .npy .md`, no path traversal / absolute paths / symlinks, no pickles or
  executables (also checked by content, not only by name), numpy arrays without Python objects, size and
  compression-ratio limits, every file listed in the manifest with its sha256
- the manifest agrees with the entry (pack id, maps, claimed times), the code version and input fingerprint are
  in `compat.json`

## Verifying (people in `verifiers.txt`)
`python tm_ai.py verify <pack_id>` (or the dashboard's "Verify pending packs"): every claimed run's TICK input
file is replayed on your copy of the map (deterministic physics: the time must match exactly), then the pack's
driver drives the map best-of-3 with fixed seeds. The result file (with a digest of its content) opens as a new
file `verifications/<pack_id>/<you>.json`; propose it as a pull request. Only files from listed verifiers count.

## Reporting
Open a "Report a pack" issue (title `Report: <pack_id>`; the tm_ai dashboard has a Report button). The pack is
quarantined until a verifier re-verifies it (a newer verification than the report).
