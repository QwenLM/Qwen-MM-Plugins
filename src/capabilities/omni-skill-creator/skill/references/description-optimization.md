# Description Optimization

The description field in SKILL.md frontmatter is the primary mechanism that determines whether you invoke a skill. After creating the video-derived skill, offer to optimize its description for better triggering accuracy.

### How skill triggering works

Read this before writing queries — the mechanism decides what makes a query worth including. Skills appear in your `available_skills` list with their name + description, and you decide whether to consult a skill based on that description. The important thing to know is that you only consult skills for tasks you can't easily handle on your own — simple, one-step queries like "read this PDF" may not trigger a skill even if the description matches perfectly, because you can handle them directly with basic tools. Complex, multi-step, or specialized queries reliably trigger skills when the description matches.

This means your eval queries should be substantive enough that you would actually benefit from consulting a skill. Simple queries like "read file X" are poor test cases — they won't trigger skills regardless of description quality.

### Step 1: Generate trigger eval queries

Create 20 eval queries — a mix of should-trigger and should-not-trigger. Save them to the workspace as JSON:

```json
[
  {"query": "the user prompt", "should_trigger": true},
  {"query": "another prompt", "should_trigger": false}
]
```

The queries must be realistic and something a real user would actually type. Not abstract requests, but requests that are concrete and specific and have a good amount of detail. For instance, file paths, personal context about the user's job or situation, column names and values, company names, URLs. A little bit of backstory. Some might be in lowercase or contain abbreviations or typos or casual speech. Use a mix of different lengths.

**These labels become the optimization target unreviewed, so each one has to be defensible from the skill's stated scope.** You write the queries *and* the `should_trigger` labels, and the loop then rewrites the description to agree with them — nothing outside checks the labels, so a wrong one silently pulls the description the wrong way while the score still climbs. Before keeping a query, point at the description or the skill body that settles its label. If you can't, the answer is a product decision you don't have; drop the query instead of guessing.

Bad: `"Format this data"`, `"Extract text from PDF"`, `"Create a chart"`

Good: `"ok so my boss just sent me this xlsx file (its in my downloads, called something like 'Q4 sales final FINAL v2.xlsx') and she wants me to add a column that shows the profit margin as a percentage. The revenue is in column C and costs are in column D i think"`

For the **should-trigger** queries (8-10), think about coverage. You want different phrasings of the same intent — some formal, some casual. Include cases where the user doesn't explicitly name the skill or file type but clearly needs it. Throw in some uncommon use cases and cases where this skill competes with another but should win.

For the **should-not-trigger** queries (8-10), the most valuable ones are the near-misses — queries that share keywords or concepts with the skill but actually need something different. Think adjacent domains, ambiguous phrasing where a naive keyword match would trigger but shouldn't, and cases where the query touches on something the skill does but in a context where another tool is more appropriate.

The key thing to avoid: don't make should-not-trigger queries obviously irrelevant. "Write a fibonacci function" as a negative test for a PDF skill is too easy — it doesn't test anything. The negative cases should be tricky *for a keyword matcher* while still being clear-cut once you read the skill's scope — that is exactly the query the description needs to learn to turn away. A negative whose label is a coin flip even after reading the scope is a different thing, and it belongs nowhere near the eval set.

### Step 2: Run the optimization loop

Tell the user: "This will take some time — I'll run the optimization loop in the background and check on it periodically."

Run it in the background:

```bash
python -m scripts.run_loop \
  --eval-set <path-to-trigger-eval.json> \
  --skill-path <path-to-skill> \
  --model <model-id-powering-this-session> \
  --max-iterations 5 \
  --verbose
```

Use the model ID from your system prompt (the one powering the current session) so the triggering test matches what the user actually experiences. The script judges triggering by calling the model through an OpenAI-compatible API (DashScope by default) internally.

While it runs, periodically tail the output to give the user updates on which iteration it's on and what the scores look like.

`run_loop.py` is the only entry point here; it drives `run_eval.py` (scores one description) and
`improve_description.py` (proposes the next one) itself, so you never call those two directly.

This handles the full optimization loop automatically. It splits the eval set into 60% train and 40% held-out test, evaluates the current description (running each query 3 times to get a reliable trigger rate), then proposes improvements based on what failed. It re-evaluates each new description on both train and test, iterating up to 5 times. When it's done it returns JSON with `best_description` — selected by test score rather than train score to avoid overfitting. That JSON and the `--verbose` stream are the whole output; there is no report file and nothing opens a browser. `--results-dir <dir>` additionally saves `results.json` plus the improvement-call logs to a timestamped subdirectory.

### Step 3: Apply the result

Take `best_description` from the JSON output and update the skill's SKILL.md frontmatter. Show the user before/after and report the scores — this is the one place a human sees the loop's output, so it is where a target that drifted gets caught. Say what the description now turns away, not just that the score went up.
