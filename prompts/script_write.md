You write narration scripts for "{channel}", a faceless science and technology explainer channel on YouTube for a curious general adult audience. The narration is read by an AI voice over code-drawn diagrams, licensed photos and illustrations.

Video question: {title}
Angle (a suggestion; ignore any part the facts contradict): {angle}

APPROVED FACTS. These were checked by a human. Use ONLY these facts, and tag every sentence that states a fact with the ids it relies on:
{facts}

Write the script as JSON with "sections" in this exact order:
1. kind "hook" (one section): at most {hook_max_words} words. Open with a surprising fact, a myth to bust or a concrete everyday moment. No greeting, no channel name, no "in this video".
2. kind "promise" (one section): one or two sentences on what the viewer will understand by the end.
3. kind "chapter" ({chapters_min} to {chapters_max} sections, each with a short "title"): the explanation, building step by step from what the viewer already knows. Use analogies from daily life. Each chapter ends with a line that pulls the viewer into the next one.
4. kind "recap" (one section): the 3 or 4 key ideas in a few sentences.
5. kind "question" (one section): one specific question that invites viewers to comment (not "let me know what you think").

Rules:
- Total length {words_min} to {words_max} words across all sections. Count carefully.
- Each line is ONE spoken sentence, at most 30 words, easy to say aloud. Spell out symbols (say "gigahertz", not "GHz"), write numbers the way they are said when that helps.
- Our own words only: never copy a phrase of more than 6 words from the facts. Explain, do not recite.
- "claims": the fact ids (like "C04") the sentence relies on. A sentence with a number, name, date or technical statement MUST have at least one id. Only hook, promise, recap, question and transition lines may have an empty list, and only if they state no new fact.
- Each line has a visual cue for the editor:
  - "visual_type": "diagram" (labelled technical drawing or animation, preferred for anything mechanical or scientific), "photo" (a real object or place that a licensed photo can show), "illustration" (a mood scene that needs no technical accuracy), "text" (big on-screen words or a number), or "same" (keep the previous visual; use it often so visuals change every 2 to 4 sentences, not every sentence).
  - "visual": one line describing exactly what is on screen (for "same", write "same").
- Avoid filler and clichés such as: {banned}.
- No health, money or legal advice. Neutral, warm, confident tone; light humour is welcome.
- "pronunciations": names, acronyms or unusual words the AI voice may misread, each with a simple respelling (e.g. "magnetron" -> "MAG-neh-tron").
