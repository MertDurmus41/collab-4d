# -*- coding: utf-8 -*-
"""Cinema 4D version profiles for the fake c4d module.

A profile makes the fake look like one Cinema 4D version: the version number, methods that did not exist yet
(they raise AttributeError, like the real thing), constants that were named differently or not there, and
the preference-page API (R19+). The facts come from Maxon's Python API docs ("New in version ..." notes) and
from what was measured on real R19 and 2023; every entry names its source in tests/compat/api_matrix.py,
which is the single list the plugin's fallbacks are checked against.

Not covered (needs the real application): behaviour such as which dirty counters move for which edit.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import collections

import fake_scene as fs


class _Missing(object):
    """A class attribute that is not there in this version: any access raises AttributeError."""

    def __init__(self, name):
        self.name = name

    def __get__(self, obj, owner=None):
        raise AttributeError(self.name)


_UNDO = []   # [(class, name, original or None)] restored by restore()


def _hide_method(cls, name):
    _UNDO.append((cls, name, cls.__dict__.get(name)))
    setattr(cls, name, _Missing(name))


DETAILED_HDIRTY = ("HDIRTYFLAGS_OBJECT", "HDIRTYFLAGS_OBJECT_MATRIX", "HDIRTYFLAGS_OBJECT_HIERARCHY",
                   "HDIRTYFLAGS_ANIMATION", "HDIRTYFLAGS_TAG", "HDIRTYFLAGS_MATERIAL", "HDIRTYFLAGS_SHADER")
NEW_FLAG_NAMES = ("DESCFLAGS_GET_NONE", "DESCFLAGS_SET_NONE", "DESCFLAGS_DESC_NONE", "GETACTIVEOBJECTFLAGS_NONE")
OLD_FLAG_NAMES = ("DESCFLAGS_GET_0", "DESCFLAGS_SET_0", "DESCFLAGS_DESC_0", "GETACTIVEOBJECTFLAGS_0")

# name: (version number, what is missing) - "missing" lists API that is NOT there yet in that version
PROFILES = collections.OrderedDict([
    ("R17", dict(
        version=17053, python="2.7",
        no_get_hdirty=True, no_get_range=True, no_hud_text=True, no_weight_map=True, no_preference_page=True,
        no_detailed_hdirty=True, old_flag_names=True)),
    ("R18", dict(
        version=18057, python="2.7",
        no_get_hdirty=True, no_get_range=True, no_weight_map=True, no_preference_page=True,
        no_detailed_hdirty=True, old_flag_names=True)),
    ("R19", dict(
        version=19024, python="2.7",
        no_weight_map=True, no_detailed_hdirty=True, old_flag_names=True)),
    ("R20", dict(
        version=20057, python="2.7",
        no_weight_map=True)),
    ("R21", dict(
        version=21026, python="2.7")),
    ("R23", dict(
        version=23008, python="3.7")),
])


def apply(c4d, name):
    """Make the freshly installed fake `c4d` look like Cinema 4D `name` (a key of PROFILES)."""
    profile = PROFILES[name]
    c4d.version = profile["version"]
    c4d.profile_name = name
    hidden = set()
    if profile.get("no_get_hdirty"):
        _hide_method(fs.BaseDocument, "GetHDirty")
    if profile.get("no_get_range"):
        _hide_method(fs.BaseSelect, "GetRange")
    if profile.get("no_hud_text"):
        _hide_method(fs.FakeBaseDraw, "DrawHUDText")
    if profile.get("no_weight_map"):
        _hide_method(fs.WeightTag, "SetWeightMap")
        _hide_method(fs.WeightTag, "GetWeightMap")
    if profile.get("no_preference_page"):
        for attr in ("PreferenceData", "RegisterPreferencePlugin"):
            if hasattr(c4d.plugins, attr):
                delattr(c4d.plugins, attr)
    if profile.get("no_detailed_hdirty"):
        hidden.update(DETAILED_HDIRTY)
    if profile.get("old_flag_names"):
        hidden.update(NEW_FLAG_NAMES)
        for flag in NEW_FLAG_NAMES:
            if flag in c4d.__dict__:
                delattr(c4d, flag)
        for flag in OLD_FLAG_NAMES:
            setattr(c4d, flag, 0)
    c4d._hidden = hidden
    for flag in hidden:
        if flag in c4d.__dict__:
            delattr(c4d, flag)


def restore():
    """Undo every hidden method (the fake classes are shared by all tests of the process)."""
    while _UNDO:
        cls, name, original = _UNDO.pop()
        if original is None:
            if name in cls.__dict__:
                delattr(cls, name)
        else:
            setattr(cls, name, original)
