"""DataPilot benchmark: representative business questions, trusted answers and an evaluator.

  cases.json       the questions and what a correct answer must contain
  ground_truth.py  hand-written SQL that defines the correct answer for each question
  evaluate.py      loading, checking and comparing results (pure Python, no I/O)
  run.py           the command-line runner (offline ground-truth mode, opt-in live mode)

See docs/benchmark.md.
"""
