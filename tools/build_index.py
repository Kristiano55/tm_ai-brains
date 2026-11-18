"""
Builds index.json and the GitHub Pages leaderboard from the hub repository (run by .github/workflows/build.yml).

status per pack (only this script sets it, never a submitter):
  unverified   no verification yet
  verified     the newest verification by someone in verifiers.txt says "verified" (and its sha256 matches)
  failed       the newest such verification says "failed"
  quarantined  an open issue labelled "report" with title "Report: <pack_id>" newer than that verification
votes = 👍 reactions on an issue labelled "vote" titled "Vote: <pack_id>".
Issues come from the GitHub API (GITHUB_TOKEN + GITHUB_REPOSITORY) or, for tests, from --issues FILE.
"""
import argparse
import html
import json
import os
import shutil
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import packcheck as pc  # noqa: E402


def load_issues(path=None):
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (tok and repo):
        return []
    out = []
    for page in range(1, 11):
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/issues?state=open&per_page=100&page={page}",
                                     headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            batch = json.loads(r.read().decode())
        out += batch
        if len(batch) < 100:
            break
    return out


def _ts(issue):
    try:
        return datetime.strptime(issue["created_at"], "%Y-%m-%dT%H:%M:%SZ").timestamp()
    except (KeyError, ValueError):
        return time.time()


def build(repo, issues):
    repo = Path(repo)
    verifiers = [l.strip() for l in (repo / "verifiers.txt").read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("#")]
    vl = {v.lower() for v in verifiers}
    labels = lambda i: {lb["name"] if isinstance(lb, dict) else lb for lb in i.get("labels", [])}  # noqa: E731
    reports, votes = {}, {}
    for i in issues:
        t = (i.get("title") or "").strip()
        if "report" in labels(i) and t.lower().startswith("report:"):
            pid = t.split(":", 1)[1].strip().split()[0] if t.split(":", 1)[1].strip() else ""
            reports.setdefault(pid, []).append(_ts(i))
        if "vote" in labels(i) and t.lower().startswith("vote:"):
            pid = t.split(":", 1)[1].strip().split()[0] if t.split(":", 1)[1].strip() else ""
            votes[pid] = votes.get(pid, 0) + int((i.get("reactions") or {}).get("+1", 0))
    packs = []
    for f in sorted((repo / "packs").glob("*.json")):
        e = json.loads(f.read_text(encoding="utf-8"))
        e.update(status="unverified", verifier=None, verified_at=None, benchmark_score=None,
                 votes=votes.get(e["pack_id"], 0))
        vers = []
        for vf in (repo / "verifications" / e["pack_id"]).glob("*.json"):
            try:
                v = json.loads(vf.read_text(encoding="utf-8"))
            except ValueError:
                continue
            ok = (vf.stem.lower() in vl and v.get("verifier", "").lower() == vf.stem.lower()
                  and v.get("sha256") == e["sha256"]
                  and pc.digest({k: x for k, x in v.items() if k != "signed_off"}) == v.get("digest"))
            if ok:
                vers.append(v)
        if vers:
            v = max(vers, key=lambda x: x.get("created", 0))
            e.update(status=v["verdict"], verifier=v["verifier"], verified_at=v["created"])
            vm = {m["uid"]: m for m in v.get("maps", [])}
            for m in e["maps"]:
                r = vm.get(m["uid"]) or {}
                m["verified"] = {"drive_ms": r.get("drive_ms"),
                                 "runs_match": all(x.get("match") for x in (r.get("runs") or {}).values()) if r.get("runs") else None}
            if v.get("benchmark"):
                e["benchmark_score"] = v["benchmark"].get("score")
        last_ok = e["verified_at"] or 0
        if any(t > last_ok for t in reports.get(e["pack_id"], [])):
            e["status"] = "quarantined"
        packs.append(e)
    return {"generated": round(time.time()), "packs": packs, "verifiers": verifiers, "benchmark_url": "benchmark.json",
            "compat": json.loads((repo / "compat.json").read_text(encoding="utf-8"))}


