# Tick & Tomorrow pipeline

Zero-cost, human-in-the-loop production pipeline for the **Tick & Tomorrow** YouTube channel.
GitHub Actions runs every stage; GitHub Issues and Pull Requests are the approval screens.
Nothing moves past a gate without Venkat, and nothing is ever published automatically.

## Stages built so far

| # | Stage | Starts | Output | Your job |
|---|-------|--------|--------|----------|
| 1 | Topic suggestions | Mondays 06:00 UTC, or **Actions → 1. Suggest topics → Run workflow** | Issue labelled `gate-1-topic` with 5 ranked ideas | Comment `/approve 1` (or any number) |
| 2 | Research | Your `/approve` comment, or **Actions → 2. Research → Run workflow** | Pull request labelled `gate-2-factcheck` with `facts.md` | Delete or fix doubtful claims, then merge |

Coming next: script (Gate 3), voice, visuals, assembly, private upload (Gate 4), Shorts, news scanner.

## How research stays honest

1. Sources are fetched by code, never invented by the AI: Wikipedia articles for the topic, plus official
   pages (`.gov`, `.edu`, standards bodies…) taken from Wikipedia's own reference links, plus any URL you add.
   Each is saved as plain text in `episodes/<episode>/sources/`.
2. Gemini Flash writes atomic claims, each with a quote copied from a source.
3. Code checks every quote really appears in the saved source text. Claims whose quote is not found are dropped.
4. Claims with numbers need two sources, otherwise they are flagged ⚠️ single-source for you to judge.

Gemini budget per episode so far: 1 Flash-Lite call + 1 Flash call (free tier allows ~20 Flash calls a day).

## Files

```
config.yaml              model names, limits, trusted source domains (one-line swaps)
topics/backlog.yaml      evergreen topic ideas; add your own any time
prompts/                 the prompts sent to Gemini (plain text, edit freely)
pipeline/                Python code for each stage
episodes/<date>-<slug>/  one folder per video: topic.yaml, sources/, facts.md, facts.json
.github/workflows/       the automation
```

## Secrets

Stored only in **Settings → Secrets and variables → Actions**: `GEMINI_API_KEY`, `CF_ACCOUNT_ID`,
`CF_API_TOKEN` (YouTube OAuth comes with the upload stage). They never appear in code, logs or chat.

## Run the tests

```
pip install -r requirements.txt
python -m pytest -q
```
