# Generated-task review guide — verifying & hardening a synthetic HiL-Bench task

This is the workflow for a contributor who is handed a **synthetically generated task** — a
complete, gate-passing `deliverables/` folder authored by the pipeline's generator, not something
you authored yourself. Your job is to **independently verify it, harden it, and either improve it
in place or regenerate it**.

> **You own the task once you accept it.** The generator self-graded this task to PASS, but a
> self-graded PASS can hide a weak or guessable blocker. Approving it makes it yours — so
> double-check everything, especially anything you change.

> **Make sure this is the right guide.** This one is for a *generated* task you're verifying before
> it enters the benchmark. If instead you're triaging a *human-attempted, already-submitted* task
> against the async evaluation sheet, use [`REVIEWER_GUIDE.md`](./REVIEWER_GUIDE.md). The critical
> difference here: you must verify **both** checks yourself, and **re-verify both after every edit**
> (see the rule below). For pipeline/command mechanics see
> [`CONTRIBUTOR_GUIDE.md`](./CONTRIBUTOR_GUIDE.md) and
> [`COMMAND_REFERENCE.md`](./COMMAND_REFERENCE.md).

---

## What you receive

A full `deliverables/` folder for a task whose gates and both checks already PASS:

```
blocker_registry.json / .md
modified_problem_statement.txt / modified_requirements.txt / modified_public_interfaces.txt
golden_patch_obstructed.diff / test_patch_obstructed.diff / relevant_tests.txt   (setup_patch.diff if Scenario 2)
validate_obstructed_result.txt / plan.md / Dockerfile / .hilbench_env
```

Two facts that shape the workflow:

- **Tasks are uploaded only when both checks pass** — so you are *verifying and hardening*, not
  triaging a known failure. Treat the PASS as unproven until you reproduce it.
- **Whether you also receive the async check1/check2 results (or the `check*_agent_clean.diff`
  agent solves) is not guaranteed.** Do **not** depend on them — self-verify both checks in the
  sandbox regardless. If they *are* shipped, use them only as a diagnostic head-start.

---

## The one rule that defines this workflow

**Verify BOTH checks, and re-verify BOTH after every edit — the two checks pull against each
other.** A fix aimed at one routinely regresses the other:

| You edit to fix… | How you fix it | What it can silently regress |
|---|---|---|
| **Check 2** (`FAIL_UNSOLVABLE`) | add clarity/detail to the spec or resolution so agents can solve *with* resolutions | the added detail leaks the answer → a blocker becomes guessable → **Check 1 → `FAIL_GUESSABLE`** |
| **Check 1** (`FAIL_GUESSABLE`) | diverge the resolution to a non-obvious value; strip leakage from spec/identifiers | the task becomes unsolvable even *with* resolutions → **Check 2 → `FAIL_UNSOLVABLE`** |

So there is no such thing as fixing "just Check 1" or "just Check 2." Every change is followed by
re-running **both**. If you edited anything, only accept the task once Check 1 and Check 2 both
pass on that latest version.

---

## 0. Provision the sandbox

You need the full sandbox (container + Harbor) to reproduce the checks. In the **chat window**:

```
/hilbench-provision
```

Then, in the **terminal**:

```bash
source deliverables/.hilbench_env
```

(See the Stage 0 notes in `CONTRIBUTOR_GUIDE.md` for image-source / auto-checkout behavior.)

---

## 1. Audit the task yourself (don't trust the PASS)

Reproduce every signal before deciding anything.

**Static evals** — run all 12 verbatim (same prompts the gates use), read-only:

```
/hilbench-run-evals                # all 12 → EVAL <name>: PASS|FAIL + summary
/hilbench-run-eval <eval_name>     # one, for a focused look
```

**Both checks** — reproduce them in the container. Iterate cheaply with the in-container
pre-screens, then confirm with the authoritative Harbor grade:

