# tm_ai brains

Shared Trackmania 2020 AI drivers ("brain packs") made with [tm_ai](https://github.com/Kristiano55/tm_ai).
Leaderboard: **https://kristiano55.github.io/tm_ai-brains/** (built from `index.json`).

A brain pack is **data only**: a zip of JSON, TICK input text files and plain numpy arrays (the network weights).
tm_ai never runs, imports or unpickles anything from a pack, and this hub's check rejects any pack that
contains anything else.

| Folder / file | What it is |
|---|---|
| `packs/<pack_id>.json` | one entry per pack (submitted by its author, always `unverified`) |
| `verifications/<pack_id>/<verifier>.json` | verification results (only from people in `verifiers.txt`) |
| `index.json` | built automatically from the two folders above + reports + votes |
| `benchmark.json` | the benchmark maps (UIDs, where to get them, par times) |
| `compat.json` | tm_ai code versions + input layouts the hub accepts |
| `verifiers.txt` | GitHub usernames allowed to verify |
| `tools/` | the checks the GitHub Actions run (`validate_pr.py`, `build_index.py`, `packcheck.py`) |

**Use a pack:** in tm_ai, Hub tab → Import, or `python tm_ai.py hub import <pack_id>`.
**Share a pack:** `python tm_ai.py publish <run>` (see CONTRIBUTING.md).

Status: `unverified` → `verified` / `failed` (by a verifier) · `quarantined` when reported, until re-verified.

## Setting up this repository (maintainer, once)
1. Create the repo `Kristiano55/tm_ai-brains` from this folder (`hub_template/` of tm_ai).
2. Settings → Pages → Source: **GitHub Actions**.
3. Settings → Branches: protect `main`, require the "Check pack submission" check before merging.
4. Create the labels `report` and `vote`.
5. Run the "Build index + leaderboard" workflow once (Actions tab).
