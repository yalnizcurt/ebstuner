CTL–DAT Alignment Studio

Start: .venv/bin/python -m uvicorn web:app --host 127.0.0.1 --port 8000 --no-access-log
Open: http://127.0.0.1:8000

Prepare a fixed-width DAT for review before an Oracle SQL*Loader/EBS import:
inspect its CTL, find damaged bytes, compare evidence, approve changes and export.

See README.md for installation, workflow, Groq setup, tests and deployment.
See docs/ORACLE_EXAMPLES.md for 11 synthetic layouts / 26 variants up to 4,800 bytes.
See docs/GROQ.md for openai/gpt-oss-120b setup and limits.
See docs/ENGINEERING_REPORT.md for the historical initial verification results.

Source DAT bytes remain immutable. All repair candidates require approval.
Groq openai/gpt-oss-120b is the default AI provider; Bedrock is an explicit alternative.
Deterministic mode needs no cloud account. The app does not execute Oracle/EBS loads.
