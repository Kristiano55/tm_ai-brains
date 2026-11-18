"""
Validates a pull request to the brain hub. Run by .github/workflows/validate.yml with the TRUSTED main branch in
--base (this script, schema, verifiers.txt, compat.json) and the pull request's files in --head, which are only
READ as data (JSON + the downloaded pack zip; nothing from the PR is executed or imported).

Rules
  * exactly one added/changed file: packs/<pack_id>.json  OR  verifications/<pack_id>/<github user>.json
  * packs/: matches schema/pack.schema.json, status "unverified" (only verifications change it), submitted by the
    entry's author_github (changes to an existing entry only by its author), asset hosted in the author's own
    GitHub releases; the asset is downloaded and must match size + sha256 and pass packcheck (allowed file types,
    no path traversal, no pickles / executables, size limits, manifest hashes), its manifest must match the entry,
    and code version + input fingerprint must be compatible (compat.json)
  * verifications/: PR author is in verifiers.txt and is the file's verifier, the pack exists, sha256 matches the
    pack entry, the digest is intact
Exit code 0 = ok, 1 = rejected (reasons printed, and written to the job summary on GitHub).
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import packcheck as pc  # noqa: E402  (the trusted copy next to this file)

SKIP = {".git", "__pycache__"}          # (running this script creates __pycache__ in the base checkout)


def tree(root):
    root = Path(root)
    out = {}
    for p in root.rglob("*"):
        if p.is_file() and not (set(p.relative_to(root).parts) & SKIP):
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def changed_files(base, head):
    b, h = tree(base), tree(head)
    added = sorted(k for k in h if k not in b)
    modified = sorted(k for k in h if k in b and h[k] != b[k])
    deleted = sorted(k for k in b if k not in h)
    return added, modified, deleted


def version_tuple(v):
    return tuple(int(x) for x in str(v).split("."))


def schema_check(obj, schema_path):
    import jsonschema
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    errs = sorted(jsonschema.Draft202012Validator(schema).iter_errors(obj), key=lambda e: list(e.path))
    return [f"{'/'.join(str(x) for x in e.path) or '(top)'}: {e.message}" for e in errs[:10]]


def fetch_asset(url, asset_dir=None):
    """The pack zip: from --asset-dir (tests) by file name, else downloaded (size-limited)."""
    name = url.rsplit("/", 1)[-1]
    if asset_dir:
        return Path(asset_dir) / name
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    req = urllib.request.Request(url, headers={"User-Agent": "tm_ai-brains-validator"})
    n = 0
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            n += len(b)
            if n > pc.MAX_ZIP_BYTES:
                raise pc.PackError("asset larger than the size limit")
            f.write(b)
    return Path(tmp)


def check_pack_entry(rel, base, head, author, asset_dir, errors, notes):
    pid = rel[len("packs/"):-len(".json")]
    try:
        entry = json.loads((Path(head) / rel).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        errors.append(f"{rel}: not valid JSON ({e})")
        return
    errors += [f"{rel}: {m}" for m in schema_check(entry, Path(base) / "schema" / "pack.schema.json")]
    if errors:
        return
    if entry["pack_id"] != pid:
        errors.append(f"{rel}: pack_id {entry['pack_id']!r} does not match the file name")
    if entry["author_github"].lower() != author.lower():
        errors.append(f"{rel}: submitted by {author}, but author_github is {entry['author_github']}")
    old = Path(base) / rel
    if old.exists():
        prev = json.loads(old.read_text(encoding="utf-8"))
        if prev.get("author_github", "").lower() != author.lower():
            errors.append(f"{rel}: only {prev.get('author_github')} may change this entry")
    owner = entry["asset_url"].split("/")[3]
    if owner.lower() != entry["author_github"].lower():
        errors.append(f"{rel}: the asset must be in the author's own GitHub releases (github.com/{entry['author_github']}/...)")
    compat = json.loads((Path(base) / "compat.json").read_text(encoding="utf-8"))
    if version_tuple(entry["code_version"]) < version_tuple(compat["min_code_version"]):
        errors.append(f"{rel}: code_version {entry['code_version']} is older than {compat['min_code_version']}")
    if entry["input_fingerprint"] not in compat["input_fingerprints"]:
        errors.append(f"{rel}: input fingerprint {entry['input_fingerprint']} is not one the hub accepts")
    if errors:
        return
    try:
        zp = fetch_asset(entry["asset_url"], asset_dir)
        man, files = pc.check_zip(zp, entry["sha256"], entry["size"])
    except (pc.PackError, OSError, ValueError) as e:
        errors.append(f"{rel}: pack rejected: {e}")
        return
    for k in ("pack_id", "kind", "code_version", "input_fingerprint", "base_id"):
        if man.get(k) != entry.get(k):
            errors.append(f"{rel}: {k} differs between the entry ({entry.get(k)!r}) and the pack ({man.get(k)!r})")
    pm = {m["uid"]: m for m in man.get("maps", [])}
    for m in entry["maps"]:
        p = pm.get(m["uid"])
        if p is None:
            errors.append(f"{rel}: map {m['uid']} is not in the pack")
            continue
        for k in ("champion_ms", "best_ms"):
            if m.get(k) != p.get(k):
                errors.append(f"{rel}: {m['uid']} {k} {m.get(k)} differs from the pack's {p.get(k)}")
        for name, run in (p.get("runs") or {}).items():
            if run.get("file") not in files:
                errors.append(f"{rel}: {m['uid']} claimed {name} run has no input file in the pack")
    if len(pm) != len(entry["maps"]):
        errors.append(f"{rel}: the pack has maps the entry does not list")
    notes.append(f"pack {pid}: {len(files)} files, {entry['size']} bytes, sha256 ok, safety checks ok")


def check_verification(rel, base, head, author, errors, notes):
    m = re.match(r"^verifications/([a-z0-9-]+-[0-9a-f]{8})/([A-Za-z0-9-]+)\.json$", rel)
    if not m:
        errors.append(f"{rel}: expected verifications/<pack_id>/<github user>.json")
        return
    pid, user = m.groups()
    verifiers = [l.strip().lower() for l in (Path(base) / "verifiers.txt").read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("#")]
    if author.lower() not in verifiers:
        errors.append(f"{rel}: {author} is not in verifiers.txt; only verifiers can verify packs")
    if user.lower() != author.lower():
        errors.append(f"{rel}: file is for {user}, but the pull request is by {author}")
    try:
        ver = json.loads((Path(head) / rel).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        errors.append(f"{rel}: not valid JSON ({e})")
        return
    errors += [f"{rel}: {x}" for x in schema_check(ver, Path(base) / "schema" / "verification.schema.json")]
    if errors:
        return
    entry_f = Path(base) / "packs" / f"{pid}.json"
    if not entry_f.exists():
        errors.append(f"{rel}: there is no pack {pid}")
        return
    entry = json.loads(entry_f.read_text(encoding="utf-8"))
    if ver["pack_id"] != pid or ver["verifier"].lower() != user.lower():
        errors.append(f"{rel}: pack_id / verifier inside the file do not match its path")
    if ver["sha256"] != entry["sha256"]:
        errors.append(f"{rel}: verified a different file (sha256 does not match the pack entry)")
    if pc.digest({k: v for k, v in ver.items() if k != "signed_off"}) != ver["digest"]:
        errors.append(f"{rel}: digest does not match the content (edited after verification?)")
    if not errors:
        notes.append(f"verification of {pid} by {user}: {ver['verdict']}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--asset-dir", default=None, help="tests: read pack zips from here instead of downloading")
    a = ap.parse_args(argv)
    errors, notes = [], []
    added, modified, deleted = changed_files(a.base, a.head)
    ch = added + modified
    if deleted:
        errors.append("files deleted: " + ", ".join(deleted) + " (maintainers remove packs)")
    if len(ch) != 1:
        errors.append(f"a submission changes exactly one file; this one changes {len(ch)}: " + ", ".join(ch[:10]))
    elif ch[0].startswith("packs/") and re.match(r"^packs/[a-z0-9-]+\.json$", ch[0]):
        check_pack_entry(ch[0], a.base, a.head, a.author, a.asset_dir, errors, notes)
    elif ch[0].startswith("verifications/"):
        check_verification(ch[0], a.base, a.head, a.author, errors, notes)
    else:
        errors.append(f"{ch[0]}: submissions may only add packs/<id>.json or verifications/<id>/<user>.json")
    lines = (["### Brain hub check: " + ("FAILED" if errors else "passed")] + [f"- ❌ {e}" for e in errors]
             + [f"- ✅ {n}" for n in notes])
    print("\n".join(lines))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
