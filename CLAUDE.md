# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# PLANNING SKILL: SPEC-DRIVEN DEVELOPMENT

## Core Philosophy

**No code before clarity. No clarity without documentation.**

Spec-driven development means: before a single line of code is written, the *why*, the *what*, the *who*, and the *how* are fully documented, questioned, challenged, and agreed upon. Any person — including a stranger six months later — should be able to read the generated files and reconstruct:

1. Why this project exists
2. What decisions were made and why
3. What was considered and rejected
4. What the system does and how it works
5. Every meaningful change that happened

---

## The Artifact Set

When PLANNER is active, the following files are created and maintained in a `spec/` directory at the project root (or in `./` if no project root exists yet):

| File | Purpose |
|---|---|
| `spec/PROJECT.md` | Vision, motivation, goals, non-goals, stakeholders |
| `spec/SPEC.md` | Full functional + technical specification |
| `spec/DECISIONS.md` | Architecture Decision Records (ADRs) — every non-trivial decision |
| `spec/QUESTIONS.md` | Every question asked, its answer, and who resolved it |
| `spec/CHANGELOG.md` | Chronological log of every meaningful change to the spec or project |
| `spec/TASKS.md` | Derived task breakdown from the spec, with status |

These files are **living documents** — updated at every phase, not written once and forgotten.

---

## Operating Rules

1. **Ask before assuming.** If something is ambiguous, ask. Never infer scope silently.
2. **Document the question, not just the answer.** The reasoning behind a decision is as important as the decision itself.
3. **Explicit rejections.** If an option was considered and rejected, record it in `DECISIONS.md` with the reason. Future you (or a collaborator) deserves to know why you didn't go that way.
4. **One phase at a time.** Do not jump to architecture until requirements are locked. Do not write tasks until the spec is complete.
5. **No orphaned decisions.** Every decision in `DECISIONS.md` must trace back to a requirement or a question in `QUESTIONS.md`.
6. **Changelog discipline.** Every time the spec evolves — even a scope change, a rejected idea, or a revised requirement — it gets logged.

---

## Phase 0: Discovery

**Goal:** Understand the raw idea before formalizing anything.

Ask the following, one focused set at a time (do not dump all questions at once):

### Round 1 — The What and Why
- What are we building, in one sentence?
- What problem does it solve? Who has this problem?
- Why does this need to exist now? What triggered this?
- Is there an existing solution? Why not use it?

### Round 2 — The Who
- Who are the users? (Internal tool? Public product? Single user? Team?)
- What does success look like for a user?
- What does failure look like?

### Round 3 — The Boundaries
- What is explicitly OUT of scope?
- Are there hard constraints? (deadline, budget, specific tech, existing infrastructure)
- What can change vs. what is fixed?

### Round 4 — The Scale and Context
- Rough scale: how many users, how much data, what kind of traffic?
- Is this greenfield or does it integrate with something existing?
- What does the deployment environment look like?

**Output:** Populate `spec/PROJECT.md` with all answers.

---

## Phase 1: Requirements

**Goal:** Derive formal, unambiguous requirements from the discovery output.

Requirements are written as:
- **Functional Requirements (FR):** What the system must do. Written as `FR-001: The system shall...`
- **Non-Functional Requirements (NFR):** How well it must do it. Written as `NFR-001: The system shall...`
- **Constraints:** Hard limits. Written as `CON-001: ...`

Clarifying questions at this phase focus on:
- Edge cases: "What happens if the user does X without doing Y first?"
- Conflicts: "FR-003 implies realtime updates, but you said no websockets — how do we resolve this?"
- Priority: "If you had to cut one requirement, which goes first?"

Every requirement must have:
- A unique ID (`FR-001`, `NFR-002`, etc.)
- A plain-English description
- The source (which user/stakeholder need drives it)
- Priority: `MUST / SHOULD / COULD / WON'T` (MoSCoW)

**Output:** Requirements section in `spec/SPEC.md`.

---

## Phase 2: Architecture

**Goal:** Design the system structure that satisfies the requirements.

For each significant architectural decision, record an ADR in `spec/DECISIONS.md`:

```
## ADR-001: [Decision Title]

**Date:** YYYY-MM-DD
**Status:** Accepted | Superseded | Rejected

**Context:**
What situation forced this decision? Which requirements drive it?

**Options Considered:**
1. Option A — [brief description]
   - Pros: ...
   - Cons: ...
2. Option B — [brief description]
   - Pros: ...
   - Cons: ...

**Decision:**
We chose Option X because...

**Consequences:**
What does this decision make easier? What does it make harder?

**Linked Requirements:** FR-002, NFR-001
```

Architectural questions to ask:
- What are the major components/services/modules?
- Where is state stored? Who owns it?
- What are the data flows? (Draw it out in ASCII if needed)
- What are the failure modes? How does the system degrade gracefully?
- What are the integration points and their contracts?
- Where is the complexity? Is it justified?

**Output:** Architecture section in `spec/SPEC.md`, ADRs in `spec/DECISIONS.md`.

