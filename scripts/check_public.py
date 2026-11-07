"""Check that the files headed for a public repo carry no text from a confidential screenplay pack.

    python scripts/check_public.py --pack Input                # the files git would publish
    python scripts/check_public.py --pack Input FILE [FILE...]  # just these files
    python scripts/check_public.py --pack Input --names         # list the names it looks for

It fails (exit code 1) when a file:
- shares a 6-word phrase with the pack;
- quotes a whole spoken line of 4 or more words from it;
- uses one of the pack's names. A name is a capitalised word from a speaker label, an alias or the
  script that the pack never writes in lower case and that is not a format word ("Episode", "INT").

Whole-line quotes of 1-3 words ("Who are you?") are listed as warnings to read by eye: they are
usually ordinary phrases. Nothing is hard-coded about any show; everything comes from --pack.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from promofinder.parse import episode_files, parse_episode  # noqa: E402

N = 6
SKIP_KEYS = {"block_id", "scene_id", "kind", "sha256", "file", "schema_version", "show_id", "warnings",
             "start_tc", "end_tc", "int_ext", "time_of_day", "voice"}
FORMAT_WORDS = {"episode", "scene", "camera", "action", "sound", "start", "end", "int", "ext", "continuous",
                "day", "night", "dusk", "dawn", "morning", "evening", "cut", "fade", "insert", "logline",
                "continuity", "notes", "dialogue", "script", "o.s", "v.o", "off-screen"}
TEXT_EXT = {".py", ".md", ".toml", ".json", ".jsonl", ".html", ".txt", ".csv", ".yml", ".yaml", ".js", ".css", ".cfg"}
QUOTE = re.compile(r"[\"“]([^\"”\n]{1,240})[\"”]")


def _strings(x, out: list[str]) -> None:
    if isinstance(x, str):
        out.append(x)
    elif isinstance(x, dict):
        for k, v in x.items():
            if k not in SKIP_KEYS:
                _strings(v, out)
    elif isinstance(x, list):
        for v in x:
            _strings(v, out)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower())


def _shingles(toks: list[str]) -> set[str]:
    return {" ".join(toks[i:i + N]) for i in range(len(toks) - N + 1)}


def _norm(line: str) -> str:
    return " ".join(_tokens(line))


class Pack:
    def __init__(self, folder: Path):
        episodes = [parse_episode(p, show_id="check") for p in episode_files(folder)]
        if not episodes:
            raise SystemExit(f"no episode files in {folder}")
        texts: list[str] = []
        for ep in episodes:
            _strings(ep, texts)
        self.shingles: set[str] = set()
        for t in texts:
            self.shingles |= _shingles(_tokens(t))
        self.lines = {_norm(b["text"]) for ep in episodes for b in ep["blocks"] if b["kind"] == "DIALOGUE"}
        self.lines.discard("")

        # Names: words from speaker labels and aliases, plus words capitalised mid-sentence in the
        # script, that the script never writes in lower case. Delivery tags ("Cold") are not read.
        body = [b["text"] for ep in episodes for b in ep["blocks"]]
        body += [ep["header"][k] for ep in episodes for k in ("logline", "continuity_notes")]
        lower = {w for t in body for w in re.findall(r"\b[a-z][a-z-]+\b", t)}
        candidates = Counter()
        for ep in episodes:
            for b in ep["blocks"]:
                sp = b.get("speaker") or {}
                for part in (sp.get("label"), sp.get("alias_hint")):
                    for w in re.findall(r"[A-Za-z][A-Za-z-]{2,}", part or ""):
                        candidates[w.lower()] += 2
        for t in body:
            for m in re.finditer(r"(?<![.!?\"“(—–:]\s)(?<!^)\b([A-Z][a-z]{2,}(?:-[A-Z]?[a-z]+)*)(?:'s)?\b", t):
                if m.start() > 0:
                    candidates[m.group(1).lower()] += 1
        self.names = sorted(w for w, n in candidates.items()
                            if n >= 2 and w not in lower and w not in FORMAT_WORDS)


def public_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT,
                         capture_output=True, check=True).stdout.decode("utf-8")
    return [ROOT / p for p in out.split("\0") if p]


def check_file(path: Path, pack: Pack, name_re: re.Pattern) -> tuple[list[str], list[str]]:
    if path.suffix.lower() not in TEXT_EXT or not path.is_file():
        return [], []
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    problems, warnings = [], []
    shared = _shingles(_tokens(text)) & pack.shingles
    if shared:
        problems.append(f"{len(shared)} phrase(s) from the pack, e.g. '{sorted(shared)[0]}'")
    names = Counter(m.group().lower() for m in name_re.finditer(text))
    if names:
        problems.append("names from the pack: " + ", ".join(f"{w} x{n}" for w, n in names.most_common(6)))
    for q in {m.group(1) for m in QUOTE.finditer(text)}:
        line = _norm(q)
        if line in pack.lines:
            (problems if len(line.split()) >= 4 else warnings).append(f'quotes a spoken line: "{q}"')
    return problems, warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pack", required=True, help="Folder with the confidential screenplays.")
    ap.add_argument("--names", action="store_true", help="Print the names it looks for, then stop.")
    ap.add_argument("--allow", default=str(ROOT / "scripts" / "public_allow.txt"),
                    help="File of ordinary words and phrases, one per line, reviewed by hand and not to flag.")
    ap.add_argument("files", nargs="*", help="Files to check (default: what git would publish).")
    args = ap.parse_args(argv)

    pack = Pack(Path(args.pack))
    allow_path = Path(args.allow)
    allow = {_norm(ln) for ln in allow_path.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")} if allow_path.is_file() else set()
    pack.names = [w for w in pack.names if w not in allow]
    pack.lines -= allow
    if args.names:
        print(", ".join(pack.names))
        return 0
    name_re = re.compile(r"\b(" + "|".join(map(re.escape, pack.names)) + r")\b", re.IGNORECASE) \
        if pack.names else re.compile(r"(?!x)x")
    pack_dir = Path(args.pack).resolve()
    files = [Path(f).resolve() for f in args.files] if args.files else public_files()
    pack_files = {p.resolve() for p in episode_files(pack_dir)}
    files = [f for f in files if f not in pack_files]  # the pack itself; a note beside it is still checked

    failed = 0
    for f in sorted(files):
        problems, warnings = check_file(f, pack, name_re)
        rel = f.relative_to(ROOT) if ROOT in f.parents else f
        for p in problems:
            print(f"FAIL {rel}: {p}")
        for w in warnings:
            print(f"warn {rel}: {w}")
        failed += bool(problems)
    print(f"\n{len(files)} files checked against {len(pack.lines)} spoken lines and {len(pack.names)} names: "
          f"{failed} file(s) fail.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
