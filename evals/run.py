"""Run the evals and save the results for the Evals tab.

    python -m evals.run                          # all three suites on Muse Spark Open (Baseten)
    python -m evals.run --suite rules            # just one suite
    python -m evals.run --provider fireworks     # same open model on another host
    python -m evals.run --provider meta          # Muse Spark on Meta's API, for comparison
    python -m evals.run --suite agent --only biz_bean-normal
    python -m evals.run --fresh                  # ignore cached answers and call the model again

Results go to evals/results/<suite>_<provider>.json (commit them: the Evals tab reads them).
Model answers are cached in evals/.cache, so a re-run costs nothing unless you pass --fresh.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from pathlib import Path

from dotenv import load_dotenv

from core import llm
from evals import harness

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def spent_since(t0: float, provider: str) -> tuple[float, int]:
    with sqlite3.connect(llm.DB_PATH) as con:
        cost, calls = con.execute("SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM llm_calls "
                                  "WHERE ts >= ? AND provider = ? AND cache_hit = 0", (t0, provider)).fetchone()
    return round(cost, 4), calls


def main():
    load_dotenv(llm.ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="all", choices=["all", *harness.SUITES])
    ap.add_argument("--provider", default=llm.DEFAULT_PROVIDER, choices=list(llm.PROVIDERS))
    ap.add_argument("--only", nargs="*", help="case or scenario ids")
    ap.add_argument("--workers", type=int, default=3, help="parallel calls for rules and advice")
    ap.add_argument("--scenarios", type=int, default=5, help="agent scenarios per business")
    ap.add_argument("--fresh", action="store_true", help="clear the eval cache first")
    args = ap.parse_args()

    cache = HERE / ".cache" / args.provider
    if args.fresh and cache.exists():
        shutil.rmtree(cache)
    llm.CACHE_DIR = cache
    RESULTS.mkdir(exist_ok=True)

    suites = list(harness.SUITES) if args.suite == "all" else [args.suite]
    for name in suites:
        print(f"\n== {name} on {args.provider} ({llm.PROVIDERS[args.provider][2]})")
        kw = {"provider": args.provider, "only": args.only}
        if name == "agent":
            kw["n_per_business"] = args.scenarios
        else:
            kw["workers"] = args.workers
        t0 = time.time()
        out = harness.SUITES[name](**kw)
        cost, calls = spent_since(t0, args.provider)
        out.update(suite=name, provider=args.provider, model=llm.PROVIDERS[args.provider][2],
                   run_at=time.strftime("%Y-%m-%d %H:%M"), seconds=round(time.time() - t0, 1),
                   cost_usd=cost, model_calls=calls, partial=bool(args.only))
        path = RESULTS / f"{name}_{args.provider}{'_partial' if args.only else ''}.json"
        path.write_text(json.dumps(out, indent=1, default=str))
        print(f"-- {name}: " + ", ".join(f"{k} {v}" for k, v in out["summary"].items()))
        print(f"   {calls} model calls, ${cost:.4f}, {out['seconds']}s -> {path.relative_to(llm.ROOT)}")


if __name__ == "__main__":
    main()
