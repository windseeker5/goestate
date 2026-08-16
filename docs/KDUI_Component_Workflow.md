# KD UI Component Development Workflow

Estate Copilot is a real application and prototype environment. KD UI is the
separate reusable component library and owns the developer gallery. Basecoat is
the third-party visual engine used by both repositories.

## Repository Roles

- Estate Copilot: this repository
- KD UI: the local `windseeker5/kdui` checkout (currently a sibling repository)
- KD UI Git remote: `https://github.com/windseeker5/kdui.git`

Estate Copilot intentionally has no `/ui` routes or component gallery. New UI
is proven in the real page that needs it, then generalized and promoted to KD
UI, where it receives a catalog example and regression verification.

## Basecoat Sync Is Separate

`scripts/sync_basecoat.mjs` copies the installed `basecoat-css` npm package's
Jinja macros, JavaScript runtime, and CSS into the repository that runs it. It
does not read from KD UI, publish components, or interact with GitHub.

## Component Lifecycle

1. Inspect Basecoat and existing KD UI components before creating anything.
2. Prototype the pattern in a real Estate Copilot page with real data.
3. Extract reusable markup into `app/templates/components/<name>.html`.
4. Document Usage and Params, remove estate-specific text, and expose
   `class_=""` for layout overrides.
5. Put reusable browser behavior in
   `app/static/js/components/<name>.js`, not app-specific `app.js`.
6. Rebuild Estate Copilot CSS and verify the real page on desktop and mobile.
7. Audit and promote the component from the KD UI repository.
8. Add a generic live example and usage snippet to KD UI's gallery.
9. Rebuild KD UI CSS and verify its gallery.
10. Review both Git diffs. Ask before committing or pushing KD UI.

## Promotion Commands

Run these from the KD UI repository:

```bash
python scripts/kdui_promote.py status --source ../../Liquidator --kind component
python scripts/kdui_promote.py component file_upload --source ../../Liquidator --dry-run
python scripts/kdui_promote.py component file_upload --source ../../Liquidator
```

Use `block` instead of `component` for a reusable block. Promotion copies only
the named template and an optional same-named JavaScript controller. It never
copies the whole application, edits the source, commits, or pushes.

## Tailwind Build Rule

Tailwind CSS is compiled, not live. After changing any utility class, run this
inside every affected repository before visual verification:

```bash
npm run build:css
```

## Completion Checklist

- [ ] Proven in a real application page
- [ ] Reuses Basecoat where an equivalent primitive exists
- [ ] Generic macro with documentation and `class_` override
- [ ] Reusable JavaScript stored as a companion controller
- [ ] Estate Copilot tested on desktop and mobile
- [ ] Promoted to the local KD UI repository
- [ ] Added to the KD UI gallery with a usage snippet
- [ ] KD UI CSS rebuilt and gallery visually verified
- [ ] Diffs reviewed in both repositories
- [ ] No KD UI commit or push without explicit approval
