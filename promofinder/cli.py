"""Command line: python -m promofinder <command> ..."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import __version__
from .compare import compare_markdown, compare_runs
from .config import resolve
from .evaluate import evaluate_run, evaluation_markdown
from .llm import LLMError, LLMUnavailable
from .pipeline import parse_folder, parse_headline, run
from .schema import validate_episode


def cmd_parse(args: argparse.Namespace) -> int:
    in_dir = Path(args.input)
    if not any(in_dir.glob("*.txt")):
        print(f"No .txt files found in {in_dir}", file=sys.stderr)
        return 2
    _, report = parse_folder(in_dir, args.out, args.show_id)
    print(parse_headline(report))
    for w in report["warnings"]:
        print(f"  warning  E{w['episode']:03d} line {w['line']}: {w['code']}: {w['message']}")
    for e in report["errors"]:
        print(f"  ERROR    {e['error']}")
    print(f"Wrote {report['episodes_parsed']} episode files and parse_report.md/.json to {args.out}")
    return 1 if report["errors"] else 0


def cmd_validate(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    files = sorted(folder.glob("E*.json"))
    if not files:
        print(f"No E*.json files found in {folder}", file=sys.stderr)
        return 2
    bad = 0
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            bad += 1
            print(f"{f.name}: not valid JSON ({exc})")
            continue
        problems = validate_episode(doc)
        if problems:
            bad += 1
            print(f"{f.name}: {len(problems)} problem(s)")
            for p in problems[:10]:
                print(f"  - {p}")
    print(f"{len(files) - bad}/{len(files)} files are valid")
    return 1 if bad else 0


def episode_set(text: str | None) -> set[int] | None:
    """'1-5,12' -> {1, 2, 3, 4, 5, 12}."""
    if not text:
        return None
    out: set[int] = set()
    for part in text.split(","):
        lo, _, hi = part.strip().partition("-")
        out.update(range(int(lo), int(hi or lo) + 1))
    return out


def cmd_run(args: argparse.Namespace) -> int:
    in_dir = Path(args.input)
    if not any(in_dir.glob("*.txt")):
        print(f"No .txt files found in {in_dir}", file=sys.stderr)
        return 2
    out = Path(args.out or f"outputs/run_{re.sub(r'[^A-Za-z0-9.-]+', '_', args.judge)}")
    try:
        meta = run(in_dir, out, judge_spec=args.judge, config_path=args.config, show_id=args.show_id,
                   episodes_only=episode_set(args.episodes), progress=lambda line: print(line, flush=True))
    except (ValueError, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        if isinstance(exc, LLMUnavailable):
            print("Answers received so far are cached: run the same command again to resume.", file=sys.stderr)
        return 2
    print(f"Parsed {meta['episodes']} episodes ({meta['parse_errors']} errors, {meta['parse_warnings']} warnings)")
    print(f"Scored {meta['windows_scored']:,} legal windows -> {meta['candidates']} candidates "
          f"| judge: {meta['judge_label']}")
    if "llm" in meta:
        llm, cost = meta["llm"], meta["cost"]
        print(f"Model calls: {cost['llm_calls']} ({cost['cache_hits']} from cache, "
              f"{len(llm['episodes_failed'])} failed) | {cost['input_tokens']:,} in / {cost['output_tokens']:,} out "
              f"tokens | {cost['model_seconds_per_episode']} s of model time per episode")
        print(f"Model proposals: {llm['proposals']['made']} made, {llm['proposals']['built']} built into windows")
    print(f"Hard checks: {meta['candidates_passing_hard_checks']}/{meta['candidates']} candidates pass")
    print(f"Ranked {meta['promos']} promos from {meta['episodes_with_promos']} episodes "
          f"({meta['duplicates_folded']} near-duplicates folded)")
    promos = json.loads((out / "promos.json").read_text(encoding="utf-8"))
    print(f"Top {min(meta['top_n'], len(promos))}:")
    for p in promos[: meta["top_n"]]:
        seg = " + ".join(f"Ep{s['episode']:02d} {s['start']}-{s['end']}" for s in p["segments"])
        hook = p["hook"]["text"]
        print(f"  {p['rank']:>2}. {seg} ({p['duration_s']:.1f}s) final {p['scores']['final']:.2f}  "
              f"hook: {hook[:70] + ('...' if len(hook) > 70 else '')}")
    print(f"Wrote {out}: promos_ranked.md, promos.json, promos.csv, candidates.json, signals.json, "
          f"hard_checks.json, run_meta.json, parsed/")
    return 0 if meta["parse_errors"] == 0 else 1


def cmd_compare(args: argparse.Namespace) -> int:
    for folder in (args.run_a, args.run_b):
        if not (Path(folder) / "promos.json").exists():
            print(f"{folder} is not a run folder (no promos.json)", file=sys.stderr)
            return 2
    report = compare_markdown(compare_runs(args.run_a, args.run_b, top_n=args.top))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(report)
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    if not (Path(args.run) / "promos.json").exists():
        print(f"{args.run} is not a run folder (no promos.json)", file=sys.stderr)
        return 2
    report = evaluation_markdown(evaluate_run(args.run, args.gold))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(report)
    return 0


def cmd_qc(args: argparse.Namespace) -> int:
    from .config import load_config
    from .llm import get_client
    from .qc import qc_markdown, run_blind_test, run_naive_baseline

    run_dir = Path(args.run)
    if not (run_dir / "promos.json").exists():
        print(f"{args.run} is not a run folder (no promos.json)", file=sys.stderr)
        return 2
    cfg = load_config(args.config)
    try:
        if args.blind:
            print(f"Blind-viewer test with {args.blind}:", flush=True)
            run_blind_test(run_dir, get_client(args.blind, cfg), cfg, progress=lambda line: print(line, flush=True))
        if args.naive:
            print(f"Naive one-prompt baseline with {args.naive} (one long call)...", flush=True)
            long_reply = {**cfg, "llm": {**cfg["llm"], "max_output_tokens": cfg["qc"]["naive_max_output_tokens"]}}
            n = run_naive_baseline(run_dir, args.input, get_client(args.naive, long_reply), cfg, args.naive_episodes)
            broken = sum(bool(p["problems"]) for p in n["promos"])
            print(n["error"] or f"{len(n['promos'])} promos, {broken} break at least one rule")
    except (ValueError, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        if isinstance(exc, LLMUnavailable):
            print("Answers received so far are cached: run the same command again to resume.", file=sys.stderr)
        return 2
    report = qc_markdown(run_dir, cfg, gold_path=args.gold, compare=args.compare, review_path=args.review)
    (run_dir / "qc_report.md").write_text(report, encoding="utf-8")
    print(f"Wrote {run_dir / 'qc_report.md'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        prog="promofinder", description="Find promo clips in timestamped microdrama screenplays."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="Find promos end to end and write a run folder.")
    r.add_argument("--input", required=True, help="Folder with one .txt file per episode.")
    r.add_argument("--out", help="Run folder (default: outputs/run_<judge>).")
    r.add_argument("--judge", default="offline",
                   help="'offline' (rules only, no model) or 'ollama:<model>', e.g. ollama:qwen3.5:4b.")
    r.add_argument("--episodes", help="Judge only these episodes, e.g. '1-5,12' (quick trials).")
    r.add_argument("--config", help="TOML file whose values override config/default.toml.")
    r.add_argument("--show-id", help="Show slug (default: the input folder name).")
    r.set_defaults(func=cmd_run)

    p = sub.add_parser("parse", help="Parse screenplay .txt files into schema-valid JSON.")
    p.add_argument("--input", required=True, help="Folder with one .txt file per episode.")
    p.add_argument("--out", default="outputs/parsed", help="Output folder (default: outputs/parsed).")
    p.add_argument("--show-id", help="Show slug stored in every episode (default: the input folder name).")
    p.set_defaults(func=cmd_parse)

    c = sub.add_parser("compare", help="Compare two run folders: agreement between judges, review queue.")
    c.add_argument("run_a", help="First run folder.")
    c.add_argument("run_b", help="Second run folder.")
    c.add_argument("--top", type=int, default=10, help="Size of the top list to compare (default 10).")
    c.add_argument("--out", help="Write the report here (Markdown) instead of printing it.")
    c.set_defaults(func=cmd_compare)

    e = sub.add_parser("evaluate", help="Score a run folder against the gold set and the silver labels.")
    e.add_argument("run", help="Run folder.")
    e.add_argument("--gold", default=str(resolve("eval/gold_moments.toml")), help="Gold set (TOML).")
    e.add_argument("--out", help="Write the report here (Markdown) instead of printing it.")
    e.set_defaults(func=cmd_evaluate)

    q = sub.add_parser("qc", help="Quality checks for a run folder: writes qc_report.md (PLAN.md §7).")
    q.add_argument("run", help="Run folder.")
    q.add_argument("--blind", help="Run the blind-viewer test with this model, e.g. ollama:gemma3:4b.")
    q.add_argument("--naive", help="Run the naive one-prompt baseline with this model, e.g. ollama:qwen3.5:4b.")
    q.add_argument("--input", default="Input", help="Screenplay folder for the naive baseline (default: Input).")
    q.add_argument("--naive-episodes", help="Episodes pasted into the naive prompt (default from config: 1-3).")
    q.add_argument("--compare", action="append", default=[],
                   help="Another run folder to compare with (repeatable): rules only, another model, an older run.")
    q.add_argument("--gold", default=str(resolve("eval/gold_moments.toml")), help="Gold set (TOML).")
    q.add_argument("--review", default=str(resolve("eval/top10_review.toml")),
                   help="Editor verdicts on the top 10 (TOML), if any.")
    q.add_argument("--config", help="TOML file whose values override config/default.toml.")
    q.set_defaults(func=cmd_qc)

    v = sub.add_parser("validate", help="Check parsed E###.json files against the schema.")
    v.add_argument("folder", help="Folder that contains E###.json files.")
    v.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    return args.func(args)
