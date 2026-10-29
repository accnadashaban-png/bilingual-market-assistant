# Evaluation

```bash
python -m market_assistant.eval                       # all 1,296 cases, offline
python -m market_assistant.eval --suite golden        # the 47 hand-written cases
python -m market_assistant.eval --suite golden --judge  # add LLM-as-judge scores (needs an LLM)
```

Each run writes `reports/eval-latest.md` and `.json`, appends a line to `reports/runs.jsonl`
(time, suite, mode, dataset hash, metrics) and prints the change against the previous run of the same suite.
CI fails if the score drops below 98%.

## Dataset

| Part | Cases | What it checks |
|---|---|---|
| Hand-written golden set (`datasets/golden.jsonl`) | 47 | quotes, performance, movers, sectors, comparisons, screens, signals, fundamentals, market breadth, follow-ups, greetings, advice, out-of-scope, safety |
| Quotes, 5 registers × 90 companies | 450 | company-name resolution in English, Egyptian, Gulf, Levantine and MSA |
| Year-to-date performance, fundamentals, risk | 540 | 2 registers × 90 companies each |
| Risk with recomputed numbers | 90 | volatility and max drawdown must match an independent calculation |
| Movers: 4 markets × 4 periods × up/down × 2 registers | 64 | the **top 5 in the exact order** |
| Sectors, market breadth, company lists | 17 | best sector; advancers and company counts |
| Signals and signal performance, every signal type | 14 | signal counts; routing of "hit rate" questions |
| Multi-turn follow-ups | 60 | "and its revenue?", "وأداؤه من أول السنة؟" resolved from memory |
| Refusals | 14 | writes, prompt injection, news, crypto, gold and forecasts are refused without leaking internals |
| **Total** | **1,296** | |

By register (generated cases): English 226, Egyptian 138, MSA 60, Gulf 30, Levantine 30, plus 5 Arabic refusals.

## How a case passes

1. **Route**: the assistant chose the expected route (`data`, `advice`, `unsupported`...).
2. **Tool**: the first tool call is the expected one.
3. **Facts**: every expected value appears in the answer, formatted exactly as the writer prints it. Expected
   values are **computed at run time** by a reference SQL query or an independent Python calculation, never
   copied by hand, so they stay correct if the data changes.
4. **Order**: for rankings, the expected values appear in the expected order.
5. **Language**: the answer is in the language of the question.
6. **Must / must not include**: disclaimers present; SQL, tracebacks or system names absent.

A case whose reference query returns nothing fails; it never passes silently.

## Keeping the dataset trustworthy

`tests/eval/test_dataset_quality.py` checks the dataset itself before any model is graded:
unique ids, valid routes and tools, every tool covered, every register covered, every reference
query returns a non-null value, and no ties at ranking cut-offs (rankings use a deterministic tie-breaker).

## LLM-as-judge

With `--judge`, a model grades each answer from 1 to 5 on correctness, language/dialect, format and helpfulness,
with a one-line reason. Rule checks stay the source of truth for facts; the judge covers quality that rules cannot
see. A judge failure never fails the run.

## Latest result (offline mode)

| Metric | Score |
|---|---|
| Overall (1,296 cases) | 100% |
| Route accuracy | 100% |
| Tool accuracy | 100% |
| Fact accuracy | 100% |
| Language match | 100% |

Offline mode is deterministic, so this measures the planner, tools and writer. With an LLM configured,
the same command measures the model-driven pipeline, which is where the judge scores matter.
