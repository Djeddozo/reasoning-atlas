# The vision: a universe of what I know, can do and can become

## Why I am building this
I want to see my ideas, knowledge, projects and existing work together. I do not want to keep starting over, forgetting why I wanted something, or rebuilding capabilities I already have. My computer should help me recognize useful connections and choose a next step that serves my direction.

Reasoning Atlas is my personal knowledge universe. It connects learning to making. Thoughts become more than an endless chat history: I can revisit where an idea came from, what it assumes, which evidence supports it and how to test it.

This page develops the explicit requirements supplied on October 20, 2026 and the accessible earlier user messages. Some earlier assistant proposals could not be recovered. Newly proposed interpretations are not silently attributed to the owner.

## What I will be able to do
I open the Windows interface and see knowledge and projects in context. I select an idea, such as a language-learning tool, and discover relevant language skills, my GPU, reusable software and research. Atlas distinguishes established connections from suggestions.

I can move from that idea to a skill or a build step. Forge/Engine supports execution: a proposal becomes a task, an agent works on a branch, tests provide evidence, and a pull request exposes the result. Atlas distinguishes an idea, built code, merged code and something actually installed.

If an AI reaches a usage limit, the work remains. Another session or provider can read the repository and continue from the last verified checkpoint.

## How the parts fit together
- **Knowledge map:** what I know, where it came from and where sources disagree.
- **Skilltree:** what I can do, what I want to learn and which prerequisites remain.
- **Capability graph:** how knowledge, software, hardware, time and people combine to make an outcome possible.
- **Memory:** decisions, attempts and lessons, with provenance and correction history.
- **Forge and Research:** turning investigation into a proposal and then testable work.
- **Vision Room:** this explanation, persistent questions and the history of clarification.

A skilltree is one view of a larger network. Not everything is a ladder or a progress bar. Projects can share a skill; a GPU is a resource, not a skill; language proficiency can exist independently of any software project.

## Unconventional reasoning
The reference centers on LLM Reasoning and connects it to mathematical reasoning, symbolic logic, physics-based models, evolutionary search, chemical computing, meta-reasoning, embodied intelligence and neuro-symbolic systems. These are research domains, not promises that Atlas implements each reasoning engine.

The roadmap explores LLMs, hybrid reasoning, mechanism transfer, adaptive architectures and reasoning discovery. The aim is to investigate how methods can complement each other across domains. Unexpected hypotheses are welcome; they still need sources, counterevidence and experiments. A compelling visual connection is not proof.

## What keeps this useful
The application must improve understanding and choice. A spectacular map that obscures the next step misses the purpose. AI proposes connections without silently certifying them as true. Evidence, uncertainty and original context remain visible.

The [original Windows reference](assets/reference/reasoning-atlas-windows-mockup.png) anchors the visual identity: a dark knowledge workspace, violet nodes, an orange central glow, research roadmap and contextual side panels. The hardware bezel and Windows wallpaper are presentation context, not application UI to reproduce.

## When this succeeds for me
In one session I can save a thought, recover relevant prior work, see the resources I have and identify a concrete next step. I do not need to remember terminal commands for daily use. Later, I can explain why I made a decision and resume without telling the entire story again.

## Keep asking questions
Use [Vision questions](VISION_QUESTIONS.md) and the [Vision Room](https://github.com/Djeddozo/Unconventional-Reasoning-Atlas/issues/1).
Examples:
- Why am I building this?
- Does this proposed feature serve the original purpose?
- How do knowledge, skills and capabilities differ?
- What already exists in the Engine, and what does Atlas add?
- What is the smallest next step that makes the vision tangible?

Answers must distinguish the vision, confirmed decisions and new proposals. Clarification can improve this page; a change of direction requires an explicit owner decision recorded in DECISIONS.md.

## Open choices
The first real projects, actual Pi/GPU hardware and detailed implementation stack still need verification. The mockup is available; exact font identity cannot be recovered from a raster alone. Visual implementation must be compared against the reference rather than declared faithful on the strength of a token file.

## Visual-first implementation priority
The owner wants the runnable visual app first, to follow components being built inside it. Keep unfinished components visible but dimmed and explicitly labeled as not built yet. Activate them only when implemented and tested.

## One platform
The owner wants one central platform instead of loose apps, with the Engine integrated completely and other apps to follow. Domains that belong together stay together, so the owner keeps sight of what each system can do; specific functions remain callable wherever they are useful. The proposed structure (modules, capabilities, Systems view) is in [docs/PLATFORM.md](docs/PLATFORM.md); see ADR-013.

## Watching it being built
The app can stay open while agents work. A progress bar shows the active task and step, and the window updates itself when a new version is ready (ADR-014).

## Working inside Atlas
The owner wants to do his project work inside Atlas, switching to the Claude app or Codex only when Atlas cannot do something yet. Atlas knows which work it can do, says when another app is needed, and hands the work over with its context; the repository carries the work back (ADR-017).

## Programs, paths and functions
Atlas is a program that holds sub-programs (the Engine, formerly HQ; Finance) and can itself become part of something bigger. Everything it can do is a function with one home, usable everywhere. Every build shows which program and path it serves, and finished work folds away so the screen shows what matters now (docs/MODEL.md, ADR-020).

