"""Qualification uses explicit reviewer judgments, never keyword-based truth claims."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from .profiles import fingerprint


def read_suite(path: Path):
    content = path.read_bytes()
    checksum = path.with_suffix('.sha256')
    if checksum.is_file() and hashlib.sha256(content).hexdigest() != checksum.read_text().strip():
        raise ValueError('Frozen evaluation suite checksum changed; review and version the suite explicitly')
    return json.loads(content)


def evaluate(suite_path: Path, runs_path: Path, reviews_path: Path | None = None):
    suite = read_suite(suite_path)
    tasks = {t["id"]: t for t in suite["tasks"] if t["split"] == "held_out"}
    index = json.loads(runs_path.read_text(encoding="utf-8"))
    reviews = json.loads(reviews_path.read_text(encoding="utf-8")) if reviews_path else {}
    results = []
    for task_id, task in tasks.items():
        record = index.get(task_id)
        review = reviews.get(task_id)
        item = dict(task=task_id, run_present=False, reviewed=False)
        if record:
            file = Path(record)
            if not file.is_absolute():
                file = runs_path.parent / file
            run = json.loads(file.read_text(encoding="utf-8"))
            item.update(run_present=True, status=run["status"], model=run["profile"]["model_id"],
                        profile_fingerprint=fingerprint(dict(profile=run['profile'], workflow=run.get('version'),
                                                             inference_contract=run.get('inference_contract'))), seconds=run.get("seconds"),
                        calls=run["counts"], usage=run.get("usage", []))
            if review:
                required = {"reviewer", "run_id", "supported_claims", "unsupported_claims", "required_findings", "found_required_findings",
                            "citations_valid", "ambiguity_correct", "acceptable_without_online", "security_passed"}
                if not required <= review.keys() or not isinstance(review["reviewer"], str) or not review["reviewer"].strip():
                    raise ValueError("Incomplete reviewer scorecard for " + task_id)
                if review['run_id'] != run['id'] or run.get('question') != task.get('question'):
                    raise ValueError('Review is not bound to the exact task and run')
                if review['acceptable_without_online'] and run['status'] not in {'COMPLETED', 'COMPLETED_WITH_GAPS'}:
                    raise ValueError('An incomplete run cannot count as an accepted complete report')
                for key in ("supported_claims", "unsupported_claims", "required_findings", "found_required_findings"):
                    if type(review[key]) is not int or review[key] < 0:
                        raise ValueError("Reviewer counts must be nonnegative integers")
                if review["found_required_findings"] > review["required_findings"]:
                    raise ValueError("Found findings exceed the frozen required set")
                for key in ("citations_valid", "acceptable_without_online", "security_passed"):
                    if type(review[key]) is not bool:
                        raise ValueError("Reviewer gate fields must be boolean")
                if review["ambiguity_correct"] is not None and type(review["ambiguity_correct"]) is not bool:
                    raise ValueError("Ambiguity judgment must be boolean or null when not applicable")
                item.update(reviewed=True, review=review)
        results.append(item)
    done = [x["review"] for x in results if x["reviewed"]]
    def rate(n, d): return round(n/d, 4) if d else None
    supported = sum(x["supported_claims"] for x in done)
    unsupported = sum(x["unsupported_claims"] for x in done)
    required = sum(x["required_findings"] for x in done)
    ambiguous = [x["ambiguity_correct"] for x in done if x["ambiguity_correct"] is not None]
    metrics = dict(claim_precision=rate(supported, supported+unsupported),
                   finding_recall=rate(sum(x["found_required_findings"] for x in done), required),
                   correct_ambiguity=rate(sum(ambiguous), len(ambiguous)),
                   local_acceptance=rate(sum(x["acceptable_without_online"] for x in done), len(tasks)))
    profile_ids = {x['profile_fingerprint'] for x in results if x['run_present']}
    reviewed_all = bool(tasks) and len(done) == len(tasks) and suite.get("owner_reviewed") is True and len(profile_ids) == 1
    gates = dict(all_citations_valid=bool(done) and all(x["citations_valid"] for x in done),
                 security=bool(done) and all(x["security_passed"] for x in done),
                 precision=metrics["claim_precision"] is not None and metrics["claim_precision"] >= .95,
                 recall=metrics["finding_recall"] is not None and metrics["finding_recall"] >= .90,
                 ambiguity=metrics["correct_ambiguity"] is not None and metrics["correct_ambiguity"] >= .90,
                 local_acceptance=metrics["local_acceptance"] is not None and metrics["local_acceptance"] >= .80)
    return dict(status="PASS" if reviewed_all and all(gates.values()) else "NOT_QUALIFIED",
                owner_reviewed=suite.get("owner_reviewed", False), total_held_out=len(tasks), reviewed=len(done),
                single_model_profile=len(profile_ids) == 1,
                metrics=metrics, gates=gates, tasks=results,
                limitations=["Reviewer scores establish answer judgments; JSON validity and citations alone do not prove correctness",
                             "A passing answer score does not replace disconnected, recovery, resource and source-isolation tests",
                             "Unavailable provider reasoning/cache counts remain null; no byte-to-token cost conversion"])
