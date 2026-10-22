"""
<summary>
The full screen Manage slots window (SLOTS-WINDOW, 22/10/2026): every slot as
a row, sortable by next up, name, time of day or type, with buttons to add a
slot and to back up or restore.
</summary>
<remarks>
The window is resources/skins/Default/720p/EngageSlots.xml. Editing, adding
and the backup menu are the existing dialog flows in slot_manager, opened from
here; the list is rebuilt from the store each time one returns, so it always
shows what was saved. The sort chosen is kept in the hidden slots_sort setting.
slot_manager is imported inside the functions that use it, because it imports
this module for manage_slots.
</remarks>
"""

from datetime import datetime

import xbmc
import xbmcaddon
import xbmcgui

from resources.lib import functional_lists
from resources.lib import slot_rules
from resources.lib import slots as store

ADDON_ID = 'service.engage'
WINDOW_XML = 'EngageSlots.xml'
SORT_SETTING = 'slots_sort'

# The sort orders, in the order the Sort menu offers them: (id, menu words).
SORTS = [
    ('next', 'Next up'),
    ('az', 'A to Z'),
    ('time', 'Time of day'),
    ('type', 'Type'),
]
DEFAULT_SORT = 'next'
KIND_ORDER = {'favourite': 0, 'sequence': 1, 'list': 2}

LIST_ID = 500
SORT_BUTTON = 9001
ADD_BUTTON = 9002
BACKUP_BUTTON = 9003
BUTTON_ROW = 9000

ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92


def _log(msg, level=xbmc.LOGINFO):
    """
    <summary>Write one line to kodi.log under the window's prefix.</summary>
    <param name="msg">The text.</param>
    <param name="level">An xbmc log level.</param>
    """
    xbmc.log('Engage slots window: {}'.format(msg), level)


def sort_label(sort_id):
    """
    <summary>
    The words for a sort order, as the Sort button shows them.
    </summary>
    <param name="sort_id">One of the ids in SORTS.</param>
    <returns>The words; those of DEFAULT_SORT for an unknown id.</returns>
    """
    words = dict(SORTS)
    return words.get(sort_id, words[DEFAULT_SORT])


def kind_text(slot):
    """
    <summary>
    The second line of a row: what the slot plays.
    </summary>
    <param name="slot">A stored slot dict.</param>
    <returns>'Favourite: Voyager', 'Film sequence, 3 films', 'Binge order, 12
    episodes' or 'List: Films, random, unwatched'.</returns>
    """
    kind = slot.get('kind') or 'favourite'
    if kind == 'list':
        return 'List: {}, {}'.format(slot.get('list_name') or '(no list)',
                                     functional_lists.mode_text(slot))
    if kind == 'sequence':
        count = len(store.items_of(slot))
        if slot.get('seq_type') == 'binge':
            return 'Binge order, {} episode{}'.format(count, '' if count == 1 else 's')
        return 'Film sequence, {} film{}'.format(count, '' if count == 1 else 's')
    return 'Favourite: {}'.format(slot.get('favourite') or '(none)')


def sort_slots(slots, sort_id, now):
    """
    <summary>
    The slots in the chosen order, each with when it is next due.
    </summary>
    <param name="slots">Stored slot dicts, in the store's order.</param>
    <param name="sort_id">One of the ids in SORTS; anything else sorts as
    DEFAULT_SORT.</param>
    <param name="now">The current time.</param>
    <returns>A list of (slot, due) pairs, due being slot_rules.next_due's datetime
    or None.</returns>
    <remarks>
    Every order breaks ties by name, then by the store's order, so the list never
    shuffles between visits. Next up puts the soonest first, then slots with no
    occurrence coming, then the disabled ones, which never come up; the other
    orders leave disabled slots where they fall and only grey them. Names
    compare without case.
    </remarks>
    """
    rows = []
    for position, slot in enumerate(slots):
        try:
            due = slot_rules.next_due(slot, now)
        except Exception as e:  # a malformed slot must not empty the list
            _log('next due failed for slot {}: {}'.format(slot.get('id'), e), xbmc.LOGWARNING)
            due = None
        rows.append((slot, due, position))

    def name(row):
        """<summary>The tie breaker: name without case, then store order.</summary>"""
        return (slot_rules.title_of(row[0]).casefold(), row[2])

    if sort_id == 'az':
        key = name
    elif sort_id == 'time':
        def key(row):
            """<summary>Hour and minute, then name.</summary>"""
            return (int(row[0].get('hour') or 0), int(row[0].get('minute') or 0)) + name(row)
    elif sort_id == 'type':
        def key(row):
            """<summary>Favourites, then sequences, then lists, then name.</summary>"""
            return (KIND_ORDER.get(row[0].get('kind') or 'favourite', 3),) + name(row)
    else:
        def key(row):
            """<summary>Enabled first, then soonest, then nothing coming, then name.</summary>"""
            slot, due = row[0], row[1]
            return (not slot.get('enabled'), due is None, due or datetime.max) + name(row)
    return [(slot, due) for slot, due, _ in sorted(rows, key=key)]


