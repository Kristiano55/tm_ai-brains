<!-- One pull request = ONE file: packs/<pack_id>.json (a new pack) or verifications/<pack_id>/<you>.json (verifiers). -->
## Pack

- Pack id:
- Map(s) (name + UID):
- Claimed champion / best time(s):
- PB / WR time (if known):
- Trained from (base id / parent pack, if any):

## Checklist

- [ ] Made with `python tm_ai.py publish ...` (the entry file was generated, not hand-written)
- [ ] The pack zip is attached to a release in **my own** fork, at the URL in `asset_url`
- [ ] `status` is `unverified` (verifiers set the status, the check rejects anything else)
- [ ] The claimed runs are real: anyone can replay the included TICK input files to the exact time
