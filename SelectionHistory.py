"""Selection History - restores a previous selection after Fusion clears it.

Fusion drops the active selection constantly and offers no way to get it back.
Applying a sketch constraint deselects everything the moment it is applied, a
stray click in empty space clears a set that took a dozen picks to build, and
several commands clear the selection on exit. Undo does not help: the selection
is not part of the undo stack, so Ctrl+Z rolls back the modeling operation
rather than restoring what was highlighted.

This add-in listens to the active selection changed event and keeps a bounded
stack of recent non-empty selections. Pressing the command restores the most
recent remembered set; pressing it again steps one entry further back, so a
selection cleared several operations ago is still reachable.

Fusion raises that event on every individual pick, so a set built up by
ctrl-clicking arrives as a run of growing selections rather than one. Each entry
in the stack is a whole finished selection, not a stage of one being assembled:
a set that grows or shrinks from the newest entry replaces it in place, and only
a genuinely different selection starts a new entry.

Each document keeps its own history. Entities only mean anything inside the
design that owns them, so a set remembered in one is never restored into
another; switching documents parks the current stack and swaps in whatever that
document had, and closing one forgets it so its geometry is not held in memory.

The command works whether or not something is currently selected. A live
selection is treated as the point being stepped back from: if it matches the
newest remembered entry, that entry is skipped so the first press moves
somewhere new instead of spending itself restoring what is already on screen.

The command is registered in the Select panel in both solid and sketch mode,
and can be given a keyboard shortcut from Fusion's keyboard shortcut
preferences.

Requires Autodesk Fusion (the adsk modules only exist inside Fusion's Python).
"""

__author__ = 'brad anderson jr.'
__copyright__ = 'Copyright (c) 2026 brad anderson jr.'
__license__ = 'MIT'
__version__ = '1.0.0'
__maintainer__ = 'brad anderson jr.'
__email__ = 'brad@bradandersonjr.com'
__status__ = 'Production'

import adsk.core
import adsk.fusion
import traceback
from pathlib import Path

_app = None
_ui = None
_handlers = []

# The selection history, oldest first, and how far back the last run walked.
# _history_cursor is None whenever the user has made a selection of their own
# since the last restore, which is what restarts stepping from the top.
_history = []
_history_cursor = None

# Entities are only meaningful inside the document that owns them, so a set
# remembered in one design must never be restored into another — the references
# are stale there and reselecting them fails or, worse, resolves to something
# unrelated. Each document therefore gets its own history, parked in _histories
# while another document is on screen and swapped back when it returns.
#
# The stores are dropped on documentClosing, because holding entity references
# from a closed document would keep the whole document alive in memory.
#
# Keyed on Document.creationId rather than on the Document object: Fusion
# returns a new Python wrapper on each access, so comparing objects reports a
# document switch on every single event. Names are no good either — two unsaved
# documents can share one — while the creation ID is constant for the life of a
# document and exists before it has ever been saved.
#
# _NO_DOCUMENT is a distinct sentinel rather than None so that "no document is
# open" never compares equal to the initial "nothing recorded yet" state.
_NO_DOCUMENT = object()
_history_document = _NO_DOCUMENT

# creationId -> (history, cursor) for every document except the active one,
# whose state lives in _history and _history_cursor above while it is on screen.
_histories = {}

# A restore edits the selection, which raises the very event the add-in listens
# to. Left unguarded it would record its own work as fresh history and stepping
# back would never move. The two guards below cover the two ways Fusion can
# deliver that event, and both are needed because which one applies is not
# something the API documents:
#
#   _restoring         — set across the rebuild, catching synchronous delivery.
#   _restored_entities — what the restore put on screen, catching delivery that
#                        lands after this function has already returned.
_restoring = False

# See _restoring above. This is the half that survives past the end of a
# restore, so late-delivered events are still recognized as the add-in's own
# work rather than treated as a user selection that resets the walk.
_restored_entities = []

CMD_ID = 'AIW_SelectionHistory'
CMD_NAME = 'Selection History'
CMD_TOOLTIP = (
    'Restore the previous selection after it was cleared. Press again to step '
    'further back through selection history.'
)