def leaderboard(idx):
    """Static page: per map the best verified packs, per base the best benchmark score (no scripts)."""
    esc = html.escape
    fmt = lambda ms: f"{ms / 1000:.3f}" if ms else "–"  # noqa: E731
    by_map = {}
    for p in idx["packs"]:
        for m in p["maps"]:
            by_map.setdefault(m["uid"], {"name": m.get("name") or m["uid"], "rows": []})["rows"].append((p, m))
    parts = []
    for uid, d in sorted(by_map.items(), key=lambda kv: kv[1]["name"].lower()):
        rows = [(p, m) for p, m in d["rows"] if p["status"] == "verified" and (m.get("verified") or {}).get("drive_ms")]
        rows.sort(key=lambda pm: pm[1]["verified"]["drive_ms"])
        wr = next((m.get("wr_ms") for _, m in d["rows"] if m.get("wr_ms")), None)
        body = "".join(f"<tr><td>{i + 1}</td><td><code>{esc(p['pack_id'])}</code></td><td>{esc(p['author'])}</td>"
                       f"<td>{fmt(m['verified']['drive_ms'])}</td><td>{fmt(m.get('champion_ms'))}</td>"
                       f"<td>{('+%.3f' % ((m['verified']['drive_ms'] - wr) / 1000)) if wr else '–'}</td>"
                       f"<td>{esc(p.get('verifier') or '')}</td><td>{p.get('votes', 0)}</td></tr>"
                       for i, (p, m) in enumerate(rows[:20]))
        pending = sum(1 for p, _ in d["rows"] if p["status"] != "verified")
        parts.append(f"<h2>{esc(d['name'])} <small>{esc(uid)} · WR {fmt(wr)}</small></h2>"
                     + (f"<table><tr><th>#</th><th>Pack</th><th>Author</th><th>Verified drive</th><th>Claimed champion</th>"
                        f"<th>vs WR</th><th>Verifier</th><th>Votes</th></tr>{body}</table>" if rows else
                        "<p class=m>no verified pack yet</p>")
                     + (f"<p class=m>{pending} more pack(s) not verified (yet)</p>" if pending else ""))
    bases = {}
    for p in idx["packs"]:
        if p.get("benchmark_score") is not None and p["status"] == "verified":
            for b in p.get("lineage") or [p.get("base_id")]:
                if b and (b not in bases or p["benchmark_score"] > bases[b][0]):
                    bases[b] = (p["benchmark_score"], p)
    brow = "".join(f"<tr><td><code>{esc(b)}</code></td><td>{s}</td><td><code>{esc(p['pack_id'])}</code></td><td>{esc(p['author'])}</td></tr>"
                   for b, (s, p) in sorted(bases.items(), key=lambda kv: -kv[1][0]))
    when = datetime.utcfromtimestamp(idx["generated"]).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>tm_ai brains</title><style>
:root{{color-scheme:dark}}body{{margin:0;padding:16px;background:#0e1013;color:#e8eaed;font:14px/1.45 "Segoe UI",system-ui,sans-serif;max-width:1100px}}
h1{{font-size:22px}}h2{{font-size:16px;margin:26px 0 8px}}small,.m{{color:#8b93a0;font-weight:400}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th{{text-align:left;color:#8b93a0;font-weight:400;border-bottom:1px solid #262b32;padding:4px 8px}}
td{{padding:5px 8px;border-bottom:1px solid #1d2127}}code{{font-size:12px}}a{{color:#6fb1ff}}
@media(max-width:700px){{table{{display:block;overflow-x:auto}}}}</style></head><body>
<h1>tm_ai brains <small>shared Trackmania 2020 AI drivers · updated {when}</small></h1>
<p class=m>Only packs verified by a hub verifier are ranked: their claimed runs replayed to the exact time and the driver
itself drove the map (best of 3, fixed seeds). <a href="index.json">index.json</a> · <a href="benchmark.json">benchmark maps</a></p>
{''.join(parts) or '<p class=m>no packs yet</p>'}
<h2>Base drivers <small>best verified benchmark score per lineage</small></h2>
{('<table><tr><th>Base id</th><th>Score</th><th>Pack</th><th>Author</th></tr>' + brow + '</table>') if brow else '<p class=m>no benchmark scores yet</p>'}
</body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default="site")
    ap.add_argument("--issues", default=None)
    a = ap.parse_args(argv)
    idx = build(a.repo, load_issues(a.issues))
    (Path(a.repo) / "index.json").write_text(json.dumps(idx, indent=1), encoding="utf-8")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    shutil.copyfile(Path(a.repo) / "benchmark.json", out / "benchmark.json")
    (out / "index.html").write_text(leaderboard(idx), encoding="utf-8")
    print(f"index: {len(idx['packs'])} packs, " + ", ".join(f"{s}: {sum(1 for p in idx['packs'] if p['status'] == s)}"
                                                         for s in ("verified", "unverified", "failed", "quarantined")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
