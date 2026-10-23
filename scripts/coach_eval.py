#!/usr/bin/env python3
"""
Ask the AI coach a fixed set of questions on each model and write the answers side by side,
so models can be compared on real logs (quality first, then time and tokens).

    python scripts/coach_eval.py --user harsh_0824
    python scripts/coach_eval.py --user harsh_0824 --models gemini:gemini-2.5-flash,groq:openai/gpt-oss-120b

It uses the user's saved keys plus GEMINI_API_KEY / GROQ_API_KEY, reads the database the app is
configured for, and records nothing (answers here don't show up on the admin review page).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

# Let this script import project modules when run as `python scripts/<name>.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from models import Session, User, UserApiKey
from services import ai_coach

QUESTIONS = [
    "How has my training gone over the last 4 weeks?",
    "Which of my lifts are stalling, and what should I do about it?",
    "What are my strongest lifts?",
    "What should I lift next time on bench press?",
    "Which muscle groups am I neglecting?",
    "What should I train today?",
    "Is my squat improving?",
    "Write me a poem about cats",  # should be politely declined
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", required=True, help="username whose logs the coach answers from")
    parser.add_argument("--models", help="comma list of provider:model; default: every model in the current order")
    parser.add_argument("--questions", help="file with one question per line (default: a built-in set)")
    parser.add_argument("--pause", type=float, default=6.0, help="seconds between questions, for free-tier limits")
    parser.add_argument("--out", default="coach_eval.md", help="markdown report path")
    args = parser.parse_args()

    db = Session()
    user = db.query(User).filter_by(username=args.user).first()
    if not user:
        print(f"No user {args.user!r}.")
        return 1
    keys = [
        {"id": k.id, "api_key": k.api_key}
        for k in db.query(UserApiKey).filter_by(user_id=user.id, is_active=True).all() if k.api_key
    ]
    models = [m.strip() for m in args.models.split(",")] if args.models else [f"{p}:{m}" for p, m in ai_coach._model_order()]
    questions = QUESTIONS
    if args.questions:
        with open(args.questions) as f:
            questions = [line.strip() for line in f if line.strip()]

    rows = []  # (question, model, answer, seconds, tokens, tools, source)
    for model in models:
        # One model at a time, nothing to fall back to, so each answer is that model's own.
        Config.AI_MODEL_ORDER, Config.GEMINI_COACH_MODEL = model, ""
        for q in questions:
            started = time.perf_counter()
            events = list(ai_coach.run_coach_events(db, user, messages=[{"role": "user", "text": q}], api_keys=keys))
            result = events[-1]["result"]
            usage = result.get("usage") or {}
            rows.append((q, model, result.get("answer") or result.get("error") or "", time.perf_counter() - started,
                         usage.get("tokens"), [s["tool"] for s in result.get("steps") or []], result.get("source")))
            print(f"{model:40} {rows[-1][3]:5.1f}s  {(result.get('source') or '-'):5}  {q}")
            time.sleep(args.pause)

    with open(args.out, "w") as f:
        f.write(f"# Coach eval, {datetime.now():%Y-%m-%d %H:%M}, user {args.user}\n\n")
        f.write("| Model | Answered by AI | Median time | Median tokens |\n|---|---|---|---|\n")
        for model in models:
            mine = [r for r in rows if r[1] == model]
            ai = [r for r in mine if r[6] == "ai"]
            times = sorted(r[3] for r in ai)
            tokens = sorted(r[4] for r in ai if r[4])
            f.write(f"| {model} | {len(ai)}/{len(mine)} | "
                    f"{(f'{times[len(times) // 2]:.1f}s' if times else '-')} | "
                    f"{(tokens[len(tokens) // 2] if tokens else '-')} |\n")
        for q in questions:
            f.write(f"\n## {q}\n")
            for r in (r for r in rows if r[0] == q):
                tools = ", ".join(r[5]) or "no lookups"
                label = "" if r[6] == "ai" else " (fell back to the built-in answer)"
                f.write(f"\n### {r[1]}{label}\n*{r[3]:.1f}s, {r[4] or '?'} tokens, {tools}*\n\n{r[2]}\n")
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
