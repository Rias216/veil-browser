# DCR Scoring Rubric

Six dimensions, each scored 0–10. Aggregate = weighted mean.

---

## Dimension weights

| Dimension              | Weight | Rationale                                      |
|------------------------|--------|------------------------------------------------|
| Requirement Coverage   | 20%    | Unmet requirements are fatal bugs              |
| Correctness            | 25%    | Highest weight — wrong output is worse than slow |
| Logic                  | 20%    | Structural flaws cascade into correctness issues |
| Performance            | 15%    | Matters in production; tolerable in scripts    |
| Maintainability        | 10%    | Important but rarely blocks immediate use      |
| User Intent Alignment  | 10%    | Catches "technically correct but wrong thing"  |

---

## Within-range scoring tiebreaker

All anchors use bands (e.g., 8-9, 6-7). To pick the exact integer:

- **Upper** of the band (9, 7, 5...): the weakness theoretically exists but would not
  manifest under normal, realistic use.
- **Lower** of the band (8, 6, 4...): the weakness would realistically manifest given
  typical inputs or usage patterns.

Apply this rule consistently across cycles — the same output must score the same integer
on every re-evaluation, because accurate deltas are required for plateau detection in
--chase mode.

---

## Content-type reframing

The default dimension definitions assume code. For other content types, reframe
Performance and Logic/Maintainability before scoring:

| Dimension       | Code                              | Prose / Long-form content       | Prompts / Instructions          |
|-----------------|-----------------------------------|---------------------------------|---------------------------------|
| Performance     | Computational efficiency, O(n)    | Conciseness - no padding or filler | Instruction efficiency - no redundant or contradictory constraints |
| Logic           | Control flow, algorithm soundness | Argument coherence, structure   | Instruction ordering, completeness of edge-case handling |
| Maintainability | Readable, modifiable code         | Editable, well-structured text  | Reusable, parameterisable; easy to adjust scope |

The checklists below are written for code. When scoring prose or prompts, map each item
to its nearest equivalent from the table above and skip items that have no analogue.

---

## RC vs UIA disambiguation

Requirement Coverage (RC) and User Intent Alignment (UIA) can feel similar. Use this rule:

- **RC owns the issue** if it is about *what* was asked for — something explicitly or
  implicitly requested was not delivered.
- **UIA owns the issue** if it is about *how* — the right thing was delivered but in the
  wrong style, abstraction level, format, or spirit.

Example: user asked for a Python function; output is JavaScript = RC (wrong format).
User asked for a Python function; output is a 400-line class when a 10-line function was
implied = UIA (wrong scope/abstraction).

Never penalise the same root issue under both dimensions. Assign it to whichever fits best
and note the decision in the critique.

---

## Dimension Definitions & Scoring Anchors

---

### 1. Requirement Coverage (20%)

**Definition**: Does the output address every stated and implied requirement from the prompt?

| Score | Anchor                                                                      |
|-------|-----------------------------------------------------------------------------|
| 10    | All explicit requirements met; all reasonable implicit requirements met     |
| 8-9   | All explicit met; 1 minor implicit requirement missed                       |
| 6-7   | Most explicit requirements met; 1 explicit or 2+ implicit missed            |
| 4-5   | 2-3 explicit requirements missing or partially implemented                  |
| 2-3   | Core requirement(s) missing; output is substantially incomplete             |
| 0-1   | Output barely addresses the prompt                                          |

**Checklist**:
- [ ] Does it match the language/framework/format requested?
- [ ] Does it handle the inputs/outputs described?
- [ ] Does it cover edge cases the user mentioned?
- [ ] Are constraints (performance, size, style) respected?

---

### 2. Correctness (25%)

**Definition**: Is the output factually and functionally correct? Would it work if used as-is?

| Score | Anchor                                                                      |
|-------|-----------------------------------------------------------------------------|
| 10    | Output is correct; no bugs, errors, or false statements found               |
| 8-9   | Correct for the happy path; 1 minor edge case bug                          |
| 6-7   | Works for most inputs; identifiable bug under realistic conditions          |
| 4-5   | Contains a bug that would cause failures in common use                      |
| 2-3   | Multiple bugs; output would fail most of the time                           |
| 0-1   | Output is broken or contains fundamental factual errors                     |

**Checklist (code)**:
- [ ] No syntax errors
- [ ] No off-by-one errors or incorrect boundary conditions
- [ ] No use of undefined variables or incorrect APIs
- [ ] Return values and types are correct
- [ ] Error paths don't silently swallow exceptions

**Checklist (prose/prompts)**:
- [ ] No factual inaccuracies
- [ ] No contradictions within the output
- [ ] Claims are supported or marked as assumptions

---

### 3. Logic (20%)

**Definition**: Is the reasoning, control flow, or structure sound? Would a senior reviewer approve the approach?

| Score | Anchor                                                                      |
|-------|-----------------------------------------------------------------------------|
| 10    | Elegant, clear control flow; correct algorithmic approach                   |
| 8-9   | Sound logic; minor structural redundancy or suboptimal branch               |
| 6-7   | Logic works but has a notable structural smell (e.g., unnecessary nesting)  |
| 4-5   | Logic flaw that could cause incorrect behavior in a non-obvious path        |
| 2-3   | Algorithmic mistake (wrong data structure, incorrect recursion base case)   |
| 0-1   | Logic is fundamentally broken or circular                                   |

