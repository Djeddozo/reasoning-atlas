# Visual first: UI mockups before building (task #147)

## Question
Before a feature with a visual side is built, Djeddi wants to see how it would look inside Atlas and where it
would sit, give feedback ("more of this, less of that", "how would it look there") and approve before code is
written. Which way of producing such a mockup fits Atlas?

## Options compared
| Way | Fidelity to Atlas's look | Iterating on feedback | Cost / dependencies | Safety |
|---|---|---|---|---|
| Image model (text → picture) | Low: invents a generic UI, text in images is often garbled, cannot reuse Atlas's real colours and layout reliably | Weak: every round redraws from scratch, small edits drift | A new paid API or a GPU model; new account | Inert image |
| Design tool with an API (Figma and similar) | High | Good | New account, subscription, outside service with his data | External system: needs approval |
| Screenshot of the real app with a patched component | Highest | Needs code to be written first, which is what the step is meant to precede | Headless browser on the Pi | Runs real code |
| **HTML/CSS mockup drawn by Claude, shown sandboxed** | High: the prompt carries Atlas's palette, fonts, sizes and navigation; the model reproduces the frame and places the feature in it | Good: the previous round's HTML and the feedback go into the next call, so only what was asked changes | One model call per round via `models.complete("mockup", …)`, no new dependency | Untrusted output: sanitised and shown in an iframe without script permission |
| Wireframe by rule | Low but correct placement | Moves between sections only | None | Fully under our control |

## Choice
HTML/CSS drawn by Claude, with the rule wireframe as fallback when no model is reachable. It is the only option
that is close to Atlas's look, iterates cheaply, needs no new account or package (stdlib only, rule 8) and keeps
the step before any code is written. Language models write layout HTML well; text-to-image models do not draw
legible, consistent interfaces.

## Safety
Model output is untrusted input (rule 4). `engine/mockup.py` removes scripts, embedding tags, event handlers,
outside addresses and CSS imports/urls, injects a Content-Security-Policy (`default-src 'none'`), and Atlas shows
the page in `<iframe sandbox="">` (no scripts, no same-origin access, no forms).

## Flow
1. Cortex › Visual first lists open tasks with a visual side (word rule, no model) that have no choice yet:
   *Mockup first* or *Build without*. A free request ("Draw a mockup of something new") is also possible.
2. A mockup is drawn as round 1. Feedback (with starters: More of this…, Less of that…, How would it look in…)
   draws the next round; every round is kept and can be compared.
3. While a task's mockup is open, the dev loop skips the task (`mockup.hold`). Approve releases it and the approved
   HTML and feedback history go into the build prompt (`mockup.prompt_block`). Set aside keeps the mockup and lets
   the task be built without it; Reopen starts iterating again.

## Change of 2026-10-25: made in Claude or ChatGPT
Djeddi decided that Atlas should not draw pictures itself: "the image should be created in Claude or ChatGPT and
then be visually shown" in Atlas. The drawing step moved out; everything around it stayed.

1. Atlas writes the request (`mockup.outside_prompt`): the feature, what it should show, the section it sits in,
   Atlas's look, the dashed orange outline for what is new, and every earlier piece of feedback.
2. *Make it in Claude* / *…in ChatGPT* opens `claude.ai/new?q=` or `chatgpt.com/?q=` with the request filled in
   (also copied to the clipboard). No account, key or API is involved: it is his own subscription in his browser.
3. He brings the result back by pasting the picture (Ctrl+V), dropping or choosing a file, reading the clipboard,
   or pasting HTML. `mockup.add_round` recognises PNG, JPEG, WebP and GIF by their first bytes (up to 10 MB) and
   stores them in `memory/mockups/` (git-ignored, personal); anything else must be an HTML page and is sanitised as
   before. Each addition is a round.
4. Approval, Set aside, Reopen and the build hold are unchanged. The build prompt gives the builder the picture's
   path so it can open it with its Read tool.

No model call is made for mockups any more; the `mockup` job and the HTML drawing code remain only for the CLI
and old rounds. The hub refuses `feedback` alone with a message to make the next round outside.

## Follow-ups
- Offer the choice at task creation (Talk / brain dump) instead of only in Cortex.
- Render a PNG thumbnail (needs a headless browser; outside code → security gate) for Telegram.
- Let the builder attach a screenshot of the finished screen next to the approved mockup for comparison.
