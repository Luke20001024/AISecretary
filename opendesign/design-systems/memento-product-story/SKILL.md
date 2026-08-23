---
name: memento-product-story
description: Build Memento product-story interfaces from the real cognitive landscape product, its code, screenshots, product-state document, and horizon poster language
---

# Memento Product Story

Use this system for Memento homepage storytelling, product explainers, launch pages, and static hero compositions.

## Source hierarchy

1. Read the current `cognitive-home` implementation before styling a new surface
2. Reuse actual palette, typography roles, borders, map vocabulary, and component density
3. Use real product screenshots for product claims
4. Use the explicit horizon poster for cover atmosphere only
5. Do not let a general design trend override current product evidence

## Visual system

- Import `tokens/colors_and_type.css`
- Use warm paper, ink, and one cold blue family
- Use Georgia or Times only for brand and editorial display text
- Use system Chinese sans for interface text
- Use monospaced text for date, source, index, state, and provenance
- Prefer hairline divisions and open spatial groups
- Reserve pill shapes for compact camera controls or small state switches already present in the product
- Keep corner radii small on windows and controls
- Keep shadows subtle and limited to true floating layers

## Image system

- Preserve the real application chrome, data density, labels, contours, and interaction cues visible in screenshots
- Allowed transformations: crop, mask, redact, annotate, scale, overlap, and slight perspective up to two degrees
- Keep screenshots readable at the point where the story depends on them
- Add a small source label and a blue evidence marker when a crop enters the narrative
- Unlabelled synthetic app screens, fake product demos, decorative device mockups, and generic SaaS dashboards are out of scope
- A labelled scene reconstruction is allowed on a narrative page when real source screenshots are unavailable; build it as replaceable HTML layers, keep app claims generic, and never present it as captured personal data or a shipped Memento interaction

## Story-only derivatives

These components may be built for the explanation page using existing product primitives:

- **Evidence window**: a real window crop, app/source name, time, one highlighted intent
- **Context trace**: a fine blue line connecting a highlighted sentence to the record flow
- **Interrupted trace**: a visibly broken line showing that fragments have not been unified
- **Terrain response**: contours that appear progressively as evidence accumulates
- **Formation chain**: original record, AI interpretation, supporting or revising evidence, and current understanding
- **Local boundary**: a restrained frame showing local data storage and external AI access through MCP

These are narrative compositions. They must not imply product interactions that do not exist.

## Motion grammar

- Use motion to reveal cause and effect: fragment appears, trace connects, evidence enters, contour grows, understanding updates
- Animate opacity, transform, stroke dash offset, and clip path only when practical
- Control feedback lasts about 180ms
- Narrative transitions last 500–900ms with `cubic-bezier(.2,.75,.3,1)`
- Avoid looping decorative motion
- Respect `prefers-reduced-motion`

## Content rules

- Use compact Chinese phrases and short paragraphs
- Give each chapter an evocative main title and a functional subtitle
- Explain what happens, how it happens, and why it matters
- Keep the three product values exact: `接住正在发生的意图`, `长期理解你的形状`, `让每个 AI 都从同一个你开始`
- Name the third capability `可调用的个人记忆`; keep MCP, authorization scope, and audit in mechanism copy
- Do not use `让理解被授权调用`, `支持多端接入`, or `个人上下文接口` as user-facing value headlines
- Avoid personality verdicts and certainty unsupported by evidence
- Distinguish current preview capability from future automation and MCP capability
- Omit sentence-ending punctuation in on-page display copy

## Page density

- One cover plus three compact acts is the default ceiling
- Each act needs one dominant visual and one concise explanatory block
- Do not duplicate the same claim across sections
- On desktop, use approximately 56–72px side padding and keep the narrative line length below 30 Chinese characters
- On mobile, preserve the order of evidence, formation, understanding, access

## Build checklist

- [ ] Current product code was checked before styling
- [ ] Every screenshot is real and traceable to a source file
- [ ] Private content is redacted or approved
- [ ] Screenshot text remains legible
- [ ] The page uses product blue, ink, paper, and hairlines consistently
- [ ] The map is recognizably the current Memento map
- [ ] Motion explains a state transition
- [ ] Current and future capabilities are labelled accurately
- [ ] Reduced motion and keyboard focus are supported
