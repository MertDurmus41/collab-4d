"""Texture files that travel with the session.

A material on the host points at `C:/project/tex/wood.png`. Another computer does not have that
file, so the shader would show nothing (found in the first live run: R19 showed the textures, the
guest did not). Every texture file a material or shader refers to is therefore sent once as an
"asset" node and stored in a cache folder on the receiving side, and the shader there is pointed
at the cached copy when its own path does not exist.

- Assets are content-addressed: the node uuid is the SHA-1 of the file, so the same file is never
  sent twice and a received file can be verified.
- Node data: {"z": base64(zlib(file bytes))}; the node name is the file name.
- Material/shader node data gets a section "f": {parameter key: {"h": sha1, "n": file name}}.

SECURITY (see ARCHITECTURE.md 6b): only image file types are accepted; the stored name is reduced
to a safe file name inside our own cache folder; size is limited before and after decompression;
the content must hash to the uuid it came with.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import base64
import hashlib
import os
import re
import tempfile
import zlib

import c4d

from ...core.compat import is_fatal, string_types
from ...core.log import get_logger
from .. import compat
from . import params

LOG = get_logger("assets")

MAX_ASSET_BYTES = 25 * 1024 * 1024          # one file
MAX_SESSION_BYTES = 300 * 1024 * 1024       # all files the tracker sends in one session
IMAGE_EXTENSIONS = frozenset([
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".bmp", ".hdr", ".exr", ".psd", ".gif", ".dds",
    ".webp", ".pic", ".iff", ".jp2", ".sgi", ".pcx", ".rla", ".pict", ".pct", ".b3d", ".dpx",
])
SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")
HEX40 = re.compile(r"^[0-9a-f]{40}$")

_digests = {}   # path -> (size, mtime, sha1)


def is_asset_uuid(value):
    return isinstance(value, string_types) and bool(HEX40.match(value))


def safe_name(name):
    """A file name that cannot leave the cache folder or hide as something else."""
    base = os.path.basename((name or "").replace("\\", "/"))
    base = SAFE_NAME.sub("_", base).strip(". ")[:100]
    return base or "texture"


def allowed(name):
    return os.path.splitext(name or "")[1].lower() in IMAGE_EXTENSIONS


def cache_dir():
    try:
        base = os.path.join(compat.prefs_path(), "c4d_collab_assets")
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        base = os.path.join(tempfile.gettempdir(), "c4d_collab_assets")
    return base


def cache_path(sha, name):
    return os.path.join(cache_dir(), "%s_%s" % (sha[:16], safe_name(name)))


def project_dir():
    """The folder a guest's session document is given. Cinema 4D shortens a texture path to its bare name now and then
    (measured: after a click, on both characters of a rig); a bare name is looked up in the document's folder and "tex"
    below it, then in this computer's texture search paths. A document without a folder found Mert's skin only because a
    search path reached one of the ten projects that hold it, and not Auges', which only one project holds: its alpha
    channel then made the whole body transparent. Every received texture is therefore also kept here under its own
    name, in "tex", so a bare name is found on every computer."""
    return os.path.join(cache_dir(), "project")


def _publish(target, name):
    """Keep a copy of the cached file `target` as project_dir()/tex/<name>; a different file already there is kept (its
    name then stays unresolved and apply_files points the shader at the cached copy instead)."""
    folder = os.path.join(project_dir(), "tex")
    wanted = os.path.join(folder, safe_name(name))
    try:
        if os.path.isfile(wanted):
            return
        if not os.path.isdir(folder):
            os.makedirs(folder)
        with open(target, "rb") as source:
            raw = source.read()
        partial = wanted + ".part"
        with open(partial, "wb") as handle:
            handle.write(raw)
        os.rename(partial, wanted) if not os.path.exists(wanted) else os.remove(partial)
    except (IOError, OSError) as exc:
        LOG.warning("Cannot keep %r in the session folder: %s", name, exc)


def prepare_project(doc):
    """Give a guest's session document its folder (see project_dir)."""
    folder = project_dir()
    try:
        if not os.path.isdir(os.path.join(folder, "tex")):
            os.makedirs(os.path.join(folder, "tex"))
        doc.SetDocumentPath(compat.native_str(folder))
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        LOG.warning("The session document has no folder: textures may be looked up by name in vain")


def digest(path):
    """SHA-1 of a file (cached until its size or modification time changes), or None."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    known = _digests.get(path)
    if known is not None and known[0] == stat.st_size and known[1] == stat.st_mtime:
        return known[2]
    sha = hashlib.sha1()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                sha.update(block)
    except (IOError, OSError):
        return None
    _digests[path] = (stat.st_size, stat.st_mtime, sha.hexdigest())
    return sha.hexdigest()


def describe(path):
    """(sha1, file name) for a texture file we may send, else None."""
    if not path or not os.path.isabs(path) or not allowed(path):
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size > MAX_ASSET_BYTES:
        LOG.warning("Texture %s is %d MB: too big to send, other users will not see it", path, size // (1024 * 1024))
        return None
    sha = digest(path)
    return (sha, safe_name(path)) if sha else None


def encode(path):
    """base64(zlib(file)) of a texture file, or None if it cannot be read."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_ASSET_BYTES + 1)
    except (IOError, OSError):
        return None
    if len(raw) > MAX_ASSET_BYTES:
        return None
    return base64.b64encode(zlib.compress(raw, 1)).decode("ascii")