**Checklist**:
- [ ] Are loops and conditionals structured correctly?
- [ ] Is the algorithm appropriate for the problem (e.g., not O(n3) when O(n) exists)?
- [ ] Are side effects handled correctly (mutations, global state)?
- [ ] Is async/await or error propagation handled correctly?

---

### 4. Performance (15%)

**Definition**: Is the output efficient enough for its intended context?

Note: "good enough" depends on context. A CLI script and a hot API path have different bars.
Infer context from the prompt. If unclear, assume production-grade web service.
For prose/prompts, see the content-type reframing table above.

| Score | Anchor                                                                      |
|-------|-----------------------------------------------------------------------------|
| 10    | Optimal or near-optimal for the context; no wasted work                    |
| 8-9   | Efficient; 1 minor unnecessary computation                                  |
| 6-7   | Works but has a known inefficiency (e.g., repeated DB calls in a loop)     |
| 4-5   | Clear bottleneck that would cause latency at scale                          |
| 2-3   | Algorithmic complexity makes it unusable at production scale                |
| 0-1   | Unusable even at tiny scale                                                 |

**Checklist**:
- [ ] No N+1 query patterns
- [ ] No unnecessary repeated computation (missing memoization/caching)
- [ ] Appropriate data structures (hash map vs list for lookups)
- [ ] No blocking I/O on hot paths (if async context)
- [ ] Memory usage is reasonable (no loading entire files into RAM unnecessarily)

---

### 5. Maintainability (10%)

**Definition**: Can a competent developer read, understand, and modify this output in 6 months?
For prose/prompts, substitute "developer" with "editor" and evaluate whether the content
can be updated without re-reading the original context.

| Score | Anchor                                                                      |
|-------|-----------------------------------------------------------------------------|
| 10    | Clean, readable, well-named, well-structured; self-documenting              |
| 8-9   | Readable; 1-2 naming or doc gaps                                            |
| 6-7   | Understandable but has a section that needs a comment or clearer naming     |
| 4-5   | Multiple hard-to-read sections; magic numbers; unclear responsibilities     |
| 2-3   | Hard to follow; requires deep context to modify safely                      |
| 0-1   | Obfuscated or no discernible structure                                      |

**Checklist**:
- [ ] Functions/variables are descriptively named
- [ ] Functions are single-responsibility (do one thing)
- [ ] No magic numbers or unexplained constants
- [ ] Complex logic has inline comments
- [ ] No dead code or unused imports
- [ ] Consistent style throughout

---

### 6. User Intent Alignment (10%)

**Definition**: Does this output match what the user actually wanted, even if the literal request is satisfied?

This dimension catches the gap between what was asked and what was meant.
Evidence: tone, scope, format, level of abstraction, chosen library/approach.
See RC vs UIA disambiguation above before scoring.

| Score | Anchor                                                                      |
|-------|-----------------------------------------------------------------------------|
| 10    | Output fits the user's evident goal perfectly; right abstraction level      |
| 8-9   | Mostly aligned; 1 minor mismatch in style, scope, or format                |
| 6-7   | Technically answers the request but misses the spirit (over/under-engineered)|
| 4-5   | Addresses the wrong level of abstraction or the wrong interpretation        |
| 2-3   | Output solves a different problem than what was intended                    |
| 0-1   | Completely misaligned with intent                                           |

**Checklist**:
- [ ] Is the scope right (not too minimal, not over-engineered)?
- [ ] Is the format what the user likely expects?
- [ ] Does it use the stack/language/tools the user implied?
- [ ] Does the tone/register match (casual script vs enterprise module)?
- [ ] Are there assumptions made that contradict the user's evident context?

---

## Aggregate Interpretation

| Aggregate | Meaning                | Action                                                          |
|-----------|------------------------|-----------------------------------------------------------------|
| 9.0-10.0  | Excellent              | Return as-is; --chase stops here                               |
| 7.5-8.9   | Good                   | Address the single weakest dimension; one more cycle may yield +0.3 to +0.5 |
| 6.0-7.4   | Acceptable             | Multiple dimensions need improvement; rewrite required          |
| 4.0-5.9   | Poor                   | Full rewrite; preserve only highest-scoring elements            |
| 0.0-3.9   | Failing                | Restart from scratch; do not reuse existing structure           |

---

## Gate Rule

If any single dimension scores 3 or below, it is a gate failure.
The rewrite must resolve gate failures before addressing anything else.

### Hard stops - output must not be returned until resolved

A gate failure on any of these three dimensions is a hard stop:

| Dimension            | Reason                                                        |
|----------------------|---------------------------------------------------------------|
| Correctness          | Broken output causes direct harm or wasted work for the user  |
| Requirement Coverage | Delivering the wrong thing is worse than delivering nothing   |
| Logic                | A broken algorithm produces wrong results just like a correctness bug - it just does so less obviously |

### Gate failure at safety cap (--chase only)

If --chase exhausts all 8 cycles while a hard-stop dimension is still 3 or below, the
output must not be silently returned. Prepend a prominent warning to the final output:

  WARNING - GATE FAILURE - NOT SAFE TO USE AS-IS
  Dimension: [name]  |  Score: [N]/10  |  Cycles run: 8
  The pipeline could not resolve this issue within the cycle limit.
  Review and fix manually before using this output.
