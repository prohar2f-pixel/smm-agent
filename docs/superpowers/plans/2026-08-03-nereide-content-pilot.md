# NERÉIDE Text Content Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a review-ready, unpublished NERÉIDE content package for Instagram, Telegram, and VK that demonstrates AiProhar's phone-driven development process and invites qualified project enquiries.

**Architecture:** Keep strategy, copy, and the production manifest in `smm-agent`, while keeping generated binary media in `/home/agent/workspace/output/nereide-content-pilot/`. Use verified screenshots and frames from the NERÉIDE feature worktree as source evidence, render a consistent static carousel and Stories set, and package everything for owner review without invoking publishing code.

**Tech Stack:** Markdown, SVG, PNG, Python 3 standard library, FFmpeg with SVG input support, Playwright CLI with Chromium, Git.

## Global Constraints

- NERÉIDE must always be described as an own demonstration project, never as a client or operating watch brand.
- The primary audience is owners of service businesses and experts who sell services.
- The only primary CTA is: «Пришлите задачу — бесплатно подскажу, что вам действительно нужно: сайт, бот или автоматизация».
- The package contains no video, Reels, Shorts, clips, publishing dates, mandatory frequency, or automatic posting.
- Every factual claim must be traceable to the project at SHA `cffd1ec`: 16 WebP frames, reversible scroll behavior, mobile adaptation, reduced-motion fallback, capped DPR, nearest-loaded-frame fallback, and 12/12 passing tests.
- Do not expose secrets, private logs, personal data, environment variables, or unapproved third-party watch images.
- Do not call `publishing/`, `scheduler.py`, platform APIs, or bot publication commands.
- Instagram, Telegram, and VK must receive native copy, not the same text pasted three times.
- Generated media stays outside Git in `/home/agent/workspace/output/nereide-content-pilot/`; only text documentation and the source manifest are committed.

---

## File Map

- Create `docs/content-strategy/nereide-content-package.md`: strategic card, final Instagram carousel copy, Stories copy, Instagram caption, Telegram post, VK post, CTA, and measurement notes.
- Create `docs/content-strategy/nereide-source-manifest.md`: exact source paths, output filenames, dimensions, allowed claims, and privacy checks.
- Create `/home/agent/workspace/output/nereide-content-pilot/render_nereide_pilot.py`: deterministic SVG/PNG renderer using only Python standard-library text templating and FFmpeg conversion.
- Create `/home/agent/workspace/output/nereide-content-pilot/source/`: safe copies of selected NERÉIDE screenshots and frames.
- Create `/home/agent/workspace/output/nereide-content-pilot/instagram-carousel/`: nine PNG slides at 1080×1350.
- Create `/home/agent/workspace/output/nereide-content-pilot/stories/`: four PNG Stories at 1080×1920.
- Create `/home/agent/workspace/output/nereide-content-pilot/vk/`: one 1080×1350 cover and selected supporting images.
- Create `/home/agent/workspace/output/nereide-content-pilot/manifest.json`: machine-readable output inventory and dimensions.
- Create `/home/agent/workspace/output/nereide-content-pilot/nereide-content-pilot.zip`: owner-review archive.
- Modify `docs/content-strategy/README.md`: link the completed package after it passes review checks.

---

### Task 1: Build the verified source manifest

**Files:**
- Create: `docs/content-strategy/nereide-source-manifest.md`
- Create: `/home/agent/workspace/output/nereide-content-pilot/source/`

**Interfaces:**
- Consumes: `/home/agent/workspace/sites/nereide-watches/.worktrees/nereide-disassembly/sites/nereide-watches/`
- Produces: a reviewed list of source files with destination names used by Tasks 2 and 3.

- [ ] **Step 1: Create the output directories**

Run:

```bash
mkdir -p /home/agent/workspace/output/nereide-content-pilot/{source,instagram-carousel,stories,vk}
```

Expected: all four directories exist and are empty before source selection.

- [ ] **Step 2: Re-run the project evidence checks**

Run:

```bash
cd /home/agent/workspace/sites/nereide-watches/.worktrees/nereide-disassembly/sites/nereide-watches
node tests.mjs
find assets/disassembly -maxdepth 1 -type f -name 'frame-*.webp' | wc -l
```

Expected: from a clean checkout of SHA `cffd1ec`, TAP reports `pass 12`, `fail 0`; the frame count is `16`.

- [ ] **Step 3: Copy only first-party project evidence**

Copy these exact project files into `source/` with stable names:

