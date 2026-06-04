# core/patches — the re-appliable-patch workflow

Our edits to a vendored submodule (`core/mpeg2fpga`, `core/MiSTer_MPEG2`, and any
build-box submodules added later) are **never committed into the submodule**. The
submodule gitlink stays frozen at its [pinned SHA](../pin-manifest.md); the **patch
file in this directory is the durable artifact** that carries our changes.

This keeps three things true at once:

1. **Reproducible inputs.** An ephemeral container re-clones each submodule at its
   exact pin every run — no drift, no surprise upstream commits.
2. **Upstream-offer-able diffs.** Each patch is a clean `git diff` against the pin, so
   it can be sent to `mrchrisster` / the `mpeg2fpga` maintainers as a PR with no
   un-tangling.
3. **No accidental pin bumps.** Because we never `git commit` inside the submodule, the
   parent repo's gitlink can't silently advance.

## Naming

```
core/patches/<submodule-name>-<topic>.patch
```

e.g. `MiSTer_MPEG2-sd-seam-network-ring.patch`,
`mpeg2fpga-verilator-lint-fixes.patch`. One topic per patch where practical so they
can be applied / dropped / offered upstream independently.

## Capture (after editing files inside a submodule)

Run from the repo root. The diff is taken **inside** the submodule so paths are
relative to the submodule root (which is what `git apply` inside the submodule wants):

```sh
git -C core/<name> diff > core/patches/<name>-<topic>.patch
```

Capture only specific files if a topic touches a subset:

```sh
git -C core/<name> diff -- rtl/mpeg2/syncgen.v > core/patches/<name>-<topic>.patch
```

If a topic adds **new** files to the submodule, stage them first so they show up in the
diff:

```sh
git -C core/<name> add <new-file>
git -C core/<name> diff --cached > core/patches/<name>-<topic>.patch
```

## Re-apply (fresh clone, or after `git submodule update --init`)

From the repo root, apply each patch **inside** its submodule. Note the `../patches/`
prefix — the path is relative to the submodule's working directory:

```sh
git -C core/<name> apply ../patches/<name>-<topic>.patch
```

Check before applying (no changes made) and verify cleanliness:

```sh
git -C core/<name> apply --check ../patches/<name>-<topic>.patch   # dry run
git -C core/<name> apply --3way  ../patches/<name>-<topic>.patch   # tolerant of context drift
```

Use `--3way` if a future pin bump moves context lines; it falls back to a 3-way merge
instead of a hard reject.

## Idempotent apply-all (planned)

A small `core/apply_patches.sh` should iterate the patches in a defined order and
`git -C core/<name> apply --check` first so re-running is a no-op when patches are
already applied. (Not yet authored — referenced by `core/README.md` and
`docs/dev-workflow.md`.)

## Rules

- **Never** `git commit` inside a submodule. If you did by accident, the parent repo
  will want to bump the gitlink — `git -C core/<name> checkout <pin>` to undo, re-stage
  the gitlink, and re-capture your work as a patch instead.
- The submodule SHA in the parent index must always equal the
  [pin manifest](../pin-manifest.md) value. `git ls-files -s core/<name>` shows the
  staged gitlink SHA.
- A patch that no longer applies cleanly after an intentional pin bump must be
  **regenerated** against the new pin and the manifest updated in the same change.
