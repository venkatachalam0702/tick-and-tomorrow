# One-time setup (about 2 minutes)

## 1. Let Actions open pull requests
1. Repo → **Settings** → **Actions** → **General**.
2. Scroll to **Workflow permissions**.
3. Pick **Read and write permissions**.
4. Tick **Allow GitHub Actions to create and approve pull requests**.
5. **Save**.

## 2. First test run (How does a microwave work?)
1. Repo → **Actions** → **2. Research (Gate 2 fact-check PR)** → **Run workflow**.
2. Leave the defaults ("How does a microwave work?", "Microwave oven") → **Run workflow**.
3. In about 5 minutes a pull request "Gate 2: fact-check ..." appears under **Pull requests**.
4. Open it → **Files changed** → `facts.md` → follow the review steps at the top → **Merge**.

## 3. Weekly flow
- Monday: an issue "Gate 1: pick a topic" appears. Comment `/approve 1` (or another number).
- Research runs on its own and links the fact-check pull request in that issue.
- To try it now instead of waiting for Monday: **Actions → 1. Suggest topics → Run workflow**.