```bash
pilot_src=/home/agent/workspace/sites/nereide-watches/.worktrees/nereide-disassembly/sites/nereide-watches
pilot_out=/home/agent/workspace/output/nereide-content-pilot/source
cp "$pilot_src/assets/watch-hero.png" "$pilot_out/01-hero.png"
cp "$pilot_src/assets/disassembly/frame-00.webp" "$pilot_out/02-assembled.webp"
cp "$pilot_src/assets/disassembly/frame-05.webp" "$pilot_out/03-opening.webp"
cp "$pilot_src/assets/disassembly/frame-10.webp" "$pilot_out/04-mechanism.webp"
cp "$pilot_src/assets/disassembly/frame-15.webp" "$pilot_out/05-disassembled.webp"
```

Also capture one desktop and one mobile screenshot from the current local project build:

```bash
pilot_src=/home/agent/workspace/sites/nereide-watches/.worktrees/nereide-disassembly/sites/nereide-watches
pilot_out=/home/agent/workspace/output/nereide-content-pilot/source
npx --yes playwright@latest install chromium
python3 -m http.server 4173 --directory "$pilot_src" >/tmp/nereide-content-http.log 2>&1 &
pilot_server_pid=$!
npx --yes playwright@latest screenshot --browser chromium --full-page --viewport-size="1440,1200" http://127.0.0.1:4173/ "$pilot_out/06-desktop-result.png"
npx --yes playwright@latest screenshot --browser chromium --full-page --device="iPhone 13" http://127.0.0.1:4173/ "$pilot_out/07-mobile-result.png"
kill "$pilot_server_pid"
```

Inspect both screenshots and confirm that neither contains personal data, secrets, browser chrome, loading-state overlays, or third-party images presented as client work.

- [ ] **Step 4: Write the source manifest**

Document for each source: original absolute path, destination filename, whether it is generated or captured, allowed claims it supports, and the result of a personal-data/secret scan. Include this exact truth boundary:

```markdown
NERÉIDE — собственный демонстрационный проект AiProhar. Материалы не подтверждают продажи, конверсию, клиентский результат, срок разработки или коммерческую деятельность часового бренда.
```

- [ ] **Step 5: Verify the manifest and source set**

Run:

```bash
test "$(find /home/agent/workspace/output/nereide-content-pilot/source -maxdepth 1 -type f | wc -l)" -eq 7
rg -n "16 WebP|12/12|cffd1ec|демонстрационный проект|персональ|секрет" docs/content-strategy/nereide-source-manifest.md
git diff --check
```

Expected: seven source files; all truth-boundary terms found; no whitespace errors.

- [ ] **Step 6: Commit the source manifest**

Run:

```bash
git add docs/content-strategy/nereide-source-manifest.md
git commit -m "docs: inventory NEREIDE content sources"
```

---

### Task 2: Write the native three-platform content package

**Files:**
- Create: `docs/content-strategy/nereide-content-package.md`

**Interfaces:**
- Consumes: `docs/content-strategy/nereide-pilot.md` and `docs/content-strategy/nereide-source-manifest.md`.
- Produces: approved copy blocks and exact slide/story text consumed by the renderer in Task 3.

- [ ] **Step 1: Write the strategic card**

Start the file with these fields and resolved values:

```markdown
- Цель: квалифицированные входящие обращения.
- Аудитория: владельцы сервисного бизнеса и эксперты, продающие услуги.
- Серия: «С телефона в прод».
- Мастер-идея: путь от идеи до работающего и проверенного демонстрационного сайта.
- Доказательство: рабочий NERÉIDE, 16 кадров, мобильная адаптация и 12/12 тестов на SHA `cffd1ec`.
- CTA: «Пришлите задачу — бесплатно подскажу, что вам действительно нужно: сайт, бот или автоматизация».
```

- [ ] **Step 2: Write the nine-slide Instagram carousel**

Use the Demo Walkthrough structure with exactly these slide jobs:

```text
01 — finished result and hook
02 — why this is a demonstration project
03 — five-step process overview
04 — idea and visual direction
05 — 16-frame reversible disassembly
06 — mobile and accessibility decisions
07 — what AI accelerated and what required human judgment
08 — 12/12 checks and honest limitations
09 — positioning conclusion and the single CTA
```

Each slide must contain one headline of at most 9 words and body copy of at most 28 words. Slide 1 must explicitly say «демонстрационный сайт» or «демопроект»; slide 9 must contain the full CTA and no second action.

- [ ] **Step 3: Write four Instagram Stories**

Use this sequence:

```text
01 — hook: the site was managed from a phone
02 — tension: a beautiful AI image is not a finished site
03 — proof: 16 frames, mobile adaptation, 12/12 checks
04 — one question sticker prompt leading to the same CTA
```

Limit on-screen copy to three short text groups per Story and mark the interaction type for Story 4 as `question sticker`.

- [ ] **Step 4: Write the Instagram caption**

