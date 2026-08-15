# KD UI Component Development Workflow

This document outlines the standard operating procedure for developing new
web components, using the Basecoat engine, and integrating them into your
personal UI library (KD UI).

## Current Integration Reality

KD UI and Basecoat are **not** linked to this project via Git submodules,
npm packages, or any dependency manager. They are integrated as **plain,
physically copied Jinja files**:

- `app/templates/basecoat/` — Basecoat engine macros (sidebar, tabs, dialog,
  select, combobox, dropdown-menu, toast, popover, command).
- `app/templates/components/` — your KD UI low-level components (card,
  alert, data_table, pagination, stat_card, etc.).
- `app/templates/blocks/` — full page/section compositions built from the
  above.

This means there is no automated "sync" step. Updating KD UI in a project
is a manual copy-paste (or a small script, see Phase 4 below) — which is
fine for a single-user, local-first architecture.

## ⚠️ Critical: Tailwind CSS Is Pre-Compiled, Not Live

**This is the #1 thing to remember when building a new component.**

Flask's `--debug` dev server auto-reloads on file save — but only for
**Python and Jinja** files. It does **not** watch or rebuild CSS.

`app/static/css/output.css` is a **static, pre-generated file**. Tailwind
v4 scans your `.html`/`.html.jinja` files at *build time* and only
generates CSS for the exact utility classes it finds being used. If you
add a brand-new utility class to a template (e.g., `w-0.5`, `bg-border`,
`size-[15px]`) that has never appeared anywhere else in the project, **it
will not exist in `output.css` until you rebuild it** — and the browser
will silently render nothing for that class (no error, just invisible
styling). This is exactly what happened when building the Timeline
component: the dots and connector line didn't appear until the CSS was
rebuilt.

**Rule of thumb: any time you add or change a Tailwind utility class in a
template, rebuild the CSS before judging how the component looks.**

```bash
npm run build:css
```

Or, better, while actively iterating on component styling, run the watcher
in a separate terminal so every save rebuilds automatically:

```bash
npm run watch:css
```

Then just refresh the browser (no Flask restart needed — only the CSS file
needs regenerating).

### When you MUST rebuild CSS
- After adding a new component/block with **any** new Tailwind utility
  class not already used elsewhere in the project.
- After editing `app/static/css/input.css` (e.g. adding `@theme` tokens,
  custom layers, or overrides).
- After running `npm run sync:basecoat` (which replaces
  `app/static/css/basecoat-vega.css`, a dependency of `input.css`).
- After `npm install` or upgrading `basecoat-css` / `tailwindcss` versions
  (though `postinstall` handles this automatically).

### When you do NOT need to rebuild CSS
- Editing Jinja logic, Flask routes, or Python code — the dev server
  auto-reloads these.
- Reusing utility classes that are already used elsewhere in the project
  (they're already compiled into `output.css`).

### Known gotcha: semantic color utilities silently failing
If a component's color-based classes (`bg-primary`, `text-muted-foreground`,
`border-primary`, `bg-border`, `text-destructive`, etc.) render as invisible
or unstyled even after a rebuild, check that `app/static/css/input.css`
still has its `@theme inline { --color-x: var(--x); ... }` token remap
block. Basecoat ships its design tokens as plain CSS custom properties
(`:root { --primary: ...; }`), not as Tailwind's native `@theme` syntax —
without the `@theme inline` remap, Tailwind v4 doesn't know those tokens
are colors and won't generate utilities for them at all. This was fixed
once already (see `input.css`); if it's ever removed or `basecoat-vega.css`
is re-synced with a structurally different token format, this symptom will
reappear.

## 1. The "Extract and Publish" Pattern

Never build a new component in a vacuum inside the KD UI repository. Always
build it inside a living, breathing application (like `Liquidator`) first.
This ensures the component solves a real problem, fits naturally into a
Flask/Jinja architecture, and works correctly with live data before it gets
promoted to a reusable library.

## 2. Step-by-Step Workflow

### Phase 1 — Prototype Locally (in Liquidator)