```
/hilbench-check1                   # fast pre-screen: is any blocker guessable (no-ask solve)?
/hilbench-check2                   # fast pre-screen: solvable WITH resolutions?
/hilbench-evaluate-full            # authoritative Harbor grade (incremental); --fresh for full
```

**Watch the memorization trap on Check 1.** These are well-known OSS repos at publicly known
commits, so a resolution-free solver can recognize the repo and reconstruct the *real* upstream
behavior from memory — "guessing" a blocker even though nothing in your task leaked the answer. If
a blocker's correct resolution equals the obvious/real upstream behavior, it is guessable and Check
1 will fail it. The fix is to make the resolution a **non-standard, project-specific value that
diverges from the real upstream default**, with the tests asserting your value. This is the
guessability criterion (eval 7 / Test B in `references/eval-prompts.md`); the empirical no-ask
Check 1 run is the decisive test.

---

## 2. Where each failing signal lives (fix map)

Diagnosis only — you decide whether to improve or regenerate. Full detail in
[`references/reviewer-evals.md`](./references/reviewer-evals.md) and the repair taxonomy in
[`references/optional-repair.md`](./references/optional-repair.md).

| Failing signal | Usually lives in | Note |
|---|---|---|
| `eval_request` | modified problem statement / requirements / interfaces | remove the leak/embedded solution |
| `eval_golden_patch` | `golden_patch_obstructed.diff` | missing impl or out-of-scope change |
| `eval_test_list` / `eval_test_relevance` | `test_patch_obstructed.diff`, `relevant_tests.txt` | tighten/add — never weaken |
| `eval_blocker_critical_implementation` (Test A) | golden / tests / the relevant resolution | resolution must be test-required |
| `eval_blocker_objective` / `_descriptions` / `_questions` / `_type` / `eval_self_reference` | `blocker_registry.json` | wording of one blocker |
| `eval_blocker_independence` | `blocker_registry.json` | coupled resolutions — often a regenerate |
| `eval_blocker_distribution` | `blocker_registry.json` | wrong type counts — often a regenerate |
| **Check 1 `FAIL_GUESSABLE`** | `blocker_registry.json` resolution + modified spec | diverge to a non-obvious value; strip leakage — then re-check Check 2 |
| **Check 2 `FAIL_UNSOLVABLE`** | modified spec / resolution / golden | add minimal missing detail — then re-check Check 1 |

---

## 3. Decide: improve or regenerate

There is no attempter to send the task back to — the choice is yours:

- **Improve in place** when the issue is localized (one blocker's wording, a missing spec detail,
  a resolution that needs to diverge from the upstream default). See §4.
- **Regenerate specific blockers** when one or a few blockers are weak/guessable but the rest are
  fine — `/hilbench-registry-regenerate <id[,id...]>`. It re-does only those blockers (constrained
  to stay independent of and non-overlapping with the ones you keep) and merges them back. See §5.
- **Regenerate the whole task** when the problems are systemic — several guessable/coupled
  blockers, or a wrong distribution that needs new blockers — `/hilbench-registry-generate`
  (destructive: overwrites the entire registry). Then re-inject + revalidate.

---

## 4. Fix, then re-verify BOTH checks

**Edit the canonical files in place.** Overwrite `blocker_registry.json`,
`modified_*`, and the obstructed patches directly — every tool (`/hilbench-run-eval`, the gates,
`/hilbench-evaluate-full`) reads those exact filenames. The pristine generated original is
preserved in the upload/generation store, so no local backup is needed. Follow the no-regression
rules in `references/optional-repair.md` (smallest change; never weaken tests; no new leaks or
self-reference).

Then run the **regression loop** — this is the spine of the workflow:

1. Re-run the static eval(s) you touched: `/hilbench-run-eval <name>` (or `/hilbench-run-evals`).
2. **Re-run the check you fixed AND the other one.** Fixed Check 2? Re-run Check 1 too, and vice
   versa. Use the fast pre-screens (`/hilbench-check1`, `/hilbench-check2`) while iterating.