Write 120–180 Russian words. The first line must stand alone as a hook; the body must distinguish AI assistance from developer responsibility; the final paragraph must contain only the primary CTA. Do not include external links or invented hashtags.

- [ ] **Step 5: Write the Telegram post**

Write 450–700 Russian words with short paragraphs and five sections: result, task, decisions, checks, business conclusion. Explain `prefers-reduced-motion` and DPR in plain language. End with the primary CTA and no engagement bait.

- [ ] **Step 6: Write the VK post**

Write 250–400 Russian words as a self-contained case. Open with the business meaning, show the five-step path, name NERÉIDE as a demo, and end with the primary CTA. Do not tell readers to leave VK to understand the case.

- [ ] **Step 7: Run copy quality checks**

Run:

```bash
rg -n "NERÉIDE|демонстрацион|16|12/12|Пришлите задачу" docs/content-strategy/nereide-content-package.md
if rg -ni "клиентский кейс|бренд заказчика|продажи выросли|конверсия выросла|гарантир|успешный успех|революцион" docs/content-strategy/nereide-content-package.md; then exit 1; fi
git diff --check
```

Expected: verified proof and CTA terms are present; prohibited claims are absent; no whitespace errors.

- [ ] **Step 8: Commit the copy package**

Run:

```bash
git add docs/content-strategy/nereide-content-package.md
git commit -m "docs: write NEREIDE platform content"
```

---

### Task 3: Render the Instagram carousel and Stories

**Files:**
- Create: `/home/agent/workspace/output/nereide-content-pilot/render_nereide_pilot.py`
- Create: `/home/agent/workspace/output/nereide-content-pilot/instagram-carousel/slide-01.png` through `slide-09.png`
- Create: `/home/agent/workspace/output/nereide-content-pilot/stories/story-01.png` through `story-04.png`
- Create: `/home/agent/workspace/output/nereide-content-pilot/manifest.json`

**Interfaces:**
- Consumes: the exact carousel and Story copy from `docs/content-strategy/nereide-content-package.md`, plus the seven files listed in `docs/content-strategy/nereide-source-manifest.md`.
- Produces: nine 1080×1350 PNG carousel slides, four 1080×1920 PNG Stories, and a manifest whose entries have `path`, `width`, `height`, `platform`, and `sourceEvidence` fields.

- [ ] **Step 1: Write a render-input assertion before the renderer**

At the top of `render_nereide_pilot.py`, define the nine carousel records and four Story records explicitly. Add assertions:

```python
assert len(CAROUSEL) == 9
assert len(STORIES) == 4
assert all(len(item["headline"].split()) <= 9 for item in CAROUSEL)
assert CAROUSEL[-1]["body"] == PRIMARY_CTA
assert STORIES[-1]["interaction"] == "question sticker"
```

- [ ] **Step 2: Verify the assertions fail before the records are complete**

Run:

```bash
python3 /home/agent/workspace/output/nereide-content-pilot/render_nereide_pilot.py
```

Expected: an assertion failure while the records are incomplete.

- [ ] **Step 3: Implement the deterministic SVG renderer**

The renderer must:

```text
use #050908 as the base background, #EDF0E8 as primary text, #C8A76A as accent;
use DejaVu Serif Bold for display text and DejaVu Sans for body text;
apply one consistent layout to all interior carousel slides;
embed or reference only files from source/;
keep carousel text inside x=80..1000 and y=90..1260;
keep Story text inside x=72..1008 and y=250..1610;
export intermediate SVG files beside each PNG;
invoke FFmpeg once per SVG to create the PNG;
write manifest.json after all conversions succeed.
```

The first carousel slide shows the finished site result; slides 4–8 use the matching source evidence; the final slide prioritizes the CTA over decoration. Stories use the same palette but may use different crops from the carousel.

- [ ] **Step 4: Run the renderer**

Run:

```bash
python3 /home/agent/workspace/output/nereide-content-pilot/render_nereide_pilot.py
```

Expected: exit code 0; nine carousel PNGs, four Story PNGs, and `manifest.json` are created.

- [ ] **Step 5: Verify counts, dimensions, and manifest fields**

Run:

```bash
test "$(find /home/agent/workspace/output/nereide-content-pilot/instagram-carousel -maxdepth 1 -name 'slide-*.png' | wc -l)" -eq 9
test "$(find /home/agent/workspace/output/nereide-content-pilot/stories -maxdepth 1 -name 'story-*.png' | wc -l)" -eq 4
ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 /home/agent/workspace/output/nereide-content-pilot/instagram-carousel/slide-01.png
ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 /home/agent/workspace/output/nereide-content-pilot/stories/story-01.png
python3 -m json.tool /home/agent/workspace/output/nereide-content-pilot/manifest.json >/dev/null
```

