# A persistent place for vision questions

Read [VISION.md](VISION.md), then use the [Vision Room — questions, reminders and clarifications](https://github.com/Djeddozo/Unconventional-Reasoning-Atlas/issues/1) to add questions as comments.

You can also ask in Codex or Claude desktop:
> Answer this using VISION.md and confirmed decisions. Distinguish established requirements from proposals. Update the question log and write useful clarifications back into the vision.

GitHub does not answer automatically. An active AI session must read and answer the questions; no always-running responder or additional subscription is configured.

## Answer protocol
1. Give a direct answer in plain English.
2. Cite the relevant vision section or decision.
3. Identify missing information and proposed interpretations.
4. Write useful clarification back to VISION.md.
5. Change product direction only following an explicit owner decision; record it in DECISIONS.md.
6. Mark a question answered only when its answer is available. Retain corrections and earlier context.

## Question log
| ID | Question | Answer / evidence | Status |
|---|---|---|---|
| VQ-001 | Why does this exist alongside the Engine? | The Engine provides existing planning and execution; Atlas connects knowledge, skills and resources in its own visual workspace. See VISION.md and docs/INTEGRATION_ENGINE.md. | Proposed interpretation |
| VQ-002 | Is a skilltree the whole universe? | No. It is a dependency view inside a larger network that also includes sources, projects and physical resources. | Specified |
| VQ-003 | Can another AI continue after a cap? | This is a core requirement. Git, small tasks and checkpoints carry context. RA-015 is the required practical test. | Scripted drill passes (STATE/evidence/RA-015.md); live agent drill pending |
| VQ-004 | Is the visual reference included? | Yes. The supplied PNG is preserved unchanged, inspected, hashed and mapped in docs/DESIGN.md. Implementation fidelity remains to be tested. | Reference verified |
| VQ-005 | What language should the project use? | English throughout the product and development workflow, following the owner's explicit instruction. See ADR-010. | Confirmed |
| VQ-006 | Which domains belong together inside the Engine, and which functions should other sections be able to call? | Owner confirmed on 2026-10-20: Direction, Build (Forge), Research, Growth and Memory. Callable functions are defined per domain in RA-020. See docs/PLATFORM.md. | Confirmed |
| VQ-007 | What do the other apps (Life-Dash, LifeDashV5, finance_vault, Cilindervinder, Padelcode) do, and which ones join the platform first? | Owner will answer later. Atlas reminds him: one 'Dissect' item per app under Waiting on you (RA-024), feeding RA-023. | Deferred by owner |
| VQ-008 | Will one platform hide what the Engine can do? | It should not: modules stay whole and the Systems view shows every capability, its home and its status. See ADR-013. | Proposed interpretation |
| VQ-009 | How should Atlas run an agent itself? | Owner decided on 2026-10-20: the Engine's dev loop on the Pi (Claude Code with his subscription), with the Claude app and Codex as fallbacks. See ADR-019. | Confirmed |
| VQ-010 | How should the Engine's functions fit into Atlas without losing sight or overloading the screen? | Owner (2026-10-20): the Engine's structure is mostly good; integrate with Atlas; Atlas may later be part of something bigger; builds must show what they are for; avoid overload; keep Atlas's look and the Engine's interaction design. Answered by docs/MODEL.md (ADR-020). | Answered; owner may adjust |

## Planned in-app Vision Room
A persistent navigation entry opens the versioned vision on the left and a source-grounded conversation on the right. Questions and answers are stored locally, exportable and assigned provider-independent conversation IDs.
An offline or limited provider leaves a question pending. AI answers do not directly rewrite the vision: show a proposed diff and let the owner accept or reject it. This is RA-017, not an already implemented chatbot.