def _saved_sort():
    """
    <summary>The sort kept from last time, DEFAULT_SORT when none or unknown.</summary>
    <returns>One of the ids in SORTS.</returns>
    """
    try:
        value = xbmcaddon.Addon(ADDON_ID).getSetting(SORT_SETTING)
    except Exception:
        value = ''
    return value if value in dict(SORTS) else DEFAULT_SORT


def _save_sort(sort_id):
    """
    <summary>Keep the chosen sort for next time; a failure is only logged.</summary>
    <param name="sort_id">One of the ids in SORTS.</param>
    """
    try:
        xbmcaddon.Addon(ADDON_ID).setSetting(SORT_SETTING, sort_id)
    except Exception as e:
        _log('could not keep the sort: {}'.format(e), xbmc.LOGWARNING)


class SlotsWindow(xbmcgui.WindowXMLDialog):
    """
    <summary>
    The Manage slots window. Shows the slots, and hands each button and row to the
    existing dialog flows in slot_manager.
    </summary>
    """

    def __init__(self, *args, **kwargs):
        """
        <summary>Set up the window's state; Kodi builds the controls later.</summary>
        <param name="args">Passed to WindowXMLDialog: the XML, the add-on path, the
        skin and the resolution.</param>
        <param name="kwargs">Passed on as well.</param>
        """
        super().__init__(*args, **kwargs)
        self.sort_id = _saved_sort()
        self.slot_ids = []

    def onInit(self):
        """<summary>Fill the list when the window opens.</summary>"""
        from resources.lib import slot_manager
        self.setProperty('heading', ' '.join(p for p in ('ENGAGE', slot_manager._version()) if p))
        self.refresh()
        if self.slot_ids:
            # Filling the list after Kodi applied defaultcontrol leaves nothing
            # focused, so put the focus on it by hand.
            self.setFocusId(LIST_ID)

    def refresh(self, keep_id=None):
        """
        <summary>
        Rebuild the list from the store in the current order.
        </summary>
        <param name="keep_id">The id of a slot to leave the focus on, such as the one
        just edited; the first row when None or gone.</param>
        <remarks>
        Focus goes to the Add slot button when there are no slots, so OK always does
        something useful.
        </remarks>
        """
        now = datetime.now()
        rows = sort_slots(store.load_slots(), self.sort_id, now)
        items = []
        for slot, due in rows:
            item = xbmcgui.ListItem(slot_rules.title_of(slot))
            item.setLabel2(slot_rules.next_text(due, now)[len('Next: '):] if due else 'Not due')
            item.setProperty('kind', kind_text(slot))
            item.setProperty('schedule', slot_rules.summary_of(slot))
            item.setProperty('enabled', 'true' if slot.get('enabled') else 'false')
            items.append(item)
        self.slot_ids = [slot.get('id') for slot, _ in rows]
        self.setProperty('sortlabel', sort_label(self.sort_id))
        self.setProperty('count', str(len(rows)))
        listing = self.getControl(LIST_ID)
        listing.reset()
        listing.addItems(items)
        if not items:
            self.setFocusId(ADD_BUTTON)
            return
        position = self.slot_ids.index(keep_id) if keep_id in self.slot_ids else 0
        listing.selectItem(position)

    def onClick(self, controlId):
        """
        <summary>Open what a row or a button stands for, then rebuild the list.</summary>
        <param name="controlId">The control chosen.</param>
        """
        from resources.lib import slot_manager
        if controlId == LIST_ID:
            position = self.getControl(LIST_ID).getSelectedPosition()
            if 0 <= position < len(self.slot_ids):
                slot_id = self.slot_ids[position]
                slot_manager._edit_slot(slot_id)
                self.refresh(keep_id=slot_id)
        elif controlId == SORT_BUTTON:
            ids = [sort_id for sort_id, _ in SORTS]
            choice = xbmcgui.Dialog().select('Sort slots by', [words for _, words in SORTS],
                                             preselect=ids.index(self.sort_id) if self.sort_id in ids else 0)
            if choice >= 0 and ids[choice] != self.sort_id:
                self.sort_id = ids[choice]
                _save_sort(self.sort_id)
                self.refresh()
        elif controlId == ADD_BUTTON:
            before = {slot.get('id') for slot in store.load_slots()}
            slot_manager.add_slot_menu()
            added = [slot.get('id') for slot in store.load_slots() if slot.get('id') not in before]
            self.refresh(keep_id=added[0] if added else None)
            if added:
                self.setFocusId(LIST_ID)
        elif controlId == BACKUP_BUTTON:
            slot_manager._backup_restore_menu()
            self.refresh()

    def onAction(self, action):
        """
        <summary>Close on Back; everything else is Kodi's own navigation.</summary>
        <param name="action">The Kodi action.</param>
        """
        if action.getId() in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            self.close()


def open_slots_window():
    """
    <summary>
    Show the Manage slots window until the user backs out of it.
    </summary>
    <returns>True when the window was shown; False when it could not be, so the
    caller can fall back to the plain list.</returns>
    """
    try:
        path = xbmcaddon.Addon(ADDON_ID).getAddonInfo('path')
        window = SlotsWindow(WINDOW_XML, path, 'Default', '720p')
    except Exception as e:
        _log('could not open the window, using the plain list: {}'.format(e), xbmc.LOGERROR)
        return False
    try:
        window.doModal()
    finally:
        del window
    return True
