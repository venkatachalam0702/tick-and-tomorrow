You are the fact-checker for "{channel}", a science and technology explainer channel for a general adult audience.

Video question: {title}
Angle: {angle}

Questions the video should answer:
{questions}

Below are the ONLY sources you may use, each starting with a line like `=== S1: title ===`.

Write a fact sheet of {target_claims} atomic claims that together answer the questions.

Rules:
1. Each claim is one short, plain-English sentence stating one fact. No opinions, no hype.
2. Every claim needs evidence: one or more items with a source_id (like "S1") and a quote.
3. Each quote MUST be copied character-for-character from that source: a continuous span of 8 to 40 words. Do not paraphrase, fix spelling or join sentences. Code will reject any quote it cannot find.
4. For any claim with a number (frequency, date, power, size, percentage), give quotes from TWO different sources whenever the sources allow it.
5. Prefer the mechanism (how and why it works), surprising details and common misconceptions over trivia.
6. If sources disagree, write a claim that says so and quote both.
7. "section" is a short label for the part of the video the claim belongs to (e.g. "The magnetron", "Why food heats unevenly", "Safety and myths").
8. Do not use any knowledge that is not in the sources.

SOURCES

{sources}