def store(sha, name, packed):
    """Write a received asset to the cache. Returns the path, or None when it is not acceptable."""
    if not is_asset_uuid(sha) or not allowed(name) or not isinstance(packed, string_types):
        LOG.warning("Refusing an asset (%r): not an acceptable image file", name)
        return None
    target = cache_path(sha, name)
    if os.path.isfile(target):
        _publish(target, name)
        return target
    try:
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(base64.b64decode(packed.encode("ascii")), MAX_ASSET_BYTES + 1)
    except (ValueError, TypeError, zlib.error, UnicodeError) as exc:
        LOG.warning("Refusing an asset (%r): %s", name, exc)
        return None
    if len(raw) > MAX_ASSET_BYTES or decompressor.unconsumed_tail:
        LOG.warning("Refusing an asset (%r): too big", name)
        return None
    if hashlib.sha1(raw).hexdigest() != sha:
        LOG.warning("Refusing an asset (%r): content does not match its id", name)
        return None
    try:
        folder = cache_dir()
        if not os.path.isdir(folder):
            os.makedirs(folder)
        partial = target + ".part"
        with open(partial, "wb") as handle:
            handle.write(raw)
        os.rename(partial, target) if not os.path.exists(target) else os.remove(partial)
    except (IOError, OSError) as exc:
        LOG.warning("Cannot store asset %r: %s", name, exc)
        return None
    _publish(target, name)
    return target


def _held_path(atom, key):
    """The file name the parameter holds now, as text (Cinema 4D may have made the absolute path the sender gave relative)."""
    try:
        held = atom.GetParameter(params.desc_id_of(key), compat.DESCFLAGS_GET)
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        return None
    if held is None:
        return None
    getter = getattr(held, "GetString", None)   # R19: a Filename object
    return compat.from_c4d_str(getter() if getter is not None else held)


def _found(atom, path, sha=None):
    """Is the file `path` names there for sure: an absolute path that exists, or a relative name inside the document's
    own folder (or its "tex" folder)? Cinema 4D's texture search paths do not count: they differ per computer, and a
    file that is found on one only by luck went missing on another (live: R19 host, 2023 guest; the guest found Mert's
    skin, which lies in ten project folders, but not Auges', which lies in one, and Auges' alpha channel made the whole
    body transparent)."""
    if not path:
        return False
    if os.path.isabs(path):
        return os.path.isfile(path)
    try:
        doc = atom.GetDocument()
        folder = compat.from_c4d_str(doc.GetDocumentPath()) if doc is not None else ""
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        folder = ""
    if not folder:
        return False   # a document without a folder: only the cached copy is certain
    for sub in ("", "tex"):
        candidate = os.path.join(folder, sub, path)
        if os.path.isfile(candidate):
            return sha is None or digest(candidate) == sha   # another file of the same name is not this texture
    return False


def apply_files(atom, data):
    """Point file parameters whose file this side cannot find at the cached copy of the asset. What the parameter holds is
    asked after it was written: Cinema 4D turns a path inside a project's texture folder into a bare name, which a guest
    whose document has no folder cannot find (measured on R19: the skin of a rig stayed black)."""
    changed = False
    for key, info in (data.get("f") or {}).items():
        if not isinstance(info, dict):
            continue
        current = (data.get("p") or {}).get(key)
        if not isinstance(current, string_types):
            continue
        try:
            held = _held_path(atom, key)
            if _found(atom, held if held is not None else current, info.get("h")):
                continue
            target = cache_path(info.get("h", ""), info.get("n", ""))
            if os.path.isfile(target):
                atom.SetParameter(params.desc_id_of(key), compat.native_str(target), compat.DESCFLAGS_SET)
                changed = True
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            LOG.exception("Could not point %s at its cached texture", key)
    return changed


def original_value(atom_value, key, known):
    """The path the session knows for a parameter that currently holds a cached copy (so the
    redirect never shows up as a local edit), else the value itself."""
    info = ((known or {}).get("f") or {}).get(key)
    if not isinstance(info, dict) or not isinstance(atom_value, string_types):
        return atom_value
    original = ((known or {}).get("p") or {}).get(key)
    if os.path.normcase(os.path.normpath(atom_value)) == os.path.normcase(os.path.normpath(
            cache_path(info.get("h", ""), info.get("n", "")))):
        if isinstance(original, string_types):
            return original
    elif isinstance(original, string_types) and not os.path.isabs(atom_value):
        # Cinema 4D shortened the absolute path to its bare name (see apply_files): the same file, not an edit
        if os.path.normcase(atom_value.replace("\\", "/")) == os.path.normcase(os.path.basename(original.replace("\\", "/"))):
            return original
    return atom_value