Expected: counts are 9 and 4; dimensions are `1080x1350` and `1080x1920`; JSON is valid.

- [ ] **Step 6: Inspect the representative and boundary images**

Open and inspect these five PNGs at original detail:

```text
instagram-carousel/slide-01.png
instagram-carousel/slide-05.png
instagram-carousel/slide-09.png
stories/story-01.png
stories/story-04.png
```

Confirm: no clipped text, no unreadable source crop, one clear hierarchy, no text in Story forbidden zones, and no third-party branding presented as AiProhar work.

---

### Task 4: Prepare the VK image set and review archive

**Files:**
- Create: `/home/agent/workspace/output/nereide-content-pilot/vk/cover.png`
- Create: `/home/agent/workspace/output/nereide-content-pilot/vk/result-desktop.png`
- Create: `/home/agent/workspace/output/nereide-content-pilot/vk/result-mobile.png`
- Create: `/home/agent/workspace/output/nereide-content-pilot/README.txt`
- Create: `/home/agent/workspace/output/nereide-content-pilot/nereide-content-pilot.zip`
- Modify: `docs/content-strategy/README.md`

**Interfaces:**
- Consumes: verified output from Task 3 and final copy from Task 2.
- Produces: one owner-review archive and a documentation link indicating the package is ready for review, not publishing.

- [ ] **Step 1: Build the VK set without duplicating Telegram layout**

Use carousel `slide-01.png` as the basis for `vk/cover.png`, and copy the clean desktop/mobile result screenshots as supporting images. The VK post remains the primary explanation; these images support it rather than carrying the entire text.

- [ ] **Step 2: Write the archive README**

Include:

```text
STATUS: REVIEW ONLY — NOT APPROVED FOR PUBLICATION
Instagram: 9 carousel PNGs + 4 Story PNGs + caption in nereide-content-package.md
Telegram: post and gallery order in nereide-content-package.md
VK: post and 3 supporting images
Primary CTA: Пришлите задачу — бесплатно подскажу, что вам действительно нужно: сайт, бот или автоматизация
```

- [ ] **Step 3: Link the completed content package from strategy README**

Add `nereide-content-package.md` to the document table with status wording «пакет для предварительного просмотра». Do not change the publication boundary.

- [ ] **Step 4: Run final package checks**

Run:

```bash
git diff --check
rg -n "публикац|автопост|предварительного просмотра" docs/content-strategy/README.md docs/content-strategy/nereide-content-package.md
python3 -m json.tool /home/agent/workspace/output/nereide-content-pilot/manifest.json >/dev/null
find /home/agent/workspace/output/nereide-content-pilot -type f -size 0 -print
```

Expected: no diff errors, publication boundaries remain explicit, manifest is valid, and no zero-byte files are reported.

- [ ] **Step 5: Create and verify the ZIP**

Run:

```bash
cd /home/agent/workspace/output/nereide-content-pilot
zip -r nereide-content-pilot.zip README.txt manifest.json instagram-carousel stories vk
unzip -t nereide-content-pilot.zip
```

Expected: `No errors detected in compressed data`.

- [ ] **Step 6: Commit the package documentation**

Run:

```bash
git add docs/content-strategy/README.md
git commit -m "docs: mark NEREIDE content package ready for review"
```

---

### Task 5: Final pre-publication review gate

**Files:**
- Review: `docs/content-strategy/nereide-pilot.md`
- Review: `docs/content-strategy/nereide-source-manifest.md`
- Review: `docs/content-strategy/nereide-content-package.md`
- Review: `/home/agent/workspace/output/nereide-content-pilot/nereide-content-pilot.zip`

**Interfaces:**
- Consumes: all previous task outputs.
- Produces: a concise owner handoff with files attached and no publication action.

- [ ] **Step 1: Compare package coverage against the approved specification**

Confirm every required deliverable exists: strategic card, nine-slide carousel, four Stories, Instagram caption, Telegram post, VK post, source manifest, metrics, visual assets, and archive.

- [ ] **Step 2: Run the final truth and boundary scan**

Run:

```bash
rg -n "демонстрацион|16|12/12|cffd1ec|Пришлите задачу" docs/content-strategy/nereide-{pilot,source-manifest,content-package}.md
if rg -ni "клиентский кейс|бренд заказчика|продажи выросли|конверсия выросла|автоматически опублик" docs/content-strategy/nereide-{pilot,source-manifest,content-package}.md; then exit 1; fi
git status --short --branch
```

Expected: proof and CTA are present, prohibited claims are absent, and only expected committed work exists.

- [ ] **Step 3: Hand off for owner review**

Attach the ZIP and representative images in Telegram. State explicitly that nothing has been published and ask for either revisions or separate approval to publish later.
