"""Operator CLI for offline profiles, discovery, review exports and qualification."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..engine import Config
from . import profiles
from .runtime import LocalRuntime
from .transport import StdioTools
from .workflow import Discovery, export_escalation
from .resources import InferenceLock


def add_commands(commands):
    models = commands.add_parser("models", help="Manage explicit, pinned local reasoning models; never download automatically")
    subs = models.add_subparsers(dest="model_command", required=True)
    subs.add_parser("list")
    verify = subs.add_parser("verify")
    verify.add_argument("profile")
    add = subs.add_parser("add")
    add.add_argument("profile")
    for flag in ("model", "runtime"):
        add.add_argument("--"+flag, type=Path, required=True)
    for flag in ("model-id", "revision", "runtime-version", "license"):
        add.add_argument("--"+flag, required=True)
    add.add_argument("--tier", choices=tuple(profiles.TIERS), default="laptop")
    add.add_argument("--quantization", default="Q4_K_M")
    discover = commands.add_parser("discover", help="Produce a local-model discovery report over a completed MCP analysis")
    discover.add_argument("application")
    discover.add_argument("--analysis", help="Explicit analysis ID; default resolves latest completed once")
    discover.add_argument("--profile", required=True)
    discover.add_argument("--question", default="")
    discover.add_argument("--resume", help="Resume a run ID with identical scope, question and model")
    export = commands.add_parser("escalation-export", help="Export reviewed metadata for manual online assistance; never call a provider")
    export.add_argument("run")
    export.add_argument("--question", required=True)
    export.add_argument("--refs", nargs="+", required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--approve", action="store_true", help="Approve this exact selected question/evidence packet for export")
    ev = commands.add_parser("evaluate-offline", help="Evaluate saved local runs against explicit reviewer scorecards")
    ev.add_argument("--suite", type=Path, required=True)
    ev.add_argument("--runs", type=Path, required=True)
    ev.add_argument("--reviews", type=Path)


def execute(args):
    config = Config.load(False)
    state = args.state.resolve() if args.state else config.state
    if args.command == "models":
        if args.model_command == "list":
            result = dict(candidates=profiles.CANDIDATES, installed=profiles.installed(state),
                          note="Candidates are not benchmark winners; models require explicit offline provisioning")
        elif args.model_command == "verify":
            value = profiles.load(state, args.profile)
            result = dict(status="FILES_VERIFIED", model=value["model_id"], revision=value["revision"],
                          fingerprint=profiles.fingerprint(value), qualification="NOT_A_QUALITY_OR_RUNTIME_COMPATIBILITY_TEST")
        else:
            result = profiles.install(state, args.profile, model=args.model, runtime=args.runtime,
                                      model_id=args.model_id, revision=args.revision, runtime_version=args.runtime_version,
                                      license_name=args.license, tier=args.tier, quantization=args.quantization)
        print(json.dumps(result, indent=2))
        return
    if args.command == "evaluate-offline":
        from .evaluation import evaluate
        print(json.dumps(evaluate(args.suite, args.runs, args.reviews), indent=2))
        return
    if args.command == "escalation-export":
        # Operator must also be using the intended explicit project.
        run_path = state / "offline-runs" / args.run / "run.json"
        import re
        if not re.fullmatch(r"[0-9a-f]{32}", args.run):
            raise ValueError("Invalid run identifier")
        saved = json.loads(run_path.read_text(encoding="utf-8"))
        if saved["project_id"] != args.project:
            raise PermissionError("Select the run's authorized project explicitly")
        result = export_escalation(state, args.run, args.question, args.refs, args.output, args.approve)
        print(json.dumps(dict(status=result["status"], output=str(args.output), provider_called=False), indent=2))
        return
    profile = profiles.load(state, args.profile)

    async def run():
        roots = tuple(args.root) if args.root else config.roots
        async with StdioTools(state, args.project, roots) as tools:
            async with LocalRuntime(profile) as runtime:
                return await Discovery(tools, runtime, profile, state, args.project).run_report(
                    args.application, args.analysis, args.question, args.resume)

    with InferenceLock(state):
        result, folder = asyncio.run(run())
    print(json.dumps(dict(status=result["status"], run_id=result["id"], report=str(folder / "report.md"),
                          structured_report=str(folder / "run.json"), gaps=result["gaps"],
                          qualification="UNQUALIFIED", online_model_called=False), indent=2))
    if result["status"] in {"PARTIAL", "INTERRUPTED"}:
        raise SystemExit(2)
