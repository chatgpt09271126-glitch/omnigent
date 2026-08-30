# Auto Code Cards for Interview Mode

## Context

Interview Mode is used for practice/mock interviews: the candidate has
Omnigent open on their phone during the interview, while a coach on a
separate device transcribes the interviewer's question into the thread and
gets an LLM answer back. The candidate needs to scan that answer in short,
repeated glances without it looking obvious.

Prose answers are readable enough as-is. Code is the problem: code blocks
wrap awkwardly on a phone screen and are hard to read at a glance. The
existing workaround is manual — the coach takes a desktop screenshot of the
code (in a nicely-formatted editor) and uploads it via the existing "code
snapshot" feature, which the candidate can then view full-screen with
pinch-zoom/pan (`CodeSnapshots.tsx`, `SnapshotViewer`). That works well when
someone remembers to do it, but it's manual, slow, and depends on the coach's
desktop workflow.

This spec adds automatic code cards: code blocks in agent responses get
turned into the same kind of zoomable snapshot images automatically, no
coach action required, without touching the manual screenshot feature.

## Goals

- Zero manual effort: every code block in an agent response becomes
  tappable, zoomable snapshot image(s) on its own.
- Never delay the visible answer: card generation happens after the message
  finishes streaming, fully off the critical path.
- Long code blocks split into multiple cards with a few lines of overlap
  between consecutive cards, so swiping forward never loses your place.
- Fix the existing snapshot viewer's broken navigation (invisible prev/next
  buttons, no working swipe gesture) since multi-card code blocks make this
  the primary interaction.
- Leave the manual screenshot feature completely untouched.

## Non-goals

- Reformatting/summarizing code content (this is a presentation-only
  feature; the LLM's actual output doesn't change).
- Structure-aware pagination (splitting at function boundaries). Fixed
  line-count windows are good enough and much simpler.
- Changes to the grid gallery's layout — only the full-screen viewer's
  navigation is being fixed.

## Design

### 1. Pagination: sliding-window overlap

For each code block, once the message finishes streaming:

- If the block's line count fits within one card's budget (a fixed
  constant, e.g. ~20 lines), render a single card.
- Otherwise, split into a sliding window: card *N* starts a fixed number of
  lines (the overlap, e.g. 3) before card *N-1* ended. E.g. with a 20-line
  page size and 3-line overlap: card 1 = lines 1-20, card 2 = lines 18-37,
  card 3 = lines 35-54, etc.
- Page size and overlap are both simple constants, not adaptive to code
  structure — consistent with the "fixed line count" choice already made
  for the interview-answer pagination question.

### 2. Rendering: real raster images, reusing the existing pipeline

Auto cards are rendered to actual PNG images — not a live HTML/DOM
component — and uploaded through the same artifact/storage path the manual
screenshot feature already uses (`useCodeSnapshotBlock`'s `save`, the
`code_snapshots` store, etc.). This means:

- `SnapshotViewer`'s pinch-zoom/pan already works on them; no new zoom code.
- They show up in the same gallery grid as manual screenshots, with a
  `capture_type` of `auto_code_card` (vs. `uploaded_image`/`clipboard_image`
  for manual) so the two are visually/semantically distinguishable if ever
  needed, but otherwise behave identically as gallery entries.
- Visual styling mirrors the reference screenshot the coach uses today:
  dark background, generous font/line spacing, language label — rendered
  off-screen and rasterized (e.g. via a headless canvas render), not styled
  to look like a live app surface.

A raster image loses some crispness at extreme zoom compared to live text,
but this is the same tradeoff the existing manual-screenshot workflow
already has, and it already works well in practice.

### 3. Timing: async, after stream completion

Code block rendering in the chat is completely unaffected — the answer
appears exactly as fast as it does today. Once the message finishes
streaming, a background step (server-side) detects code blocks, paginates,
rasterizes, and uploads each card as a snapshot. Until those cards exist,
the code block in the chat shows a small pending/loading affordance; once
ready, it becomes tappable.

### 4. Live glance: tap-through bypasses the grid

Tapping a code block in the live chat opens the full-screen swipeable
viewer directly, scoped to just that code block's card sequence (e.g. "1 of
3"), skipping the grid entirely. The grid gallery remains for browsing the
full session afterward (debrief), showing both manual screenshots and auto
cards together, and is unchanged in this spec except that the viewer it
opens into gets the navigation fix below.

### 5. Viewer navigation fix

Current state (confirmed in `CodeSnapshots.tsx`): the prev/next buttons are
`className="sr-only"` — invisible — and there's no swipe-to-navigate
gesture. The pointer handlers that exist only do pinch/pan-zoom of the
current image, which is what makes swiping feel "glitchy" (it's panning the
zoomed image, not switching images).

Fix:

- Add a real swipe-to-navigate gesture: a horizontal drag past a threshold
  (while not zoomed in past 1x) triggers `changeIndex` with a snap
  animation, distinct from the existing pinch/pan/zoom handling.
- Make the prev/next chevrons visible and tappable (remove `sr-only`,
  style consistently with the existing close/back buttons), so navigation
  works even without a swipe gesture.
- Keep the existing "N of M" counter in the header as the primary position
  indicator.

## Data flow summary

```
Agent message finishes streaming
        |
        v
Chat renders text answer immediately (unaffected)
        |
        v
Background job: detect code blocks in the finished message
        |
        v
For each code block: paginate (sliding window) -> rasterize each page
        |
        v
Upload each page via existing snapshot artifact pipeline
  (capture_type = auto_code_card)
        |
        v
Code block in chat becomes tappable -> opens SnapshotViewer
  scoped to this block's cards, swipe/chevron navigation fixed
```

## Testing / verification

- Manual: run a mock interview session, ask the agent a question that
  returns a >20-line code block, confirm multiple cards are generated with
  overlapping content at the boundaries, and that swiping between them
  keeps a shared anchor line visible.
- Manual: confirm the manual screenshot feature (desktop capture -> upload)
  still works unchanged.
- Manual: confirm tapping a code block in chat opens directly into the
  swipe viewer (not the grid), and that the grid still shows both manual
  and auto entries together for a session.
- Manual: on an actual iOS device, confirm swipe-to-navigate is smooth (no
  conflict with pinch/pan-zoom) and the prev/next chevrons are visible and
  tappable.