1. **Identify the need.** You realize you need a new UI element (e.g., a
   `Timeline` or `DataCard`).
2. **Create a draft.** Add a new Jinja file in `app/templates/components/`
   (e.g., `timeline.html`).
3. **Use base primitives first.** Do not reinvent the wheel — import macros
   from `app/templates/basecoat/` if your component needs a dropdown,
   dialog, tabs, or button behavior.
4. **Style with Tailwind.** Use Tailwind utilities only for layout and
   app-specific adjustments, staying aligned with the existing
   shadcn/Basecoat visual language (spacing, radius, colors, typography).
5. **Rebuild the CSS.** Run `npm run build:css` (or keep `npm run
   watch:css` running in a terminal while you iterate). Any new Tailwind
   utility class you just used will not render until this runs — see the
   "Critical: Tailwind CSS Is Pre-Compiled" section above.
6. **Test in context.** Render the draft directly in a real route (e.g.,
   `dashboard.html`) with live Flask data, and take a real screenshot
   (Playwright) to confirm it renders as expected — don't just trust the
   markup. Confirm it behaves well with real content lengths, empty
   states, and edge cases.

### Phase 2 — Abstract and Generalize

Once the component works well in the app, prepare it for KD UI:

1. **Remove hardcoded data.** Replace Liquidator-specific text with Jinja
   variables (e.g., `{{ title }}`, `{{ items }}`).
2. **Add customization hooks.** Accept a `class` parameter (or `**kwargs`)
   so future consuming apps can pass Tailwind overrides without editing the
   macro itself.
3. **Re-verify.** Confirm the component still renders correctly in
   Liquidator after genericizing it.

### Phase 3 — Port to KD UI

Because integration is via plain file copy, porting is straightforward:

1. **Copy the file** from Liquidator's `app/templates/components/` (or
   `blocks/`) folder.
2. **Paste it** into the matching folder inside your standalone KD UI Git
   repository.
3. **Commit and push** to the KD UI GitHub repository so the component is
   saved permanently and versioned.

### Phase 4 — Sync and Document

1. **Add to the gallery.** In Liquidator, open
   `app/templates/gallery/components.html` (or `blocks.html` for page-level
   compositions) and add a live, interactive example of the new component.
   This is your living documentation and regression check.
2. **Rebuild CSS again.** The gallery entry may introduce its own new
   wrapper classes (e.g., a `max-w-md` on the demo container). Run `npm run
   build:css` once more and verify with a screenshot before considering the
   component done.
3. **Document the contract.** Note the file path, what it `{% from ... import %}`s,
   and the expected template variables — per the New Blocks rule in
   `AGENTS.md`.
4. **Reuse in future projects.** The next time you start a new Flask app,
   copy the KD UI `basecoat/` + `components/` folders into it, copy over
   `input.css`'s `@theme inline` token block too, then run `npm install`
   (triggers `postinstall` → `sync:basecoat` + `build:css`) — the new
   component comes along fully styled.

## 3. Quick Checklist

- [ ] Built and tested inside a real app first (not designed in isolation)
- [ ] Reuses Basecoat primitives instead of rebuilding them
- [ ] Uses Tailwind only for layout/app-specific tweaks
- [ ] Ran `npm run build:css` after adding/changing any Tailwind utility
      class (Flask's auto-reload does NOT rebuild CSS)
- [ ] Verified the rendered result with a real screenshot (Playwright),
      not just by reading the markup
- [ ] Hardcoded data replaced with Jinja variables
- [ ] Accepts a `class`/`kwargs` override hook
- [ ] Copied into the KD UI repo and committed
- [ ] Added to `/ui/components` or `/ui/blocks` gallery with documented
      file path, imports, and expected variables
- [ ] Rebuilt CSS again after adding the gallery demo entry

## 4. Future Improvement Idea

Once you have 10+ components and are copy-pasting between repos often,
consider a small `sync_kdui.ps1` script that copies known folders between
the two repo locations, to reduce manual drift. Not needed for v1 — only
worth building once the pattern gets repetitive.
