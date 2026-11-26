# Tick & Tomorrow pipeline

Zero-cost, human-in-the-loop production pipeline for the **Tick & Tomorrow** YouTube channel.
GitHub Actions runs every stage; GitHub Issues and Pull Requests are the approval screens.
Nothing moves past a gate without Venkat, and nothing is ever published automatically.

## Stages built so far

| # | Stage | Starts | Output | Your job |
|---|-------|--------|--------|----------|
| 1 | Topic suggestions | Mondays 06:00 UTC, or **Actions → 1. Suggest topics → Run workflow** | Issue labelled `gate-1-topic` with 5 ranked ideas | Comment `/approve 1` (or any number) |
| 2 | Research | Your `/approve` comment, or **Actions → 2. Research → Run workflow** | Pull request labelled `gate-2-factcheck` with `facts.md` | Delete or fix doubtful claims, then merge |
| 3 | Script | Merging a fact-check PR, or **Actions → 3. Script → Run workflow** with the episode folder | Pull request labelled `gate-3-script` with `script.md` | Edit wording and the hook in place, then merge |
| 4 | Voice | Merging a script PR, or **Actions → 4. Voice → Run workflow** | `narration.mp3` / `.wav` as a run artifact (7 days); `timings.json` and `captions.srt` committed | Listen (optional). Pick the channel voice once with mode `samples` |
| 5 | Visuals | Merging a script PR, or **Actions → 5. Visuals → Run workflow** | One 1920x1080 image per visual cue + `contact_sheet.jpg` (artifact, 5 days); `visuals.json`, `credits.json` committed | Glance at the contact sheet; assets marked CHECK failed the vision check. Re-running redraws the saved plans (no Flash calls); tick `replan` to start over |
| 6 | Assemble | **Actions → 6. Assemble → Run workflow** after 4 and 5 have finished | `video.mp4` (1080p, artifact, 7 days) | Watch it |

Coming next: private upload (Gate 4), Shorts, news scanner.

Format: one main video of 5 minutes max per week, plus 2 standalone Shorts (quick updates, written as their own short scripts, not cut from the main video).

## How the script stays original and accurate

- Written only from the claims left in `facts.md` after your review; every factual sentence carries its `[Cxx]` tag.
- Code checks it: hook ≤ 40 words, 2 to 4 chapters, 520 to 660 words (under 5 minutes with the end card, the channel's max), every tag exists,
  no sentence with a number untagged, no filler phrases, a visual cue on every line, and no run of 10+ words copied
  from a source. If a check fails, Gemini gets one repair attempt; anything still failing is listed in the PR.
- The same checks run again in CI each time you save an edit to `script.md`.

## How research stays honest

1. Sources are fetched by code, never invented by the AI: Wikipedia articles for the topic, plus official
   pages (`.gov`, `.edu`, standards bodies…) taken from Wikipedia's own reference links, plus any URL you add.
   Each is saved as plain text in `episodes/<episode>/sources/`.
2. Gemini Flash writes atomic claims, each with a quote copied from a source.
3. Code checks every quote really appears in the saved source text. Claims whose quote is not found are dropped.
4. Claims with numbers need two sources, otherwise they are flagged ⚠️ single-source for you to judge.

Gemini budget per episode so far: 1 Flash-Lite call + 1 Flash call for research, 1 to 2 Flash calls for the script (free tier allows ~20 Flash calls a day).

## Voice

Kokoro-82M (Apache 2.0) runs on the free Actions CPU, one sentence at a time, and its own word timestamps
give `timings.json` (sentence and word start/end) and `captions.srt`. Audio is normalised to -14 LUFS.
The voice is set in `config.yaml` (`voice.voice`); `af_heart,af_bella` style values blend two voices.
If a word is mispronounced, add its IPA to `voice/lexicon.yaml`.

## Visuals

- **Diagrams**: Gemini writes a simple layout (boxes, circles, arrows, waves, labels); code draws it in the channel
  colours, so labels are always spelled right.
- **Photos**: Wikimedia Commons, then Openverse, only Public domain / CC0 / CC BY / CC BY-SA. Author and license go to
  `credits.json` for the description. If no licensed photo passes, an illustration is drawn instead.
- **Illustrations**: FLUX.1 schnell on Cloudflare Workers AI (free daily allowance), with a locked brand style.
- **Text cards**: drawn by code.
- Every image is checked by Gemini vision (match with the cue, correctness, garbled text, watermarks, logos) and
  remade up to 2 more times. Anything still failing is marked CHECK on the contact sheet for your final review.

## Assembly

Each visual stays on screen from the first word of its sentences until the next visual starts, with short
cross-fades. Photos and illustrations get a slow zoom; diagrams and text cards stay still so labels stay sharp.
The watermark sits bottom-right, and a 6-second end card (logo + subscribe) leaves room for YouTube end screens.
Captions are not burned in: `captions.srt` is uploaded to YouTube as subtitles (set `assemble.burn_captions: true`
to burn them in instead).

**Background music (optional):** in YouTube Studio, open **Audio Library**, filter **Attribution not required**,
download a calm track, put the file in `assets/music/` (upload it on GitHub with **Add file → Upload files**),
and set `assemble.music` in `config.yaml` to its file name. It is ducked under the voice automatically.

## Files

```
config.yaml              model names, limits, trusted source domains (one-line swaps)
topics/backlog.yaml      evergreen topic ideas; add your own any time
prompts/                 the prompts sent to Gemini (plain text, edit freely)
voice/lexicon.yaml       pronunciation fixes for the AI voice
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