WORKSPACE_ID = 'FusionSolidEnvironment'

# Registered in both Select panels. The sketch one matters most — applying a
# constraint clears the selection immediately, which is the case this add-in
# exists for — but the selection is just as easy to lose while solid modeling.
# Fusion shows whichever panel belongs to the active mode, so placing it in
# both means one command and one shortcut cover both.
PANEL_IDS = ('SelectPanel', 'SketchSelectPanel')

# How many distinct selection sets to remember. Deep enough to survive a run of
# constraint operations, shallow enough that the held entity references cannot
# accumulate meaningfully.
MAX_HISTORY = 20

# How many inactive documents keep a parked history. documentClosing normally
# evicts a document's store, but that event can be missed — a crash, or a close
# the API does not report — so this is the backstop that keeps a long session
# from accumulating entity references indefinitely.
MAX_DOCUMENTS = 10

ADDIN_DIR = Path(__file__).resolve().parent
RESOURCES_DIR = ADDIN_DIR / 'resources'


def _log(message):
    try:
        if _app:
            _app.log(f'[SelectionHistory] {message}')
    except Exception:
        pass


class SelectionHistoryActiveSelectionHandler(
        adsk.core.ActiveSelectionEventHandler):
    def notify(self, args):
        try:
            record_selection()
        except Exception:
            _log(traceback.format_exc())


class SelectionHistoryDocumentActivatedHandler(
        adsk.core.DocumentEventHandler):
    def notify(self, args):
        try:
            # Clearing here rather than waiting for the next selection event
            # releases the old document's entity references promptly.
            _sync_history_document()
        except Exception:
            _log(traceback.format_exc())


class SelectionHistoryDocumentClosingHandler(adsk.core.DocumentEventHandler):
    def notify(self, args):
        try:
            forget_document(args)
        except Exception:
            _log(traceback.format_exc())


class SelectionHistoryCommandExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            restore_selection()
        except Exception:
            _log(traceback.format_exc())
            if _ui:
                _ui.messageBox(
                    f'{CMD_NAME} failed:\n\n{traceback.format_exc()}',
                    CMD_NAME
                )


