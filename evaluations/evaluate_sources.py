"""Engineering benchmark. --live performs public source GETs, never Groq/DB calls."""
import argparse
import asyncio
from collections import defaultdict
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.ai.citation_guard import validate_grounded_answer
from backend.ai.multilingual import expand_queries, scholar_referral
from backend.ai.retriever import SearchPlan, retrieve_sources, source, identify
from backend.core.ai_operations import cost
from backend.core.config import settings
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent


def guard_report(dataset):
    results = []
    durations = []
    for case in dataset["guard_cases"]:
        record = source("quran", "1:1", "AlQuran Cloud", "نَصٌّ", "Synthetic source text.")
        record.update(case.get("record_changes", {}))
        record = identify(record)
        claims = []
        if case["quote_changes"] is not None:
            claims = [{"source_id": record["source_id"], "reference": "1:1", "language": "en",
                       "edition": "en.itani", "text": record["translation"], **case["quote_changes"]}]
        raw = json.dumps({"explanation": case["explanation"], "quotations": claims})
        start = time.perf_counter()
        records = [record]
        if case.get("conflicting_numbering"):
            records.append(identify({**record, "numbering_system": "different-edition"}))
        result = validate_grounded_answer(case.get("raw", raw), [] if case.get("no_records") else records, "en")
        durations.append((time.perf_counter() - start) * 1000)
        results.append({"id": case["id"], "category": case["category"], "expected_valid": case["expected_valid"],
                        "accepted": result["valid"], "reason": result.get("reason")})
    accepted = [row for row in results if row["accepted"]]
    supported = [row for row in results if row["expected_valid"]]
    unsupported = [row for row in results if not row["expected_valid"]]
    quotes = [row for row in unsupported if row["category"] in (
        "wrong_surah", "wrong_ayah", "unsupported_quote", "translation_attribution", "wrong_identity")]
    return {
        "cases": len(results), "correct_decisions": sum(row["accepted"] == row["expected_valid"] for row in results),
        "citation_precision": sum(row["expected_valid"] for row in accepted) / len(accepted) if accepted else None,
        "unsupported_quotation_rejection_rate": sum(not row["accepted"] for row in quotes) / len(quotes),
        "unsupported_answer_rejection_rate": sum(not row["accepted"] for row in unsupported) / len(unsupported),
        "false_positive_rejection_rate": sum(not row["accepted"] for row in supported) / len(supported),
        "latency_ms_p50": statistics.median(durations), "latency_ms_max": max(durations), "results": results,
    }


async def retrieval_report(dataset):
    rows = []
    for case in dataset["retrieval_cases"]:
        for mode in ("baseline", "expanded"):
            queries = expand_queries(case["query"]) if mode == "expanded" else [case["query"]]
            queries = queries or [case["query"]]
            start = time.perf_counter()
            records = await retrieve_sources(SearchPlan(language="en", quran_queries=queries))
            elapsed = (time.perf_counter() - start) * 1000
            refs = list(dict.fromkeys(record["reference"] for record in records))[:3]
            relevant = set(case["relevant_references"])
            hits = len(set(refs) & relevant)
            rows.append({"id": case["id"], "language": case["language"], "mode": mode,
                         "references": refs, "precision_at_3": hits / 3, "recall": hits / len(relevant),
                         "latency_ms": elapsed, "empty": not refs})
    groups = defaultdict(list)
    for row in rows:
        groups[(row["language"], row["mode"])].append(row)
    return {"cases": len(dataset["retrieval_cases"]), "runs": len(rows),
            "by_language": {f"{lang}/{mode}": {
                "precision_at_3": statistics.mean(row["precision_at_3"] for row in values),
                "recall": statistics.mean(row["recall"] for row in values),
                "latency_ms_p50": statistics.median(row["latency_ms"] for row in values),
                "empty_results": sum(row["empty"] for row in values),
            } for (lang, mode), values in groups.items()}, "results": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Public source GETs only; no Groq or database")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    dataset = json.loads((ROOT / "source_benchmark.json").read_text(encoding="utf-8"))
    report = {"version": dataset["version"], "measured_at_utc": datetime.now(timezone.utc).isoformat(),
              "review": dataset["review"], "guard": guard_report(dataset),
              "scholar_referral": {"cases": len(dataset.get("referral_cases", [])),
                  "referred": sum(scholar_referral(case["question"]) is not None for case in dataset.get("referral_cases", []))},
              "groq_calls": 0, "actual_ai_cost_usd": "0",
              "configured_max_pipeline_estimate_usd": str(sum(cost(settings.AI_MAX_INPUT_TOKENS, outputs,
                  settings.AI_INPUT_USD_PER_MILLION, settings.AI_OUTPUT_USD_PER_MILLION)
                  for outputs in (settings.AI_PLANNER_OUTPUT_TOKENS, settings.AI_MAX_OUTPUT_TOKENS))),
              "pricing_note": "Configured illustrative prices, not verified billing rates. No end-to-end model latency measured."}
    if args.live:
        report["retrieval"] = asyncio.run(retrieval_report(dataset))
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in ("guard", "retrieval")}, ensure_ascii=True))
    print(json.dumps({"guard": {key: value for key, value in report["guard"].items() if key != "results"}}, ensure_ascii=True))
    if args.live:
        print(json.dumps(report["retrieval"]["by_language"], ensure_ascii=True))
    if report["guard"]["correct_decisions"] != report["guard"]["cases"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
