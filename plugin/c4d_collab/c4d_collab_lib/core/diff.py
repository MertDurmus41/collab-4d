"""Small structural diff for JSON-like dicts.

Patch format (all keys optional, an empty patch is never produced):
    {"set": {key: new_value}, "del": [key, ...], "sub": {key: nested_patch}}

Nested dicts are diffed recursively; lists and scalars are replaced as a whole.
Serializers should therefore store large per-element data (keyframes, points)
in dicts keyed by index/id when only a few elements change at a time, or as
packed strings (see protocol.pack_floats) when the whole array is replaced.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import copy

from .compat import number_types

DEFAULT_EPSILON = 1e-7


def _equal(a, b, eps):
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, number_types) and isinstance(b, number_types):
        return abs(a - b) <= eps
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        for x, y in zip(a, b):
            if not _equal(x, y, eps):
                return False
        return True
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return False
        for key in a:
            if not _equal(a[key], b[key], eps):
                return False
        return True
    return a == b


def diff(old, new, eps=DEFAULT_EPSILON):
    """Return a patch turning `old` into `new`, or None when they are equal."""
    if old is None:
        old = {}
    patch_set = {}
    patch_sub = {}
    patch_del = [key for key in old if key not in new]
    for key, value in new.items():
        if key not in old:
            patch_set[key] = copy.deepcopy(value)
            continue
        previous = old[key]
        if isinstance(previous, dict) and isinstance(value, dict):
            nested = diff(previous, value, eps)
            if nested is not None:
                patch_sub[key] = nested
        elif not _equal(previous, value, eps):
            patch_set[key] = copy.deepcopy(value)
    patch = {}
    if patch_set:
        patch["set"] = patch_set
    if patch_del:
        patch["del"] = sorted(patch_del)
    if patch_sub:
        patch["sub"] = patch_sub
    return patch or None


def apply_patch(target, patch):
    """Apply `patch` to dict `target` in place and return it."""
    if not patch:
        return target
    for key in patch.get("del", ()):
        target.pop(key, None)
    for key, value in patch.get("set", {}).items():
        target[key] = copy.deepcopy(value)
    for key, nested in patch.get("sub", {}).items():
        child = target.get(key)
        if not isinstance(child, dict):
            child = {}
            target[key] = child
        apply_patch(child, nested)
    return target


def is_valid_patch(patch):
    """Structural check for patches received from the network."""
    if not isinstance(patch, dict):
        return False
    if set(patch) - {"set", "del", "sub"}:
        return False
    if "set" in patch and not isinstance(patch["set"], dict):
        return False
    if "del" in patch and not isinstance(patch["del"], list):
        return False
    if "sub" in patch:
        if not isinstance(patch["sub"], dict):
            return False
        for nested in patch["sub"].values():
            if not is_valid_patch(nested):
                return False
    return True