class SelectionHistoryCommandCreatedEventHandler(
        adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            # With no command inputs, Fusion auto-executes the command, which
            # is what makes this usable as a bare keyboard shortcut.
            cmd = args.command
            on_execute = SelectionHistoryCommandExecuteHandler()
            cmd.execute.add(on_execute)

            # Fusion holds only a weak reference to handlers. Without this the
            # handler is garbage collected and the command stops responding.
            _handlers.append(on_execute)
        except Exception:
            _log(traceback.format_exc())


def _control_collections():
    """Returns the toolbar collections the command is placed in and removed from."""
    collections = []

    workspace = _ui.workspaces.itemById(WORKSPACE_ID)
    if workspace:
        for panel_id in PANEL_IDS:
            panel = workspace.toolbarPanels.itemById(panel_id)
            if panel:
                collections.append(panel.controls)

    return collections


def _remove_command():
    """Removes the command controls from the toolbars and deletes the definition."""
    try:
        if not _ui:
            return

        for collection in _control_collections():
            ctrl = collection.itemById(CMD_ID)
            if ctrl:
                ctrl.deleteMe()

        cmd_def = _ui.commandDefinitions.itemById(CMD_ID)
        if cmd_def:
            cmd_def.deleteMe()
    except Exception:
        _log(traceback.format_exc())


def _add_command():
    """Registers the command and places it in its panel."""
    try:
        if not _ui:
            return

        # Registration must be idempotent. Without this, reloading the add-in
        # during development stacks up duplicate buttons.
        _remove_command()

        # Fusion draws the button from the resource folder, not a single file.
        # A missing folder leaves the control present but rendered as a gap.
        resource_path = str(RESOURCES_DIR) if RESOURCES_DIR.exists() else ''
        cmd_def = _ui.commandDefinitions.addButtonDefinition(
            CMD_ID,
            CMD_NAME,
            CMD_TOOLTIP,
            resource_path
        )

        cmd_created_handler = SelectionHistoryCommandCreatedEventHandler()
        cmd_def.commandCreated.add(cmd_created_handler)
        _handlers.append(cmd_created_handler)

        for collection in _control_collections():
            ctrl = collection.addCommand(cmd_def)
            if not ctrl:
                continue

            ctrl.isVisible = True

            # Leave the command unpinned so the user's own pin choice sticks
            # instead of being reset on every load.
            ctrl.isPromotedByDefault = False
            ctrl.isPromoted = False

        _log('Registered the command.')
    except Exception:
        _log(f'Failed to add the command: {traceback.format_exc()}')


def _add_selection_watcher():
    """Subscribes to the selection and document events the history depends on."""
    # Each subscription stands alone. Sharing one try block means a failure in
    # any of them skips the rest, which would leave the add-in half-wired —
    # silently recording nothing, or recording without ever scoping to a
    # document — with only a log line to say so.
    for event, handler, description in (
        (_ui.activeSelectionChanged,
         SelectionHistoryActiveSelectionHandler(), 'the active selection'),
        (_app.documentActivated,
         SelectionHistoryDocumentActivatedHandler(), 'document switches'),
        (_app.documentClosing,
         SelectionHistoryDocumentClosingHandler(), 'document closes'),
    ):
        try:
            event.add(handler)
            _handlers.append(handler)
        except Exception:
            _log(f'Failed to watch {description}: {traceback.format_exc()}')

    _log(f'Watching the active selection ({len(_handlers)} handlers).')


def _remove_selection_watcher():
    """Unsubscribes the selection and document handlers this add-in registered."""
    try:
        if not _ui:
            return

        for handler in _handlers:
            # Best effort only: if Fusion is already tearing down the event,
            # the handler dies with it and nothing is leaked.
            if isinstance(handler, SelectionHistoryActiveSelectionHandler):
                try:
                    _ui.activeSelectionChanged.remove(handler)
                except Exception:
                    pass
            elif isinstance(handler, SelectionHistoryDocumentActivatedHandler):
                try:
                    _app.documentActivated.remove(handler)
                except Exception:
                    pass
            elif isinstance(handler, SelectionHistoryDocumentClosingHandler):
                try:
                    _app.documentClosing.remove(handler)
                except Exception:
                    pass
    except Exception:
        _log(traceback.format_exc())


def run(context):
    global _app, _ui

    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface

        # Logged before any work, so an empty Text Commands panel means the
        # add-in never ran at all rather than that it ran and stayed quiet.
        _log(f'Starting version {__version__}.')

        _add_command()
        _add_selection_watcher()

        _log('Add-in started successfully.')

    except Exception:
        if _ui:
            _ui.messageBox(
                f'{CMD_NAME} failed to start:\n\n' + traceback.format_exc()
            )


def stop(context):
    global _history_document

    try:
        _remove_selection_watcher()
        _remove_command()
        _handlers.clear()
        _history.clear()
        _restored_entities.clear()
        _histories.clear()
        _history_document = _NO_DOCUMENT
        _log('Add-in stopped.')
    except Exception:
        pass


def _current_entities():
    """Returns the entities in the active selection, skipping any that are stale."""
    entities = []

    selections = _ui.activeSelections
    for index in range(selections.count):
        # A selection item whose entity has been deleted raises on access
        # rather than returning None, so each read is guarded individually.
        try:
            entity = selections.item(index).entity
        except Exception:
            continue

        if entity:
            entities.append(entity)

    return entities


def _same_entities(left, right):
    """Returns True when two entity lists refer to the same objects in any order."""
    if len(left) != len(right):
        return False

    remaining = list(right)
    for entity in left:
        for index, candidate in enumerate(remaining):
            if _is_same_entity(entity, candidate):
                del remaining[index]
                break
        else:
            return False

    return True


def _is_same_entity(left, right):
    """Returns True when two entity references point at the same object."""
    try:
        if left == right:
            return True
    except Exception:
        # Comparison itself can raise once one side has been invalidated.
        return False

    # Two references to one entity are not always the same Python object, so
    # fall back to the token Fusion uses to identify geometry across rebuilds.
    # Not every entity exposes one, and reading it from a dead reference can
    # raise, so a failure here just means "cannot prove they match".
    try:
        left_token = left.entityToken
        right_token = right.entityToken
    except Exception:
        return False

    return bool(left_token) and left_token == right_token


def _is_within(candidate, existing):
    """Returns True when every entity in candidate also appears in existing."""
    remaining = list(existing)
    for entity in candidate:
        for index, other in enumerate(remaining):
            if _is_same_entity(entity, other):
                del remaining[index]
                break
        else:
            return False

    return True


def _is_superset(candidate, existing):
    """Returns True when candidate contains every entity in existing, and more."""
    return len(candidate) > len(existing) and _is_within(existing, candidate)


def _sync_history_document():
    """Discards the history when the active document is not the one it came from."""
    global _history_document, _history_cursor, _restored_entities

    try:
        # creationId, not the Document object: Fusion hands back a new Python
        # wrapper on every access, so comparing objects reports a switch on
        # every event and wipes the history continuously. The creation ID is
        # constant for the life of a document and exists before it is saved.
        document = _app.activeDocument.creationId
    except Exception:
        # Fusion raises rather than returning None when no document is open,
        # such as during startup or after the last tab is closed.
        document = _NO_DOCUMENT

    if document == _history_document:
        return

    # Park the outgoing document's history so returning to it finds the stack
    # intact. _NO_DOCUMENT is not a real document and never gets a store.
    if _history_document is not _NO_DOCUMENT:
        if _history:
            _histories[_history_document] = (list(_history), _history_cursor)
        else:
            _histories.pop(_history_document, None)

    parked, cursor = _histories.pop(document, ([], None)) \
        if document is not _NO_DOCUMENT else ([], None)

    # Rebind in place: the walk logic reads the module-level list directly.
    _history[:] = parked
    _history_cursor = cursor
    _restored_entities = []
    _history_document = document

    if _history:
        _log(f'Switched document; restored a history {len(_history)} deep.')


def forget_document(args):
    """Drops the stored history for a document that is closing."""
    global _history_document, _history_cursor, _restored_entities

    try:
        # documentClosing still exposes the Document; documentClosed does not,
        # which is why the eviction happens on the earlier event.
        document = adsk.core.DocumentEventArgs.cast(args).document.creationId
    except Exception:
        _log('Could not identify the closing document; its history is kept '
             'until the next document switch.')
        return

    _histories.pop(document, None)

    if document == _history_document:
        # The document being closed is the one on screen, so the working state
        # refers to entities that are about to stop existing.
        _history.clear()
        _history_cursor = None
        _restored_entities = []
        _history_document = _NO_DOCUMENT

    _log('Document closed; forgot its selection history.')


def record_selection():
    """Pushes the current selection onto the history stack when the user changes it."""
    global _history_cursor, _restored_entities

    if not _ui:
        return

    # A restore drives the selection itself and must not be recorded, or the
    # entry just replayed would be pushed back on top and stepping would stall.
    if _restoring:
        return

    # Before anything reads or writes the stack, make sure it belongs to the
    # document currently on screen.
    _sync_history_document()

    entities = _current_entities()

    if not entities:
        # An empty selection is the thing being recovered from, not a state
        # worth remembering. Leave the stack untouched so the hotkey still has
        # something to restore.
        return

    _log(f'Selection event: {len(entities)} entities.')

    # Fusion delivers this event asynchronously, so a restore's own events land
    # after restore_selection() has returned and lowered the _restoring flag —
    # that flag alone cannot suppress them. Rebuilding a set also emits one
    # event per entity added, so the partly-filled stages arrive too. Both are
    # subsets of what was restored, and both must leave the cursor alone:
    # treating them as user selections is what would reset the walk on every
    # press and make the command look like it only ever restores the newest
    # entry.
    if _restored_entities and _is_within(entities, _restored_entities):
        return

    # A selection the user made themselves ends any walk back through history,
    # so the next press starts again from the newest entry.
    _history_cursor = None
    _restored_entities = []

    if _history and _same_entities(_history[-1], entities):
        # Fusion raises the event for changes that leave the set identical,
        # such as a reselect of the same entity. Collapsing them keeps one
        # press from stepping through several copies of the same selection.
        return

    # Fusion raises the event on every individual pick, so building a set of
    # three circles arrives as [1], [1,2], [1,2,3] — three events describing one
    # selection the user is still assembling. Recording each stage would make a
    # single ctrl-click walk back one pick at a time instead of moving between
    # the distinct selections the user actually made.
    #
    # A superset of the newest entry is that same set still being built, so it
    # replaces the entry in place rather than stacking on top of it. A subset is
    # the same edit in reverse — ctrl-clicking one item back off — and collapses
    # the same way. Anything else is a genuinely new selection and starts its
    # own entry.
    #
    # Collapsing a subset does mean a set the user deliberately narrowed is
    # remembered only at its narrowest. That is the right trade: the wider set
    # is one ctrl-click from being rebuilt, whereas a stack full of half-built
    # intermediate states makes stepping back useless.
    if _history and (_is_superset(entities, _history[-1])
                     or _is_superset(_history[-1], entities)):
        _history[-1] = entities
        return

    _history.append(entities)

    while len(_history) > MAX_HISTORY:
        _history.pop(0)

    _log(f'Recorded a selection of {len(entities)} entities; '
         f'history is {len(_history)} deep.')


def restore_selection():
    """Reselects the next entry back in the selection history."""
    global _history_cursor, _restoring, _restored_entities

    if not _ui:
        return

    _sync_history_document()

    if not _history:
        # Silent no-op is better for a keyboard shortcut.
        _log('No selection history to restore.')
        return

    if _history_cursor is not None:
        # Already walking, so keep going back.
        cursor = _history_cursor - 1
    else:
        # A fresh walk. Whatever is selected now is the point being stepped back
        # from, so start below it rather than at the newest entry — restoring an
        # entry identical to the live selection would spend a press changing
        # nothing. With an empty selection there is nothing to skip and the walk
        # starts at the top.
        cursor = len(_history) - 1

        current = _current_entities()
        if current and _is_within(current, _history[cursor]):
            cursor -= 1

    if cursor < 0:
        _log('Reached the oldest remembered selection.')
        return

    entities = _history[cursor]

    # Claim the target before touching the selection, not after. Fusion may
    # deliver a selection event either during the rebuild below or after this
    # function has returned, and in both cases the guard in record_selection()
    # has to already know what is being restored, or the add-in records its own
    # work as a user selection and resets the walk.
    _restored_entities = list(entities)

    _restoring = True
    try:
        selections = _ui.activeSelections
        selections.clear()

        restored = []
        for entity in entities:
            # A deleted entity usually raises on reselect, but not always: an
            # entity whose owner regenerated — a spline handle after a
            # constraint reshapes the curve — can be accepted and silently
            # resolved to the surviving parent instead. Adding one at a time
            # and reading back what actually landed is the only way to tell,
            # since sketch entities expose no validity flag to test up front.
            try:
                before = selections.count
                selections.add(entity)
            except Exception:
                continue

            if selections.count == before:
                # Fusion rejected it without raising.
                continue

            try:
                added = selections.item(selections.count - 1).entity
            except Exception:
                added = None

            if added is None or not _is_same_entity(added, entity):
                # Fusion substituted something else, so this is not the entity
                # that was remembered. Drop it rather than selecting geometry
                # the user never picked.
                continue

            restored.append(entity)
    finally:
        # Cleared unconditionally: leaving this True would silently stop all
        # further history recording for the rest of the session. Late-arriving
        # events are covered by _restored_entities, which outlives this block.
        _restoring = False

    if not restored:
        # Every entity in this entry is gone, so the entry is dead. Drop it and
        # try the next one back, which keeps a stale entry from blocking the
        # stack permanently.
        _log('Remembered selection no longer exists; dropping it.')
        del _history[cursor]
        _history_cursor = cursor
        _restored_entities = []
        restore_selection()
        return

    _history_cursor = cursor

    # Narrow the claim to what actually made it on screen, so a set with a
    # deleted entity in it is still recognized as this add-in's own work.
    _restored_entities = restored

    _log(f'Restored {len(restored)} entities from history entry {cursor} '
         f'of {len(_history)}.')
