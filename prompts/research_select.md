You are helping research a YouTube explainer for a general adult audience.

Video question: {title}
Main Wikipedia article (may be empty): {wiki}

Return JSON with:
- "wikipedia_titles": up to {max_articles} exact English Wikipedia article titles that together explain this topic well (the main article first, then articles on the key mechanisms or parts). Use real article titles only.
- "key_questions": 5 to 8 questions a curious viewer would want answered, in the order a good explainer would answer them.
- "angle": one sentence with a fresh, specific angle for the video (not generic).
