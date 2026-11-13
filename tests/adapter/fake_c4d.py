"""A minimal fake `c4d` module so the adapter can be imported and exercised
outside Cinema 4D, on Python 2.7 and 3.x.

It only checks our own logic (imports, string handling, dialog flow); real API
behaviour is verified inside Cinema 4D with tests/c4d_manual/.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import sys
import tempfile

import fake_scene
import types
import zlib

PY2 = sys.version_info[0] == 2


class FakeModule(types.ModuleType):
    """Unknown upper-case constants resolve to stable ints."""

    def __getattr__(self, name):
        if name.startswith("__") or not name.isupper() or name in self.__dict__.get("_hidden", ()):
            raise AttributeError(name)
        value = zlib.crc32(name.encode("ascii")) & 0x3FFFFFFF
        setattr(self, name, value)
        return value


def _require_native_str(value, what):
    """Mimic R17-R22: the API rejects unicode on Python 2."""
    if PY2 and not isinstance(value, str):  # pragma: no cover - Python 2 only
        raise TypeError("%s must be str, not %s" % (what, type(value).__name__))


class BaseContainer(object):
    def __init__(self):
        self._data = {}

    def SetString(self, key, value):
        _require_native_str(value, "BaseContainer.SetString value")
        self._data[key] = value

    def GetString(self, key):
        return self._data.get(key, str(""))

    def SetInt32(self, key, value):
        self._data[key] = int(value)

    def GetInt32(self, key):
        return self._data.get(key, 0)

    def SetFloat(self, key, value):
        self._data[key] = float(value)

    def GetFloat(self, key):
        return self._data.get(key, 0.0)

    def SetBool(self, key, value):
        self._data[key] = bool(value)

    def GetBool(self, key):
        return self._data.get(key, False)

    def SetContainer(self, key, container):
        self._data[key] = container

    def GetContainerInstance(self, key):
        value = self._data.get(key)
        return value if isinstance(value, BaseContainer) else None

    def __contains__(self, key):
        return key in self._data

    def __iter__(self):
        return iter(sorted(self._data.items()))


class ByteSeq(object):
    def __init__(self, ptr, size):
        self._data = bytearray(size)

    def __setitem__(self, index, value):
        self._data[index] = ord(value) if isinstance(value, (str, bytes)) else value

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


class FakeAtom(object):
    def __init__(self, byteseq_only=False):
        self.byteseq_only = byteseq_only
        self.ids = []

    def AddUniqueID(self, app_id, data):
        if self.byteseq_only and not isinstance(data, ByteSeq):
            raise TypeError("argument 2 must be c4d.storage.ByteSeq")
        self.ids.append((app_id, bytes(bytearray(data))))

    def FindUniqueID(self, app_id):
        for key, value in reversed(self.ids):
            if key == app_id:
                return memoryview(value)
        return None


class FakeDocument(object):
    def __init__(self):
        self.hdirty = {}

    def GetHDirty(self, flags):
        return self.hdirty.get(flags, 0)


class GeDialog(object):
    def __init__(self):
        self.widgets = {}
        self.values = {}
        self.enabled = {}
        self.title = None
        self.timer = None
        self.opened = False

    def _add(self, kind, widget, *args):
        if widget:
            self.widgets[widget] = kind

    def AddEditText(self, widget, flags, w=0, h=0, editflags=0):
        self._add("edit", widget)

    def AddEditNumberArrows(self, widget, flags, w=0, h=0):
        self._add("number", widget)

    def AddButton(self, widget, flags, w, h, name):
        _require_native_str(name, "button name")
        self._add("button", widget)

    def AddStaticText(self, widget, flags, w, h, name, border):
        _require_native_str(name, "static text")
        self._add("static", widget)

    def AddMultiLineEditText(self, widget, flags, w, h, style):
        self._add("multiline", widget)

    def GroupBegin(self, widget, flags, cols, rows, title, groupflags):
        _require_native_str(title, "group title")
        return True

    def GroupEnd(self):
        return True

    def GroupSpace(self, x, y):
        return True

    def GroupBorderSpace(self, *args):
        return True

    def SetTitle(self, title):
        _require_native_str(title, "title")
        self.title = title

    def SetString(self, widget, value):
        _require_native_str(value, "SetString")
        self.values[widget] = value

    def GetString(self, widget):
        return self.values.get(widget, str(""))

    def SetInt32(self, widget, value, mini=None, maxi=None):
        self.values[widget] = int(value)

    def GetInt32(self, widget):
        return self.values.get(widget, 0)

    def Enable(self, widget, state):
        self.enabled[widget] = bool(state)

    def SetTimer(self, ms):
        self.timer = ms

    def CoreMessage(self, id, msg):
        return True

    def Open(self, *args):
        self.opened = True
        return True

    def Restore(self, *args):
        return True


class MessageData(object):
    pass


class CommandData(object):
    pass


class ObjectData(object):
    pass


class PreferenceData(object):
    """Like the real one: InitPreferenceValue stores the default when nothing is saved yet."""

    def InitPreferenceValue(self, ident, default, description, desc_id, container):
        _require_native_str(default, "InitPreferenceValue default") if isinstance(default, str) else None
        if container is None or ident in container:
            return
        if isinstance(default, bool):
            container.SetBool(ident, default)
        elif isinstance(default, int):
            container.SetInt32(ident, default)
        elif isinstance(default, float):
            container.SetFloat(ident, default)
        else:
            container.SetString(ident, default)


class Description(object):
    """What GetDDescription receives."""

    def __init__(self):
        self.loaded = []

    def LoadDescription(self, name):
        _require_native_str(name, "LoadDescription name")
        self.loaded.append(name)
        return True


def install(profile=None):
    """Create and register the fake modules. Returns the fake c4d module.
    `profile`: a key of profiles.PROFILES to look like that Cinema 4D version (default: 2023, the full API)."""
    c4d = FakeModule(str("c4d"))
    plugins = FakeModule(str("c4d.plugins"))
    gui = FakeModule(str("c4d.gui"))
    storage = FakeModule(str("c4d.storage"))

    c4d.version = 2023103
    c4d.special_events = []
    c4d.event_adds = []
    c4d.registered = []
    c4d.message_dialogs = []
    c4d.world_data = {}
    c4d.preference_pages = []
    c4d.world_container = BaseContainer()
    c4d.GetWorldContainerInstance = lambda: c4d.world_container
    c4d.Description = Description

    c4d.GetC4DVersion = lambda: c4d.version
    c4d.SpecialEventAdd = lambda id, p1=0, p2=0: c4d.special_events.append(id)
    c4d.EventAdd = lambda flags=0: c4d.event_adds.append(flags)
    c4d.status_texts = []
    c4d.StatusSetText = lambda text: c4d.status_texts.append(text)
    c4d.BaseContainer = BaseContainer

    def register(kind):
        def _register(id, name, *rest):
            _require_native_str(name, "plugin name")
            c4d.registered.append((kind, id, rest[-1]))
            return True
        return _register

    plugins.MessageData = MessageData
    plugins.CommandData = CommandData
    plugins.ObjectData = ObjectData
    plugins.PreferenceData = PreferenceData
    plugins.RegisterMessagePlugin = register("message")
    plugins.RegisterCommandPlugin = register("command")

    def register_object(id, name, cls, description, info, icon, disklevel=0):
        _require_native_str(name, "plugin name")
        c4d.registered.append(("object", id, cls))
        return True
    plugins.RegisterObjectPlugin = register_object
    def register_preference(id, g, name, description, parentid, sortid):
        _require_native_str(name, "preference name")
        _require_native_str(description, "preference description")
        c4d.registered.append(("preference", id, g))
        c4d.preference_pages.append((name, description, parentid, sortid))
        return True
    plugins.RegisterPreferencePlugin = register_preference
    plugins.GetWorldPluginData = lambda id: c4d.world_data.get(id)
    plugins.SetWorldPluginData = lambda id, bc, add=True: c4d.world_data.__setitem__(id, bc)

    gui.GeDialog = GeDialog
    gui.MessageDialog = lambda text, type=0: c4d.message_dialogs.append(text)
    c4d.question_dialogs = []
    c4d.question_answers = []   # tests put the answers here; empty means "yes"

    def question_dialog(text):
        _require_native_str(text, "QuestionDialog text")
        c4d.question_dialogs.append(text)
        return c4d.question_answers.pop(0) if c4d.question_answers else True
    gui.QuestionDialog = question_dialog

    storage.ByteSeq = ByteSeq
    storage.GeGetC4DPath = lambda which: str(tempfile.gettempdir())

    c4d.plugins = plugins
    c4d.gui = gui
    c4d.storage = storage
    documents = FakeModule(str("c4d.documents"))
    c4d.scene_state = fake_scene.install_into(c4d, documents)
    for name, module in (("c4d", c4d), ("c4d.plugins", plugins), ("c4d.gui", gui),
                         ("c4d.storage", storage), ("c4d.documents", documents)):
        sys.modules[name] = module
    if profile is not None:
        import profiles
        profiles.apply(c4d, profile)
    return c4d


def uninstall():
    try:
        import profiles
        profiles.restore()
    except ImportError:
        pass
    for name in [n for n in sys.modules if n == "c4d" or n.startswith("c4d.") or n.startswith("c4d_collab_lib.c4d_adapter")]:
        del sys.modules[name]
