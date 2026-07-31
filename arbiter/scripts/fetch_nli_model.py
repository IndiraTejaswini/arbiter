"""
Download and vendor the DeBERTa-NLI checkpoint for the semantic
contradiction layer.

    python scripts/fetch_nli_model.py                 # default checkpoint
    python scripts/fetch_nli_model.py --base          # smaller, for laptops/CI
    python scripts/fetch_nli_model.py --dest models/  # explicit location

Why this script exists: the semantic layer is MANDATORY and fail-closed, so
without weights every case carrying comparable claim pairs escalates. That
is correct behaviour -- an unrunnable mandatory check is an unknown, not a
pass -- but it means an out-of-the-box run escalates far more than it
should. Vendoring the checkpoint is what turns the fail-closed design from
a permanent degraded mode into the exception path it is meant to be.

The model is downloaded to a local directory and `ARBITER_NLI_MODEL_PATH`
is pointed at it, so the runtime never reaches the network. That matters
beyond convenience: a dispute-adjudication service that fetches model
weights from the public internet at boot has a supply-chain dependency it
cannot audit and an outage mode it does not control.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# The two permitted checkpoints. Both are DeBERTa-v3 fine-tuned on MNLI and
# friends -- the ENGINE is not configurable (see arbiter.evidence.nli), only
# its size. `base` exists because `large` is ~870 MB, which is a poor fit
# for CI and for a laptop demo, and the accuracy difference on the short,
# highly-templated claim pairs this layer sees is small.
LARGE = "MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli"
BASE = "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli"

DEFAULT_DEST = REPO_ROOT / "models" / "nli"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", action="store_true",
                        help=f"fetch the smaller {BASE} checkpoint (~370 MB) instead of large (~870 MB)")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST,
                        help="directory to vendor the checkpoint into")
    parser.add_argument("--model", type=str, default=None,
                        help="explicit HF repo id; must still be a DeBERTa-NLI checkpoint")
    args = parser.parse_args()

    model_id = args.model or (BASE if args.base else LARGE)

    if "deberta" not in model_id.lower():
        # The engine is a hard architectural constraint, not a default. A
        # helper script that quietly lets someone vendor a different family
        # of model would defeat it.
        print(
            f"ERROR: {model_id!r} is not a DeBERTa checkpoint.\n"
            "The semantic contradiction layer is DeBERTa-NLI exclusively -- see\n"
            "src/arbiter/evidence/nli.py for why a generative model must never\n"
            "serve this boundary.",
            file=sys.stderr,
        )
        return 2

    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError:
        print(
            "ERROR: `transformers` is not installed.\n\n"
            "    pip install -e \".[nli]\"\n\n"
            "Until then the semantic contradiction layer reports UNAVAILABLE and every\n"
            "case with comparable claim pairs ESCALATES to human review. That is the\n"
            "designed fail-closed behaviour, not a crash -- but it is a degraded mode.",
            file=sys.stderr,
        )
        return 1

    dest = args.dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {model_id}\n  -> {dest}")
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForSequenceClassification.from_pretrained(model_id)
    tokenizer.save_pretrained(dest)
    model.save_pretrained(dest)

    size_mb = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e6
    print(f"\nVendored {size_mb:.0f} MB.\n")
    print("Point the runtime at it so it never reaches the network at boot:\n")
    print(f"    export ARBITER_NLI_MODEL_PATH={dest}\n")
    print("Verify it loaded:\n")
    print("    python -c \"from arbiter.evidence.nli import is_available; print(is_available())\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
