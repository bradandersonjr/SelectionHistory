# Changelog

All notable changes to Selection History will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- History is no longer discarded on every selection event. The active document
  was compared by object identity, but Fusion returns a new wrapper on each
  access, so every event looked like a document switch and wiped the stack —
  leaving it permanently one entry deep. Documents are now keyed on
  `Document.creationId`, which is stable for the life of a document.

- Restoring no longer selects the wrong geometry after a regeneration. A spline
  handle whose curve was rebuilt by a constraint is accepted by Fusion and
  silently resolved to the parent spline; each entity is now read back after
  being added and dropped if Fusion substituted something else.

- History no longer carries across documents. Selections remembered in one
  design could previously be restored into another, where the entity references
  are stale — the stack is now discarded whenever the active document changes.

## [1.0.0] - 2026-08-21

### Added

- Selection History command, registered in the Select panel of the Design
  workspace and in the Select panel shown while editing a sketch.
- Selection history recorded from the `activeSelectionChanged` event, bounded to
  `MAX_HISTORY` entries. Each entry is a whole finished selection: a set that
  grows or shrinks from the newest entry replaces it in place, so building a set
  by ctrl-clicking does not fill the stack with half-built stages.
- Repeated presses step one entry further back through history, resetting to the
  newest entry whenever the user makes a selection of their own.
- A live selection is treated as the point being stepped back from, and is
  skipped when it matches the newest entry so the first press always moves to a
  different selection.
- Entities deleted since they were recorded are skipped on restore, and an entry
  whose entities are all gone is dropped so it cannot block the stack.
- Two independent guards keep a restore from recording itself as user history,
  covering both synchronous and late delivery of the selection event.