3. Repeat until both are clean at the same time.
4. **Final sign-off:** `/hilbench-evaluate-full --fresh` — a full authoritative Harbor grade with
   no incremental skips, so you accept the task only on a complete, current both-checks pass.

Nothing auto-advances — you decide when the task is clean enough to accept.

---

## 5. Regenerate a blocker (instead of hand-fixing)

When a blocker is fundamentally weak — most often `FAIL_GUESSABLE` from the memorization trap, or a
non-independent / mistyped blocker a reword can't save — regenerate it rather than hand-patching:

```
/hilbench-registry-regenerate <id[,id...]>
```

It re-does **only** the named blockers (keeping them independent of and non-overlapping with the
ones you keep, and type-locked so the distribution still holds), merges them back into the
registry, and leaves the retained blockers untouched. It does **not** touch the specs/patches —
those are *derived* from the registry, so you re-derive them with the normal pipeline. The command
prints this exact stream to run next (each stops for review):

```
/hilbench-validate-registry     # confirm the merged registry + full set
/hilbench-inject                # re-derive ALL modified specs + obstructed patches from the registry
/hilbench-validate-artifacts    # alignment map + patch-dependent evals
/hilbench-validate-obstructed   # dynamic FAIL→PASS on the re-authored patches
/hilbench-check1 ; /hilbench-check2   # both checks (the point of the regen)
/hilbench-evaluate-full --fresh # authoritative sign-off
```

> **Order matters.** `/hilbench-inject` regenerates *all* derived artifacts from scratch — it
> re-authors the unchanged blockers' specs/patches too (so the whole task re-passes the gates) and
> **overwrites any artifact-only manual fixes**. So regenerate + re-inject **first**, then do any
> artifact-only hand-tuning (a golden fix, a `setup_patch` tweak) — never the reverse.

For a systemic mess (many bad blockers / wrong distribution), regenerate the whole task with
`/hilbench-registry-generate` instead, then run the same stream.

---

## Track what you changed

As you go, **keep a running record of every change you make and why** — you will be asked to
submit a **brief summary of your changes and the rationale** for each. A change is anything you
edited from the generated original: a blocker resolution/description, a spec file, a patch, the
distribution, or a decision to regenerate.

Keep it lightweight — a few lines per change is enough, e.g.:

```
- blocker_registry.json (blocker_b): diverged resolution from the real upstream default (30s)
  to a project-specific 45s — Check 1 was FAIL_GUESSABLE (memorization trap).
- modified_requirements.txt: added the missing pagination-limit sentence — Check 2 was
  FAIL_UNSOLVABLE; re-checked Check 1 after, still clean.
- regenerated: 3 of 4 blockers were guessable, hand-fixing wasn't worth it.
```

If nothing was changed (the task verified clean as delivered), say exactly that — "verified, no
changes." Note this record is your own change log; it is separate from the artifacts under review
and is not one of the canonical deliverable files.

---

## Quick reference

```
/hilbench-provision                 # 0  full sandbox (container + Harbor)
source deliverables/.hilbench_env   # terminal
/hilbench-run-evals                 # 1  audit: all 12 static evals
/hilbench-check1 ; /hilbench-check2 #     audit: reproduce BOTH checks (watch memorization trap)
# decide: improve in place  vs  regenerate blocker(s)  vs  regenerate whole task
# improve: edit canonical files in place (see optional-repair.md)
/hilbench-registry-regenerate <ids> # 5  regenerate specific blockers → then re-inject + revalidate
/hilbench-inject                    #     re-derive specs/patches (after any registry regen)
/hilbench-run-eval <name>           # 4  re-verify the eval you touched
/hilbench-check1 ; /hilbench-check2 #     re-verify BOTH checks after EVERY edit
/hilbench-evaluate-full --fresh     #     final authoritative sign-off
# log each change + why as you go (you'll submit a brief change summary; "verified, no changes" if none)
```