---

## Phase 3: Full Specification

**Goal:** Write the complete, implementation-ready spec.

`spec/SPEC.md` must include:

```
# [Project Name] — Technical Specification

## 1. Overview
One paragraph. What this is and why it exists.

## 2. Goals
Numbered list. What success looks like.

## 3. Non-Goals
Explicit list of what this will NOT do.

## 4. Requirements
(Populated from Phase 1)

## 5. Architecture
### 5.1 System Diagram
(ASCII or description)
### 5.2 Component Breakdown
### 5.3 Data Model
### 5.4 API / Interface Contracts

## 6. Technical Decisions
(Summary + links to DECISIONS.md ADRs)

## 7. Error Handling & Edge Cases
## 8. Testing Strategy
## 9. Deployment & Operations
## 10. Open Questions
(Things not yet resolved — link to QUESTIONS.md)
```

Before moving to tasks, run a **spec review**:
- Does every requirement have at least one architectural component addressing it?
- Are there any requirements with no clear implementation path?
- Are all open questions resolved or explicitly deferred?

---

## Phase 4: Task Breakdown

**Goal:** Decompose the spec into executable tasks.

`spec/TASKS.md` format:

```
## [Feature Area]

- [ ] TASK-001: [verb + object description]
  - Spec ref: FR-002, FR-005
  - Notes: ...
  - Blocked by: TASK-003

- [ ] TASK-002: ...
```

Rules:
- Every task must link back to at least one spec requirement.
- Tasks are small enough to be completed in one sitting.
- Dependencies are explicit (`Blocked by:`).
- No task should be "implement X" with no further description — break it down until it is unambiguous.

---

## Phase 5: Ongoing — Change Logging

Every time anything changes — a requirement is revised, an ADR is superseded, a task is added, a scope decision is made — log it in `spec/CHANGELOG.md`:

```
## YYYY-MM-DD — [Brief summary of change]

**Type:** Requirement change | Decision revision | Scope change | Task update | Bug in spec

**What changed:**
Before: ...
After: ...

**Why it changed:**
...

**Impact:**
- Tasks affected: TASK-004, TASK-007
- Requirements affected: FR-003
- ADRs affected: ADR-002 (now superseded)
```

The changelog is **append-only**. Nothing is deleted — superseded decisions stay with a `[SUPERSEDED by ADR-005]` note.

---

## Questions Log Format

`spec/QUESTIONS.md` tracks every question asked during planning:

```
## Q-001: [Question]

**Phase:** Discovery | Requirements | Architecture | Specification
**Asked by:** Claude | User | Stakeholder
**Date:** YYYY-MM-DD
**Status:** Open | Answered | Deferred

**Answer:**
...

**Impact:**
Led to FR-004, ADR-002
```

Open questions are surfaced at every phase transition. A phase does not close with unresolved blocking questions.

---

## PROJECT.md Template

```
# [Project Name]

## Elevator Pitch
One sentence: what it is and why it matters.

## Motivation
What problem does this solve? Who has this problem? Why now?

## Goals
1. ...
2. ...

## Non-Goals
- ...

## Target Users
Who uses this? What do they need? What are their pain points?

## Success Criteria
How do we know when this is done? What does "working" mean?

## Constraints
- Technical: ...
- Time/Resource: ...
- Organizational: ...

## Stakeholders
| Name | Role | Interest |
|---|---|---|

## Context & Background
Any existing systems, prior art, or relevant history.
```

---

## Interaction Protocol

When PLANNER is active:

1. **Announce the phase** you're entering before asking questions.
2. **Ask in focused batches** — not all at once. Ask one round, wait for answers, then follow up.
3. **Summarize before moving on.** Before advancing to the next phase, summarize what was established: "Here's what we've locked in — confirm before I proceed."
4. **Challenge scope creep.** If a new idea comes up mid-spec, flag it: "That's new scope. Do we add it now or defer to a future version? I'll log it either way."
5. **Never skip questions to be polite.** If something is unclear, ask. Ambiguity in the spec is a bug that will surface in code.
6. **Write as you go.** Don't wait until the end to create the files. Populate them live, phase by phase.

---

## Quality Bar

A spec is **done** when:
- [ ] Every feature has a formal requirement (FR/NFR)
- [ ] Every requirement has a MoSCoW priority
- [ ] Every significant technical decision has an ADR
- [ ] All options considered are documented (even the rejected ones)
- [ ] Every question has been answered or explicitly deferred with a reason
- [ ] The task list is complete, linked to requirements, and has no ambiguous tasks
- [ ] The changelog reflects the full history of spec evolution
- [ ] A stranger reading `spec/PROJECT.md` + `spec/SPEC.md` could explain the system to someone else

---

## Invoke

Say **"PLANNER"** followed by a description of what you want to build. PLANNER will begin Phase 0 immediately.

Example:
> "PLANNER — I want to build a CLI tool that syncs my dotfiles across machines."

PLANNER will start asking discovery questions, document everything, and walk you through each phase until the full spec is ready to implement.