# Selection History

Restores a selection that Autodesk Fusion cleared, and steps further back
through selection history on each press.

Fusion drops the active selection constantly and gives you no way to get it
back. Applying a sketch constraint deselects everything the instant it is
applied, a stray click in empty space clears a set that took a dozen picks to
build, and several commands clear the selection when they close. **Undo does not
help** — the selection is not part of the undo stack, so `Ctrl+Z` rolls back the
modeling operation instead of restoring what was highlighted.

## Where the command appears

The add-in registers **Selection History** in the **Select** panel of the Design
workspace, and in the **Select** panel shown while editing a sketch.

The sketch one is the one that matters most — applying a constraint clears the
selection immediately — but a selection is just as easy to lose while solid
modeling, so one command and one shortcut cover both.

> [!NOTE]
> Pressing the command replaces whatever is currently selected. There is no
> confirmation, and the selection you were holding is not itself pushed onto the
> history stack unless you had already finished building it — a set still being
> assembled when you press is not recoverable.

## Keyboard shortcut

This add-in is close to useless on a button — the whole point is to recover a
selection without moving the mouse away from what you are doing. Open
**File → Preferences → Keyboard Shortcuts**, search for **Selection History**, and
assign a combination.

The binding is keyed to the command ID, not to the toolbar button, so it
survives stopping and restarting the add-in.

## Install

1. Download or clone this repository into a folder named `SelectionHistory`.
2. Confirm these files are directly inside that folder:

   ```
   SelectionHistory.py
   SelectionHistory.manifest
   README.md
   resources/          (16x16-normal.png, 32x32-normal.png, 64x64-normal.png)
   ```

3. In Fusion, open **Utilities → Add-Ins → Scripts and Add-Ins → Add-Ins**.
4. Click the green **+** and select the `SelectionHistory` folder.
5. Enable **Run on Startup** so the history is always being recorded.

The `resources/` folder must be present. Fusion draws the button from the
folder, not from a single file — without it the control still exists but
renders as an empty gap.

**Run on Startup matters more here than for most add-ins.** The add-in can only
restore selections it watched you make, so nothing is remembered from before it
started.

> [!IMPORTANT]
> Fusion only reports selection changes while its **Select** command is
> running — the default state when no other command is active. Anything picked
> while a command is open, **including sketch edit mode**, is invisible to that
> event. The add-in therefore also reads the selection whenever a command
> finishes, which is what captures picks made while sketching. A selection made
> and cleared entirely inside one command can still be missed.

## How it works

The add-in subscribes to Fusion's `activeSelectionChanged` event and keeps a
stack of recent non-empty selections:

- Every selection you make yourself is pushed onto the stack. Empty selections
  are never recorded, since an empty selection is the thing being recovered
  from.
- Consecutive identical selections are collapsed into one entry, so a single
  press never has to step through several copies of the same set.
- Pressing the command restores the newest entry. Pressing it again replaces it
  with the next entry back, and so on. Selecting anything yourself resets that
  walk, so the next press starts from the top again.
- Each entry is a whole finished selection. Fusion reports every individual pick
  separately, so ctrl-clicking three circles would otherwise land as three
  entries — instead, a set that grows or shrinks from the newest entry replaces
  it in place, and only a genuinely different selection starts a new one.
  Selecting three circles, then a face, then two edges gives you exactly three
  entries to step through.
- The oldest entry is a floor. Repeated presses park there rather than wrapping
  around to the newest.

Behavior notes:

- A live selection is the point you step back *from*. If it matches the newest
  remembered entry, that entry is skipped, so the first press always moves you
  somewhere new rather than restoring what is already on screen.
- Entities deleted since they were recorded are skipped, and the rest of the
  set is still restored.
- Some entities are not deleted but *regenerated* — applying a constraint to a
  spline handle rebuilds the curve, replacing its handles. Fusion accepts the
  old reference and silently resolves it to the surviving parent, which would
  select the spline instead of the handles you picked. Each restored entity is
  read back and dropped if Fusion substituted something else, so a stale entry
  falls through to an older one rather than selecting the wrong geometry.
- If every entity in an entry is gone, that entry is dropped and the next one
  back is tried, so a stale entry cannot block the stack permanently.
- Selections are captured two ways: from Fusion's selection-changed event while
  no command is running, and by reading the selection whenever a command
  finishes. The second path is what sees picks made in sketch edit mode, since
  sketching is itself a running command and suppresses the first.
- Each document keeps its own history. Switching documents parks the current
  one and swaps in whatever that document had, so going away and coming back
  finds the stack intact. A remembered entity means nothing outside the design
  that owns it, so histories are never shared between documents.
- Closing a document forgets its history, which is what keeps the add-in from
  holding a closed design's geometry in memory. At most `MAX_DOCUMENTS`
  inactive documents keep a parked history; beyond that the oldest is dropped.
- History lives in memory only. It is cleared when the add-in stops and is not
  saved between Fusion sessions.

## Tuning

At the top of `SelectionHistory.py`:

- `MAX_HISTORY = 20` — how many distinct selection sets to remember per
  document. Deep enough to survive a run of constraint operations, shallow
  enough that the held entity references cannot accumulate meaningfully.
- `MAX_DOCUMENTS = 10` — how many inactive documents keep a parked history.
  A backstop for a close the API does not report; the oldest is dropped first.

## Requirements

- Autodesk Fusion (Windows or macOS)
- No external Python packages — uses only Fusion's bundled interpreter and the
  standard library.

## License

[MIT](LICENSE) © brad anderson jr.
