# Changelog

All notable changes to Selection History will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
