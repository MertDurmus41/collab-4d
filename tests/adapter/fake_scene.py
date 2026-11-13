"""Fake Cinema 4D scene graph for adapter tests: documents, objects, tags,
parameters with descriptions, dirty counters, uids and polygon geometry.

Dirty behaviour follows what Phase 0 measured on R19/2023:
transform params -> MATRIX, other params / rename / points -> DATA,
inserting a tag -> owner DATA + MATRIX, hierarchy changes -> document only.
The document counters behave like R19 (one counter for everything).
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import itertools
import struct
import sys
import types

PY2 = sys.version_info[0] == 2


def native(text):
    """What a test should pass to the API: utf-8 str on Python 2."""
    return text.encode("utf-8") if PY2 and not isinstance(text, str) else text


def require_native(value, what):
    """R17-R22 reject unicode text; make the fake just as strict."""
    if PY2 and not isinstance(value, str):  # pragma: no cover - Python 2 only
        raise TypeError("%s must be str, not %s" % (what, type(value).__name__))

# Types --------------------------------------------------------------------------------
Onull, Ocube, Opolygon, Opython, Ofuture = 5140, 5159, 5100, 1023866, 9999999
Tphong, Ttarget, Tpython, Tuvw = 5612, 5676, 1022749, 5671
# A code-carrying type whose id the plugin does not know (like COFFEE on R19):
# only its type name gives it away.
Ocoffee_unknown, Tcoffee_unknown = 1234567, 1234568
Ospline = 5101
Mmaterial, Xbitmap, Xlayer, Xpython_shader, Ttexture = 5703, 5833, 5831, 1022000, 5616
Ojoint, Tweights = 1019362, 1019365
Omeshdeformer, MESHDEFORMER_CAGES, MESHDEFORMER_INITIALIZE = 1024543, 1008, 1001
TAG_DISPLAY, TAG_COMPOSITING = 5613, 5637
CUSTOMDATATYPE_INEXCLUDE_LIST, MSG_DESCRIPTION_COMMAND = 1009290, 26
Tposemorph = 1024237
ID_CA_POSE_MODE, ID_CA_POSE_MIXING, ID_CA_POSE_POINTS = 1001, 1009, 1017
ID_CA_POSE_MODE_EDIT, ID_CA_POSE_MODE_ANIMATE = 0, 1
ID_CA_POSE_ANIMATE_DATA = 4000
CAMORPH_DATA_FLAGS_POINTS, CAMORPH_MODE_ABS, CAMORPH_MODE_REL, CAMORPH_MODE_AUTO = 8, 0, 1, -1
CAMORPH_MODE_FLAGS_ALL, CAMORPH_MODE_FLAGS_EXPAND, CAMORPH_MODE_FLAGS_COLLAPSE = 1007, 4096, 2048
MATERIAL_COLOR_COLOR, MATERIAL_COLOR_SHADER, MATERIAL_REFLECT, BITMAP_FILENAME = 2002, 2003, 2004, 1010
TEXTURETAG_MATERIAL = 1010
DTYPE_FILENAME = 124
PRESENCE_OBJECT = 1000005
NBIT_OHIDE_VALUE, NBITCONTROL_SET_VALUE = 63, 1
Tpointselection, Tpolygonselection, Tedgeselection = 5674, 5673, 5701
SPLINEOBJECT_TYPE, SPLINETYPE_LINEAR, SPLINETYPE_BEZIER = 1000, 0, 4
SELECTION_TAGS = (Tpointselection, Tpolygonselection, Tedgeselection)
TYPE_NAMES = {Onull: "Null", Ocube: "Cube", Opolygon: "Polygon", Opython: "Python Generator",
              Tphong: "Phong", Ttarget: "Target", Tpython: "Python", Tuvw: "UVW",
              Ocoffee_unknown: "COFFEE Effector", Tcoffee_unknown: "COFFEE", Ospline: "Spline",
              Tpointselection: "Point Selection", Tpolygonselection: "Polygon Selection",
              Tedgeselection: "Edge Selection", PRESENCE_OBJECT: "Collab 4D Presence",
              Mmaterial: "Material", Xbitmap: "Bitmap", Xlayer: "Layer", Xpython_shader: "Python Shader",
              Ttexture: "Texture", Ojoint: "Joint", Tweights: "Weight", Tposemorph: "Pose Morph",
              Omeshdeformer: "Mesh"}

DTYPE_LONG, DTYPE_REAL, DTYPE_VECTOR, DTYPE_STRING, DTYPE_BOOL = 15, 19, 23, 130, 400006001
DTYPE_BASELISTLINK, DTYPE_TIME, DTYPE_SUBCONTAINER = 133, 22, 1

ID_BASELIST_NAME = 900
REL_POSITION, REL_ROTATION, REL_SCALE = 903, 904, 905
PRIM_CUBE_LEN, PRIM_CUBE_SUBX = 1100, 1102
PHONG_ANGLE = 10000
TARGET_LINK = 1001
TAG_VISIBLE = 0x1
BIT_ACTIVE = 0x2
CTbase = 5350
VECTOR_X, VECTOR_Y, VECTOR_Z = 1000, 1001, 1002
CINTERPOLATION_SPLINE, CINTERPOLATION_LINEAR, CINTERPOLATION_STEP = 1, 2, 3
TRACK_BEFORE, TRACK_AFTER = 910, 911
KEY_OPTION_DEFAULTS = {919: True, 923: True, 915: False, 917: False, 918: False,
                       902: False, 1244: False, 1246: True, 1245: False}

TRANSFORM = [(REL_POSITION, DTYPE_VECTOR), (REL_ROTATION, DTYPE_VECTOR), (REL_SCALE, DTYPE_VECTOR)]
SCHEMAS = {
    Onull: TRANSFORM,
    Ocube: TRANSFORM + [(PRIM_CUBE_LEN, DTYPE_VECTOR), (PRIM_CUBE_SUBX, DTYPE_LONG)],
    Opolygon: TRANSFORM,
    Opython: TRANSFORM + [(1000, DTYPE_STRING)],
    Tphong: [(PHONG_ANGLE, DTYPE_REAL)],
    Ttarget: [(TARGET_LINK, DTYPE_BASELISTLINK)],
    Tpython: [(1000, DTYPE_STRING)],
    Tuvw: [],
    CTbase: [(TRACK_BEFORE, DTYPE_LONG), (TRACK_AFTER, DTYPE_LONG)],
    Ospline: TRANSFORM + [(SPLINEOBJECT_TYPE, DTYPE_LONG)],
    Tpointselection: [], Tpolygonselection: [], Tedgeselection: [],
    PRESENCE_OBJECT: [],
    Ocoffee_unknown: TRANSFORM + [(1000, DTYPE_STRING)],
    Tcoffee_unknown: [(1000, DTYPE_STRING)],
    Ttexture: [(TEXTURETAG_MATERIAL, DTYPE_BASELISTLINK)],
    Mmaterial: [(MATERIAL_COLOR_COLOR, DTYPE_VECTOR), (MATERIAL_COLOR_SHADER, DTYPE_BASELISTLINK),
                (MATERIAL_REFLECT, DTYPE_REAL)],
    Xbitmap: [(BITMAP_FILENAME, DTYPE_FILENAME)],
    Xlayer: [],
    Xpython_shader: [(1000, DTYPE_STRING)],
    Ojoint: TRANSFORM + [(1000, DTYPE_REAL)],
    Omeshdeformer: TRANSFORM + [(MESHDEFORMER_CAGES, CUSTOMDATATYPE_INEXCLUDE_LIST), (1004, DTYPE_REAL)],
    Tweights: [],
    Tposemorph: [(ID_CA_POSE_MODE, DTYPE_LONG), (ID_CA_POSE_MIXING, DTYPE_LONG), (ID_CA_POSE_POINTS, DTYPE_BOOL)],
}
MATERIAL_TYPES = frozenset([Mmaterial])
SHADER_TYPES = frozenset([Xbitmap, Xlayer, Xpython_shader])
TAG_TYPES = frozenset([Tphong, Ttarget, Tpython, Tuvw, Tcoffee_unknown, Tpointselection,
                       Tpolygonselection, Tedgeselection, Ttexture, Tweights, Tposemorph,
                       TAG_DISPLAY, TAG_COMPOSITING])
CREATABLE = frozenset(SCHEMAS)  # Ofuture is "not installed" here


class Vector(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __eq__(self, other):
        return isinstance(other, Vector) and (self.x, self.y, self.z) == (other.x, other.y, other.z)

    def __ne__(self, other):
        return not self == other

    def __add__(self, other):
        return Vector(self.x + other.x, self.y + other.y, self.z + other.z)

    def __repr__(self):
        return "Vector(%g, %g, %g)" % (self.x, self.y, self.z)


class BaseTime(object):
    """BaseTime(seconds) or BaseTime(frame, fps), like the real one."""

    def __init__(self, z=0.0, n=None):
        self.n, self.d = float(z), float(n if n is not None else 1.0)

    def Get(self):
        return self.n / self.d

    def GetFrame(self, fps):
        return int(round(self.Get() * fps))

    def GetNumerator(self):
        return self.n

    def GetDenominator(self):
        return self.d


class CPolygon(object):
    def __init__(self, a, b, c, d=None):
        self.a, self.b, self.c, self.d = a, b, c, c if d is None else d


class Matrix(object):
    def __init__(self, off=None, v1=None, v2=None, v3=None):
        self.off = off if off is not None else Vector()
        self.v1 = v1 if v1 is not None else Vector(1, 0, 0)
        self.v2 = v2 if v2 is not None else Vector(0, 1, 0)
        self.v3 = v3 if v3 is not None else Vector(0, 0, 1)


class DescLevel(object):
    def __init__(self, ident, dtype=0, creator=0):
        self.id, self.dtype, self.creator = ident, dtype, creator


class DescID(object):
    def __init__(self, *levels):
        self._levels = [l if isinstance(l, DescLevel) else DescLevel(l) for l in levels]

    def GetDepth(self):
        return len(self._levels)

    def __getitem__(self, index):
        return self._levels[index]

    def key(self):
        return tuple(l.id for l in self._levels)


_counter = itertools.count(1)


class Atom(object):
    def __init__(self, ctype):
        self._type = ctype
        self._name = str("")
        self._ids = []
        self._dirty = {"DATA": 0, "MATRIX": 0, "CHILDREN": 0}
        self._tracks = []
        self._params = {}
        self._alive = True
        self._bits = 0
        for ident, dtype in SCHEMAS.get(ctype, []):
            self._params[(ident,)] = _default(dtype)

    # identity / naming
    def GetType(self):
        return self._type

    def GetTypeName(self):
        return str(TYPE_NAMES.get(self._type, "Unknown"))

    def GetName(self):
        return self._name

    def SetName(self, name):
        require_native(name, "SetName")
        self._name = name
        self._touch("DATA")

    def IsAlive(self):
        return self._alive

    def ChangeNBit(self, bit, mode):
        self._nbits = getattr(self, "_nbits", set())
        self._nbits.add(bit) if mode == NBITCONTROL_SET_VALUE else self._nbits.discard(bit)

    def GetNBit(self, bit):
        return bit in getattr(self, "_nbits", set())

    # selection bits (selecting does not touch any dirty counter, as measured)
    def SetBit(self, bit):
        self._bits |= bit

    def DelBit(self, bit):
        self._bits &= ~bit

    def GetBit(self, bit):
        return bool(self._bits & bit)

    def GetDocument(self):
        return self._document()

    # uids (AddUniqueID appends; the latest wins, like Phase 0 measured)
    def AddUniqueID(self, app_id, data):
        self._ids.append((app_id, bytes(bytearray(data))))

    def FindUniqueID(self, app_id):
        for key, value in reversed(self._ids):
            if key == app_id:
                return memoryview(value)
        return None

    # dirty
    def GetDirty(self, flag):
        return self._dirty.get(_DIRTY_NAMES.get(flag), 0)

    def _touch(self, *names):
        for name in names:
            self._dirty[name] += 1
        doc = self._document()
        if doc is not None:
            doc.bump()

    def _document(self):
        return None

    # parameters
    def GetDescription(self, flags):
        return [(None, DescID(DescLevel(ident, dtype, 0)), None) for ident, dtype in SCHEMAS.get(self._type, [])]

    def GetParameter(self, desc_id, flags):
        return self._params.get(desc_id.key())

    def SetParameter(self, desc_id, value, flags):
        key = desc_id.key()
        if key not in self._params:
            return False
        self._params[key] = value
        self._touch("MATRIX" if key[0] in (REL_POSITION, REL_ROTATION, REL_SCALE) else "DATA")
        return True

    # animation tracks
    def GetFirstCTrack(self):
        return self._tracks[0] if self._tracks else None

    def FindCTrack(self, desc_id):
        for track in self._tracks:
            if track._desc.key() == desc_id.key():
                return track
        return None

    def InsertTrackSorted(self, track):
        track._owner = self
        self._tracks.append(track)
        doc = self._document()
        if doc is not None:
            doc.bump()

    def __getitem__(self, ident):
        if hasattr(ident, "key"):   # a DescID, like the real one: atom[desc_id]
            ids = ident.key()
            if len(ids) == 2:   # one component of a vector
                vector = self._params[(ids[0],)]
                return {VECTOR_X: vector.x, VECTOR_Y: vector.y, VECTOR_Z: vector.z}[ids[1]]
            return self._params[ids]
        return self._params[(ident,)]

    def __setitem__(self, ident, value):
        self.SetParameter(DescID(ident), value, 0)


def _default(dtype):
    if dtype == DTYPE_VECTOR:
        return Vector()
    if dtype == DTYPE_REAL:
        return 0.0
    if dtype == DTYPE_LONG:
        return 0
    if dtype == DTYPE_STRING or dtype == DTYPE_FILENAME:
        return str("")
    return None


_DIRTY_NAMES = {}


class BaseObject(Atom):
    def __init__(self, ctype):
        Atom.__init__(self, ctype)
        self._parent = None
        self._doc = None
        self._children = []
        self._tags = []

    def __new__(cls, ctype=Onull, *args):
        if ctype not in CREATABLE or ctype in TAG_TYPES:
            return None
        if cls is BaseObject and ctype == Opolygon:
            return PolygonObject.__new__(PolygonObject, ctype)
        if cls is BaseObject and ctype == Ospline:
            return SplineObject.__new__(SplineObject, ctype)
        return Atom.__new__(cls)

    def _document(self):
        return self._doc

    def _siblings(self):
        if self._parent is not None:
            return self._parent._children
        return self._doc._roots if self._doc is not None else []

    def GetMg(self):
        position = self._params.get((REL_POSITION,), Vector())
        return Matrix(position)

    def GetCache(self):
        return None

    def GetDeformCache(self):
        return None

    def GetMp(self):
        return Vector()

    def GetRad(self):
        return Vector(50, 50, 50)

    def GetRelPos(self):
        return self._params.get((REL_POSITION,), Vector())

    def GetRelRot(self):
        return self._params.get((REL_ROTATION,), Vector())

    def GetRelScale(self):
        return self._params.get((REL_SCALE,), Vector(1, 1, 1))

    def GetUp(self):
        return self._parent

    def GetDown(self):
        return self._children[0] if self._children else None

    def GetNext(self):
        siblings = self._siblings()
        index = siblings.index(self) if self in siblings else -1
        return siblings[index + 1] if 0 <= index < len(siblings) - 1 else None

    def GetPred(self):
        siblings = self._siblings()
        index = siblings.index(self) if self in siblings else -1
        return siblings[index - 1] if index > 0 else None

    def Remove(self):
        doc = self._doc
        siblings = self._siblings()
        if self in siblings:
            siblings.remove(self)
        self._parent = None
        self._set_doc(None)
        if doc is not None:
            doc.bump()

    def _set_doc(self, doc):
        self._doc = doc
        for child in self._children:
            child._set_doc(doc)

    def GetFirstTag(self):
        return self._tags[0] if self._tags else None

    def InsertTag(self, tag, pred=None):
        index = self._tags.index(pred) + 1 if pred is not None else 0
        self._tags.insert(index, tag)
        tag._owner = self
        self._touch("DATA", "MATRIX")

    def MakeTag(self, ctype, pred=None):
        tag = BaseTag(ctype)
        self.InsertTag(tag, pred)
        return tag

    def Message(self, msg, data=None):
        """Records the buttons that were pressed: [(message, DescID key)]."""
        if not hasattr(self, "_pressed"):
            self._pressed = []
        if msg == MSG_DESCRIPTION_COMMAND and isinstance(data, dict) and "id" in data:
            self._pressed.append(data["id"].key())
            if self.GetType() == Omeshdeformer and data["id"].key() == (MESHDEFORMER_INITIALIZE,):
                self._initialize_cages()
        return True

    def _initialize_cages(self):
        """What a 2023 Mesh deformer's Initialize does to its cages: a Display and a Compositing tag (measured)."""
        try:
            cages = self[MESHDEFORMER_CAGES]
        except (KeyError, TypeError):
            cages = None
        doc = self.GetDocument() if hasattr(self, "GetDocument") else None
        if cages is None or doc is None:
            return
        for index in range(cages.GetObjectCount()):
            cage = cages.ObjectFromIndex(doc, index)
            if cage is not None:
                cage.MakeTag(TAG_DISPLAY)
                cage.MakeTag(TAG_COMPOSITING)


class InExcludeData(object):
    """c4d.InExcludeData: a list of objects with flags."""

    def __init__(self):
        self._items = []

    def InsertObject(self, obj, flags):
        self._items.append([obj, flags])
        return True

    def GetObjectCount(self):
        return len(self._items)

    def ObjectFromIndex(self, doc, index):
        return self._items[index][0] if 0 <= index < len(self._items) else None

    def GetFlags(self, index):
        return self._items[index][1]


class PolygonObject(BaseObject):
    def __init__(self, ctype=Opolygon, *args):
        BaseObject.__init__(self, Opolygon)
        self._points = []
        self._polys = []

    def __new__(cls, *args):
        return Atom.__new__(cls)

    def GetPointCount(self):
        return len(self._points)

    def GetPolygonCount(self):
        return len(self._polys)

    def GetAllPoints(self):
        return list(self._points)

    def GetAllPolygons(self):
        return list(self._polys)

    def GetPoint(self, index):
        return self._points[index]

    def GetPolygon(self, index):
        return self._polys[index]

    def ResizeObject(self, points, polys):
        """Keeps existing data and (as measured) resizes UVW tags, even when the size is the same."""
        self._points = (self._points + [Vector()] * points)[:points]
        self._polys = (self._polys + [CPolygon(0, 0, 0)] * polys)[:polys]
        for tag in self._tags:
            if isinstance(tag, UVWTag):
                tag._resize(len(self._polys))
        self._touch("DATA")
        return True

    def SetAllPoints(self, points):
        self._points = list(points)
        self._touch("DATA")

    def SetPoint(self, index, point):
        self._points[index] = point
        self._touch("DATA")

    def SetPolygon(self, index, poly):
        self._polys[index] = poly
        self._touch("DATA")


class SplineObject(BaseObject):
    """Tangent edits and segment flags move no dirty counter (measured on 2023)."""

    def __init__(self, ctype=Ospline, *args):
        BaseObject.__init__(self, Ospline)
        self._points = []
        self._segments = []
        self._tangents = []
        self._params[(SPLINEOBJECT_TYPE,)] = SPLINETYPE_BEZIER

    def __new__(cls, *args):
        return Atom.__new__(cls)

    def GetPointCount(self):
        return len(self._points)

    def GetAllPoints(self):
        return list(self._points)

    def GetSegmentCount(self):
        return len(self._segments)

    def ResizeObject(self, points, segments):
        self._points = (self._points + [Vector()] * points)[:points]
        self._segments = (self._segments + [{"cnt": 0, "closed": False}] * segments)[:segments]
        self._tangents = (self._tangents + [(Vector(), Vector())] * points)[:points]
        self._touch("DATA")
        return True

    def SetAllPoints(self, points):
        self._points = list(points)
        self._touch("DATA")

    def SetPoint(self, index, point):
        self._points[index] = point
        self._touch("DATA")

    def GetSegment(self, index):
        if not 0 <= index < len(self._segments):
            raise IndexError("segment index out of range")
        return dict(self._segments[index])

    def SetSegment(self, index, count, closed):
        if not 0 <= index < len(self._segments):
            raise IndexError("segment index out of range")
        self._segments[index] = {"cnt": count, "closed": bool(closed)}

    def GetTangentCount(self):
        return len(self._points) if self._params[(SPLINEOBJECT_TYPE,)] == SPLINETYPE_BEZIER else 0

    def GetTangent(self, index):
        vl, vr = self._tangents[index]
        return {"vl": vl, "vr": vr}

    def SetTangent(self, index, vl, vr):
        self._tangents[index] = (vl, vr)


class MaterialLayer(object):
    def __init__(self, data_id):
        self._id, self._flags, self._name = data_id, 7, str("Layer")

    def GetDataID(self):
        return self._id

    def GetFlags(self):
        return self._flags

    def SetFlags(self, flags):
        self._flags = flags

    def GetName(self):
        return self._name

    def SetName(self, name):
        require_native(name, "SetName")
        self._name = name


class BaseMaterial(Atom):
    """A material: lives in the document's material list and owns a tree of shaders."""

    # Reflectance layers as measured on 2023: a new material has one layer, ids count up in steps of
    # 512 from 526336 and are never reused, the newest layer is on top, every layer owns a block of ids.
    LAYER_FIRST, LAYER_STEP = 526336, 512
    LAYER_PARAMS = ((16, DTYPE_REAL), (32, DTYPE_VECTOR))

    def __init__(self, ctype=Mmaterial):
        Atom.__init__(self, ctype)
        self._doc = None
        self._shaders = []
        self._layers = []
        self._next_layer = 0
        if ctype == Mmaterial:
            self.AddReflectionLayer()

    def __new__(cls, ctype=Mmaterial, *args):
        return Atom.__new__(cls) if ctype in MATERIAL_TYPES else None

    def _document(self):
        return self._doc

    def AddReflectionLayer(self):
        layer = MaterialLayer(self.LAYER_FIRST + self.LAYER_STEP * self._next_layer)
        self._next_layer += 1
        self._layers.insert(0, layer)
        for offset, dtype in self.LAYER_PARAMS:
            self._params[(layer.GetDataID() + offset,)] = _default(dtype)
        self._touch("DATA")
        return layer

    def _drop_layer(self, layer):
        for offset, _dtype in self.LAYER_PARAMS:
            self._params.pop((layer.GetDataID() + offset,), None)
        self._layers.remove(layer)

    def RemoveReflectionLayerIndex(self, index):
        self._drop_layer(self._layers[index])
        self._touch("DATA")
        return True

    def RemoveReflectionAllLayers(self):
        for layer in list(self._layers):
            self._drop_layer(layer)
        self._touch("DATA")
        return True

    def GetReflectionLayerCount(self):
        return len(self._layers)

    def GetReflectionLayerIndex(self, index):
        return self._layers[index] if 0 <= index < len(self._layers) else None

    def GetDescription(self, flags):
        found = Atom.GetDescription(self, flags)
        for layer in self._layers:
            for offset, dtype in self.LAYER_PARAMS:
                found.append((None, DescID(DescLevel(layer.GetDataID() + offset, dtype, self._type)), None))
        return found

    def GetFirstShader(self):
        return self._shaders[0] if self._shaders else None

    def InsertShader(self, shader, pred=None):
        index = self._shaders.index(pred) + 1 if pred is not None else 0
        self._shaders.insert(index, shader)
        shader._owner = self
        self._touch("DATA")

    def _siblings(self):
        return self._doc._materials if self._doc is not None else []

    def GetNext(self):
        siblings = self._siblings()
        index = siblings.index(self) if self in siblings else -1
        return siblings[index + 1] if 0 <= index < len(siblings) - 1 else None

    def Remove(self):
        doc = self._doc
        if doc is not None and self in doc._materials:
            doc._materials.remove(self)
        self._doc = None
        if doc is not None:
            doc.bump()

    def GetUp(self):
        return None

    # tracks need an owner document through _document()


class BaseShader(Atom):
    def __init__(self, ctype=Xbitmap):
        Atom.__init__(self, ctype)
        self._owner = None
        self._shaders = []

    def __new__(cls, ctype=Xbitmap, *args):
        return Atom.__new__(cls) if ctype in SHADER_TYPES else None

    def _document(self):
        return self._owner._document() if self._owner is not None else None

    def GetFirstShader(self):
        return self._shaders[0] if self._shaders else None

    def InsertShader(self, shader, pred=None):
        index = self._shaders.index(pred) + 1 if pred is not None else 0
        self._shaders.insert(index, shader)
        shader._owner = self
        self._touch("DATA")

    def GetNext(self):
        siblings = self._owner._shaders if self._owner is not None else []
        index = siblings.index(self) if self in siblings else -1
        return siblings[index + 1] if 0 <= index < len(siblings) - 1 else None

    def Remove(self):
        owner = self._owner
        if owner is not None and self in owner._shaders:
            doc = owner._document()
            owner._shaders.remove(self)
            self._owner = None
            if doc is not None:
                doc.bump()

    def GetUp(self):
        return None  # as measured: shaders do not report a parent


class BaseSelect(object):
    def __init__(self):
        self._items = set()

    def Select(self, index):
        self._items.add(index)
        return True

    def Deselect(self, index):
        self._items.discard(index)
        return True

    def DeselectAll(self):
        self._items = set()

    def SelectAll(self, a, b=None):
        pass  # selected nothing on C4D 2023; the plugin must not rely on it

    def IsSelected(self, index):
        return index in self._items

    def GetCount(self):
        return len(self._items)

    def _spans(self):
        spans = []
        for index in sorted(self._items):
            if spans and spans[-1][1] == index - 1:
                spans[-1][1] = index
            else:
                spans.append([index, index])
        return spans

    def GetSegments(self):
        return len(self._spans())

    def GetRange(self, segment, maximum):
        spans = self._spans()
        return tuple(spans[segment]) if 0 <= segment < len(spans) else None

    def GetAll(self, count):
        return [1 if i in self._items else 0 for i in range(count)]

    def SetAll(self, flags):
        self._items = set(i for i, flag in enumerate(flags) if flag)


def _float32(value):
    return struct.unpack("f", struct.pack("f", value))[0]


class BaseTag(Atom):
    def __init__(self, ctype):
        Atom.__init__(self, ctype)
        self._owner = None

    def __new__(cls, ctype=Tphong, *args):
        if ctype not in TAG_TYPES:
            return None
        if cls is BaseTag and ctype in SELECTION_TAGS:
            return SelectionTag.__new__(SelectionTag, ctype)
        if cls is BaseTag and ctype == Tuvw:
            return UVWTag.__new__(UVWTag, ctype)
        if cls is BaseTag and ctype == Tweights:
            return WeightTag.__new__(WeightTag, ctype)
        if cls is BaseTag and ctype == Tposemorph:
            return PoseMorphTag.__new__(PoseMorphTag, ctype)
        return Atom.__new__(cls)

    def _document(self):
        return self._owner._doc if self._owner is not None else None

    def GetObject(self):
        return self._owner

    def GetInfo(self):
        return TAG_VISIBLE

    def GetNext(self):
        tags = self._owner._tags if self._owner is not None else []
        index = tags.index(self) if self in tags else -1
        return tags[index + 1] if 0 <= index < len(tags) - 1 else None

    def Remove(self):
        owner = self._owner
        if owner is not None:
            owner._tags.remove(self)
            owner._touch("DATA")
        self._owner = None


class CKey(object):
    """Tangent and option edits move no dirty counter (as measured on 2023)."""

    def __init__(self, seconds):
        self.t = BaseTime(seconds)
        self.v = 0.0
        self.i = CINTERPOLATION_SPLINE
        self.tl, self.tr = BaseTime(-0.1), BaseTime(0.1)
        self.vl = self.vr = 0.0
        self.options = dict(KEY_OPTION_DEFAULTS)

    def GetTime(self):
        return self.t

    def SetTime(self, curve, t):
        self.t = t
        curve._changed(resort=True)

    def GetValue(self):
        return self.v

    def SetValue(self, curve, v):
        self.v = float(v)
        curve._changed()

    def GetInterpolation(self):
        return self.i

    def SetInterpolation(self, curve, i):
        self.i = i
        curve._changed()

    def GetTimeLeft(self):
        return self.tl

    def GetTimeRight(self):
        return self.tr

    def GetValueLeft(self):
        return self.vl

    def GetValueRight(self):
        return self.vr

    def SetTimeLeft(self, curve, t):
        self.tl = t

    def SetTimeRight(self, curve, t):
        self.tr = t

    def SetValueLeft(self, curve, v):
        self.vl = float(v)

    def SetValueRight(self, curve, v):
        self.vr = float(v)

    def GetParameter(self, desc_id, flags):
        return self.options.get(desc_id.key()[0])

    def SetParameter(self, desc_id, value, flags):
        ident = desc_id.key()[0]
        if ident not in self.options:
            return False
        self.options[ident] = bool(value)
        return True


class CCurve(object):
    def __init__(self, track):
        self._track = track
        self._keys = []
        self._dirty = {"DATA": 0, "MATRIX": 0, "CHILDREN": 0}

    def GetDirty(self, flag):
        return self._dirty.get(_DIRTY_NAMES.get(flag), 0)

    def _changed(self, resort=False):
        if resort:
            self._keys.sort(key=lambda k: k.t.Get())
        self._dirty["CHILDREN"] += 1
        owner = self._track._owner
        doc = owner._document() if owner is not None else None
        if doc is not None:
            doc.bump()  # HDIRTYFLAGS_ANIMATION

    def GetKeyCount(self):
        return len(self._keys)

    def GetKey(self, index):
        return self._keys[index]

    def GetValue(self, time, fps):
        """What the track gives at `time` (linear between the keys, like a Linear key interpolation)."""
        if not self._keys:
            return 0.0
        at = time.Get()
        if at <= self._keys[0].t.Get():
            return self._keys[0].GetValue()
        for before, after in zip(self._keys, self._keys[1:]):
            if at <= after.t.Get():
                span = after.t.Get() - before.t.Get()
                mix = 0.0 if span == 0 else (at - before.t.Get()) / span
                return before.GetValue() + (after.GetValue() - before.GetValue()) * mix
        return self._keys[-1].GetValue()

    def AddKey(self, t):
        key = CKey(t.Get())
        self._keys.append(key)
        self._changed(resort=True)
        return {"key": key, "nidx": self._keys.index(key)}

    def DelKey(self, index):
        del self._keys[index]
        self._changed()

    def FlushKeys(self):
        self._keys = []
        self._changed()


class CTrack(Atom):
    def __init__(self, owner, desc_id):
        Atom.__init__(self, CTbase)
        self._desc = desc_id
        self._owner = None
        self._curve = CCurve(self)

    def _touch(self, *names):
        pass  # track settings move no dirty counter (measured)

    def GetDescriptionID(self):
        return self._desc

    def GetCurve(self):
        return self._curve

    def GetObject(self):
        return self._owner

    def _document(self):
        return self._owner._document() if self._owner is not None else None

    def GetNext(self):
        tracks = self._owner._tracks if self._owner is not None else []
        index = tracks.index(self) if self in tracks else -1
        return tracks[index + 1] if 0 <= index < len(tracks) - 1 else None

    def Remove(self):
        owner = self._owner
        if owner is not None and self in owner._tracks:
            owner._tracks.remove(self)
            doc = owner._document()
            if doc is not None:
                doc.bump()
        self._owner = None


class SelectionTag(BaseTag):
    """Selection edits move no dirty counter (measured)."""

    def __init__(self, ctype=Tpolygonselection, *args):
        BaseTag.__init__(self, ctype)
        self._select = BaseSelect()

    def __new__(cls, *args):
        return Atom.__new__(cls)

    def GetBaseSelect(self):
        return self._select


class UVWTag(BaseTag):
    """Starts with 0 entries; the owner's ResizeObject sizes it. Stores float32 like C4D."""

    def __init__(self, ctype=Tuvw, *args):
        BaseTag.__init__(self, Tuvw)
        self._uv = []
        self.counts_edits = True  # False: SetSlow moves no dirty counter (observed on R19)

    def __new__(cls, *args):
        return Atom.__new__(cls)

    def _resize(self, count):
        blank = lambda: [Vector(), Vector(), Vector(), Vector()]
        self._uv = (self._uv + [blank() for _ in range(count)])[:count]

    def GetDataCount(self):
        return len(self._uv)

    def GetSlow(self, index):
        if not 0 <= index < len(self._uv):
            raise IndexError("Index out of range")
        a, b, c, d = self._uv[index]
        return {"a": a, "b": b, "c": c, "d": d}

    def SetSlow(self, index, a, b, c, d):
        if not 0 <= index < len(self._uv):
            raise IndexError("Index out of range")
        self._uv[index] = [Vector(_float32(v.x), _float32(v.y), 0) for v in (a, b, c, d)]
        if self.counts_edits:
            self._touch("DATA")


class WeightTag(BaseTag):
    """Skin weights like CAWeightTag: joints in a list, a rest state and a sparse weight map per joint
    (trailing zeros are dropped by the real thing)."""

    def __init__(self, ctype=Tweights):
        BaseTag.__init__(self, ctype)
        self._joints = []
        self._rest = []
        self._maps = []
        self._geom = None   # the matrix of the mesh when it was bound; a new tag binds where the mesh stands

    def __new__(cls, ctype=Tweights, *args):
        return Atom.__new__(cls)

    def GetGeomMg(self):
        if self._geom is not None:
            return self._geom
        owner = self.GetObject()
        return owner.GetMg() if owner is not None and hasattr(owner, "GetMg") else Matrix()

    def SetGeomMg(self, matrix):
        self._geom = matrix

    def GetJointCount(self):
        return len(self._joints)

    def GetJoint(self, index, doc):
        return self._joints[index]

    def AddJoint(self, joint):
        self._joints.append(joint)
        self._rest.append(None)
        self._maps.append([])
        self._touch("DATA")
        return len(self._joints) - 1

    def RemoveJoint(self, joint):
        index = self._joints.index(joint)
        del self._joints[index], self._rest[index], self._maps[index]

    def FindJoint(self, joint):
        return self._joints.index(joint) if joint in self._joints else -1

    def GetJointRestState(self, index):
        state = self._rest[index]
        if state is None:
            identity = Matrix()
            state = {"m_bMg": identity, "m_bMi": identity, "m_oMg": identity, "m_oMi": identity, "m_Len": 0.0}
        return dict(state)

    def SetJointRestState(self, index, bmg, bmi, omg, omi, length):
        self._rest[index] = {"m_bMg": bmg, "m_bMi": bmi, "m_oMg": omg, "m_oMi": omi, "m_Len": length}

    def GetWeightMap(self, index):
        values = list(self._maps[index])
        while values and values[-1] == 0.0:
            values.pop()
        return values

    def SetWeightMap(self, index, values):
        self._maps[index] = [round(float(v) * 65535.0) / 65535.0 for v in values]  # 16 bit like C4D

    def GetWeight(self, index, point):
        values = self._maps[index]
        return values[point] if point < len(values) else 0.0

    def SetWeight(self, index, point, weight):
        values = self._maps[index]
        if point >= len(values):
            values.extend([0.0] * (point + 1 - len(values)))
        values[point] = round(float(weight) * 65535.0) / 65535.0   # 16 bit like C4D
        return True

    def GetWeightDirty(self):
        return 0


class MorphNode(object):
    """What CAMorph.GetFirst() returns: the points of an expanded morph, or one meaningless point when
    the morph is collapsed (measured)."""

    def __init__(self, points):
        self._points = points

    def GetPointCount(self):
        return len(self._points)

    def GetPoint(self, index):
        return self._points[index]

    def SetPoint(self, index, value):
        self._points[index] = Vector(value.x, value.y, value.z)


class Morph(object):
    """CAMorph: offsets from the base pose. Expanding in REL mode exposes the offsets, ABS mode exposes
    base + offsets; collapsing keeps what was edited. Nothing here moves a dirty counter (measured)."""

    def __init__(self, tag, name):
        self._tag = tag
        self._name = name
        self._offsets = []
        self._absolute = None   # the base pose (morph 0) keeps the base mesh itself
        self._open = None   # (mode, MorphNode) while expanded
        self._number = 0        # set by AddMorph: the lowest number no morph of the tag holds (measured on 2023)

    def GetName(self):
        return self._name

    def SetName(self, name):
        require_native(name, "CAMorph.SetName")
        self._name = name

    def Store(self, doc, tag, flags):
        """Takes what the mesh shows now: the base pose keeps it as the base, a pose as offsets from it."""
        shown = tag.GetObject().GetAllPoints()
        if tag._morphs and tag._morphs[0] is self:
            self._absolute = list(shown)
            self._offsets = [Vector() for _ in shown]
        else:
            self._offsets = [Vector(p.x - b.x, p.y - b.y, p.z - b.z) for p, b in zip(shown, tag._base())]
        return True

    def SetMode(self, doc, tag, flags, mode):
        if flags & CAMORPH_MODE_FLAGS_EXPAND:
            if mode == CAMORPH_MODE_ABS:
                points = [Vector(b.x + o.x, b.y + o.y, b.z + o.z) for b, o in zip(tag._base(), self._offsets)]
            else:
                points = [Vector(o.x, o.y, o.z) for o in self._offsets]
            self._open = (mode, MorphNode(points))
        elif flags & CAMORPH_MODE_FLAGS_COLLAPSE and self._open is not None:
            mode_open, node = self._open
            if mode_open == CAMORPH_MODE_ABS:
                if tag._morphs and tag._morphs[0] is self:
                    self._absolute = list(node._points)
                else:
                    self._offsets = [Vector(p.x - b.x, p.y - b.y, p.z - b.z)
                                     for p, b in zip(node._points, tag._base())]
            else:
                self._offsets = list(node._points)
            self._open = None
        return True

    def GetFirst(self):
        return self._open[1] if self._open is not None else MorphNode([Vector()])


class PoseMorphTag(BaseTag):
    """CAPoseMorphTag with point morphs, as measured on 2023 (tests/c4d_manual/probe_posemorph.py):
    morph edits, renames, new morphs and switching the active pose move no counter; strength sliders
    live at (4000, 1001 + 100 * number) outside the description and move DATA; RemoveMorph moves DATA.
    A morph keeps its number when others are removed; AddMorph gives the lowest free number.
    The tag writes its result into the owner's own points (no deform cache): animated in animate mode,
    the selected pose in edit mode (here at once; the real tag does it when the scene is evaluated)."""

    def __init__(self, ctype=Tposemorph):
        BaseTag.__init__(self, ctype)
        self._morphs = []
        self._strengths = {}
        self._active = -1
        self._params[(ID_CA_POSE_MODE,)] = ID_CA_POSE_MODE_EDIT

    def __new__(cls, ctype=Tposemorph, *args):
        return Atom.__new__(cls)

    def _point_count(self):
        owner = self.GetObject()
        return owner.GetPointCount() if owner is not None and hasattr(owner, "GetPointCount") else 0

    def _base(self):
        if self._morphs and self._morphs[0]._absolute is not None:
            return list(self._morphs[0]._absolute)
        owner = self.GetObject()
        return owner.GetAllPoints() if owner is not None else []

    def _evaluate(self):
        """What a scene evaluation does: write the result into the mesh (moves its DATA counter)."""
        owner = self.GetObject()
        if owner is None or not self._morphs or self._morphs[0]._absolute is None:
            return
        if self._params[(ID_CA_POSE_MODE,)] == ID_CA_POSE_MODE_ANIMATE:
            owner._points = self.deformed()
        elif 0 < self._active < len(self._morphs):
            owner._points = [Vector(b.x + o.x, b.y + o.y, b.z + o.z)
                             for b, o in zip(self._base(), self._morphs[self._active]._offsets)]
        else:
            owner._points = self._base()
        owner._touch("DATA")

    def GetMorphCount(self):
        return len(self._morphs)

    def GetMorph(self, index):
        return self._morphs[index] if 0 <= index < len(self._morphs) else None

    def InitMorphs(self):
        pass

    def UpdateMorphs(self):
        self._evaluate()  # stands in for the scene evaluation that follows

    def ExitEdit(self, doc, force):
        return True

    def AddMorph(self):
        morph = Morph(self, str("Base Pose") if not self._morphs else str("Pose.%d" % len(self._morphs)))
        taken = set(m._number for m in self._morphs)
        number = 0
        while number in taken:
            number += 1
        morph._number = number
        self._morphs.append(morph)
        return morph

    def RemoveMorph(self, index):
        number = self._morphs[index]._number
        del self._morphs[index]
        self._strengths.pop(number, None)
        self._touch("DATA")

    def GetMorphID(self, index):
        return DescID(DescLevel(ID_CA_POSE_ANIMATE_DATA, DTYPE_SUBCONTAINER, 0),
                      DescLevel(1001 + 100 * self._morphs[index]._number, DTYPE_REAL, 0))

    def _numbered(self, number):
        return any(m._number == number for m in self._morphs)

    def GetActiveMorphIndex(self):
        return self._active

    def SetActiveMorphIndex(self, index):
        """In edit mode the mesh shows the selected pose; leaving a pose stores the mesh into it.
        Moves no counter (measured); the mesh changes when the scene is evaluated."""
        owner = self.GetObject()
        editing = self._params[(ID_CA_POSE_MODE,)] == ID_CA_POSE_MODE_EDIT
        if editing and 0 <= self._active < len(self._morphs):
            self._morphs[self._active].Store(None, self, CAMORPH_DATA_FLAGS_POINTS)
        self._active = index
        if editing:
            dirty = dict(owner._dirty)
            self._evaluate()
            owner._dirty = dirty

    def GetParameter(self, desc_id, flags):
        key = desc_id.key()
        if len(key) == 2 and key[0] == ID_CA_POSE_ANIMATE_DATA:
            number = (key[1] - 1001) // 100
            return self._strengths.get(number, 0.0) if self._numbered(number) else None
        return BaseTag.GetParameter(self, desc_id, flags)

    def SetParameter(self, desc_id, value, flags):
        key = desc_id.key()
        if len(key) == 2 and key[0] == ID_CA_POSE_ANIMATE_DATA:
            number = (key[1] - 1001) // 100
            if not self._numbered(number):
                return False
            self._strengths[number] = float(value)
            self._touch("DATA")
            self._evaluate()
            return True
        result = BaseTag.SetParameter(self, desc_id, value, flags)
        if key == (ID_CA_POSE_MODE,):
            self._evaluate()
        return result

    def deformed(self):
        """Test helper: the mesh as the tag shows it in animate mode."""
        points = self._base()
        if self._params[(ID_CA_POSE_MODE,)] != ID_CA_POSE_MODE_ANIMATE:
            return points
        for index, morph in enumerate(self._morphs):
            if index == 0:
                continue
            weight = self._strengths.get(morph._number, 0.0)
            points = [Vector(p.x + o.x * weight, p.y + o.y * weight, p.z + o.z * weight)
                      for p, o in zip(points, morph._offsets)]
        return points


class FakeCamera(object):
    def __init__(self, matrix):
        self._matrix = matrix

    def GetMg(self):
        return self._matrix


class FakeBaseDraw(object):
    """A viewport: reports the document's editor camera (set doc.camera_matrix in tests)."""

    def __init__(self, doc):
        self._doc = doc

    def GetSceneCamera(self, doc):
        return None

    def GetEditorCamera(self):
        return FakeCamera(getattr(self._doc, "camera_matrix", None) or Matrix(Vector(0, 100, -500)))

    def DrawHUDText(self, x, y, text):
        pass


def animate(atom, param_id, frames_values, fps=30, sub=None):
    """Helper for tests: keyframe a parameter (sub = VECTOR_X/Y/Z for vectors)."""
    levels = [DescLevel(param_id, DTYPE_VECTOR if sub is not None else DTYPE_REAL, 0)]
    if sub is not None:
        levels.append(DescLevel(sub, DTYPE_REAL, 0))
    desc_id = DescID(*levels)
    track = atom.FindCTrack(desc_id)
    if track is None:
        track = CTrack(atom, desc_id)
        atom.InsertTrackSorted(track)
    curve = track.GetCurve()
    for frame, value in frames_values:
        key = curve.AddKey(BaseTime(frame, fps))["key"]
        key.SetValue(curve, value)
    return track


class BaseDocument(object):
    def __init__(self):
        self._roots = []
        self._materials = []
        self._hdirty = 0
        self._alive = True
        self.name = str("")
        self._fps = 30
        self._time = BaseTime(0.0)
        self._min, self._max = BaseTime(0.0), BaseTime(3.0)
        self._lmin, self._lmax = BaseTime(0.0), BaseTime(3.0)

    def GetActiveBaseDraw(self):
        """No viewport unless a test sets camera_matrix (like a headless c4dpy document)."""
        return FakeBaseDraw(self) if getattr(self, "camera_matrix", None) is not None else None

    def GetFps(self):
        return self._fps

    def GetNext(self):
        documents = getattr(self, "_documents", None)
        if documents is None or self not in documents.all:
            return None
        index = documents.all.index(self)
        return documents.all[index + 1] if index + 1 < len(documents.all) else None

    def GetDocumentName(self):
        return self.name

    def GetDocumentPath(self):
        return str(getattr(self, "_path", ""))

    def SetDocumentPath(self, path):
        self._path = path

    def SetFps(self, fps):
        self._fps = fps
        self.bump()

    def GetTime(self):
        return self._time

    def SetTime(self, t):
        self._time = t  # changing the frame moves no counter (measured)

    def GetMinTime(self):
        return self._min

    def GetMaxTime(self):
        return self._max

    def GetLoopMinTime(self):
        return self._lmin

    def GetLoopMaxTime(self):
        return self._lmax

    def SetMinTime(self, t):
        self._min = t
        self.bump()

    def SetMaxTime(self, t):
        self._max = t
        self.bump()

    def SetLoopMinTime(self, t):
        self._lmin = t
        self.bump()

    def SetLoopMaxTime(self, t):
        self._lmax = t
        self.bump()

    def bump(self):
        self._hdirty += 1

    def GetHDirty(self, flags):
        return self._hdirty

    def IsAlive(self):
        return self._alive

    def SetDocumentName(self, name):
        require_native(name, "SetDocumentName")
        self.name = name

    def GetFirstObject(self):
        return self._roots[0] if self._roots else None

    def GetFirstMaterial(self):
        return self._materials[0] if self._materials else None

    def InsertMaterial(self, material, pred=None):
        if material._doc is not None:
            raise RuntimeError("material is still inserted somewhere")
        index = self._materials.index(pred) + 1 if pred is not None else 0
        self._materials.insert(index, material)
        material._doc = self
        self.bump()

    def InsertObject(self, obj, parent=None, pred=None):
        if obj.GetUp() is not None or obj._doc is not None:
            raise RuntimeError("object is still inserted somewhere")
        if pred is not None:
            parent = pred.GetUp()
            siblings = parent._children if parent is not None else self._roots
            siblings.insert(siblings.index(pred) + 1, obj)
        elif parent is not None:
            parent._children.insert(0, obj)
        else:
            self._roots.insert(0, obj)
        obj._parent = parent
        obj._set_doc(self)
        self.bump()

    def _all_objects(self):
        result = []
        stack = list(reversed(self._roots))
        while stack:
            obj = stack.pop()
            result.append(obj)
            stack.extend(reversed(obj._children))
        return result

    def GetActiveObjects(self, flags=0):
        return [o for o in self._all_objects() if o.GetBit(BIT_ACTIVE)]

    def GetActiveTags(self):
        return [t for o in self._all_objects() for t in o._tags if t.GetBit(BIT_ACTIVE)]

    def SearchObject(self, name):
        name = native(name)
        stack = list(self._roots)
        while stack:
            obj = stack.pop(0)
            if obj.GetName() == name:
                return obj
            stack.extend(obj._children)
        return None


LOCAL_ONLY = frozenset([Opython, Tpython, Ocoffee_unknown, Tcoffee_unknown])


def walk(doc, shared_only=False):
    """Readable dump of a document for assertions: nested (name, type, tags, children).
    shared_only drops what by design never leaves a machine (code-carrying types)."""
    def keep(atom):
        if atom.GetType() == PRESENCE_OBJECT:
            return False
        return not (shared_only and atom.GetType() in LOCAL_ONLY)

    def dump(obj):
        tags = [(t.GetName(), t.GetType()) for t in obj._tags if keep(t)]
        return (obj.GetName(), obj.GetType(), tags, [dump(c) for c in obj._children if keep(c)])
    return [dump(o) for o in doc._roots if keep(o)]


class Documents(object):
    """State behind the fake c4d.documents module."""

    def __init__(self):
        self.active = BaseDocument()
        self.active._documents = self
        self.all = [self.active]

    def GetActiveDocument(self):
        return self.active

    def GetFirstDocument(self):
        return self.all[0] if self.all else None

    def SetActiveDocument(self, doc):
        self.active = doc

    def InsertBaseDocument(self, doc):
        doc._documents = self
        self.all.append(doc)

    def KillDocument(self, doc):
        doc._alive = False
        if doc in self.all:
            self.all.remove(doc)


def install_into(c4d, documents_module):
    """Attach scene classes and constants to the fake c4d module."""
    names = dict(
        Ojoint=Ojoint, Tweights=Tweights, Tposemorph=Tposemorph, ID_CA_POSE_MODE=ID_CA_POSE_MODE,
        ID_CA_POSE_MIXING=ID_CA_POSE_MIXING, ID_CA_POSE_POINTS=ID_CA_POSE_POINTS,
        ID_CA_POSE_MODE_EDIT=ID_CA_POSE_MODE_EDIT, ID_CA_POSE_MODE_ANIMATE=ID_CA_POSE_MODE_ANIMATE,
        ID_CA_POSE_ANIMATE_DATA=ID_CA_POSE_ANIMATE_DATA, CAMORPH_DATA_FLAGS_POINTS=CAMORPH_DATA_FLAGS_POINTS,
        CAMORPH_MODE_ABS=CAMORPH_MODE_ABS, CAMORPH_MODE_REL=CAMORPH_MODE_REL, CAMORPH_MODE_AUTO=CAMORPH_MODE_AUTO,
        CAMORPH_MODE_FLAGS_ALL=CAMORPH_MODE_FLAGS_ALL, CAMORPH_MODE_FLAGS_EXPAND=CAMORPH_MODE_FLAGS_EXPAND,
        CAMORPH_MODE_FLAGS_COLLAPSE=CAMORPH_MODE_FLAGS_COLLAPSE, Mmaterial=Mmaterial, Xbitmap=Xbitmap, Xlayer=Xlayer, Ttexture=Ttexture,
        MATERIAL_COLOR_COLOR=MATERIAL_COLOR_COLOR, MATERIAL_COLOR_SHADER=MATERIAL_COLOR_SHADER,
        TEXTURETAG_MATERIAL=TEXTURETAG_MATERIAL, DTYPE_FILENAME=DTYPE_FILENAME,
        Onull=Onull, Ocube=Ocube, Opolygon=Opolygon, Opython=Opython,
        Tphong=Tphong, Ttargetexpression=Ttarget, Tpython=Tpython, Tuvw=Tuvw,
        DTYPE_LONG=DTYPE_LONG, DTYPE_REAL=DTYPE_REAL, DTYPE_VECTOR=DTYPE_VECTOR,
        DTYPE_STRING=DTYPE_STRING, DTYPE_BOOL=DTYPE_BOOL, DTYPE_BASELISTLINK=DTYPE_BASELISTLINK,
        DTYPE_TIME=DTYPE_TIME, DTYPE_SUBCONTAINER=DTYPE_SUBCONTAINER,
        ID_BASELIST_NAME=ID_BASELIST_NAME, ID_BASEOBJECT_REL_POSITION=REL_POSITION,
        ID_BASEOBJECT_REL_ROTATION=REL_ROTATION, ID_BASEOBJECT_REL_SCALE=REL_SCALE,
        PRIM_CUBE_LEN=PRIM_CUBE_LEN, PRIM_CUBE_SUBX=PRIM_CUBE_SUBX, TAG_VISIBLE=TAG_VISIBLE,
        BIT_ACTIVE=BIT_ACTIVE, GETACTIVEOBJECTFLAGS_NONE=0, CTbase=CTbase,
        NBIT_OHIDE=NBIT_OHIDE_VALUE, NBITCONTROL_SET=NBITCONTROL_SET_VALUE,
        DRAWPASS_OBJECT=0, DRAWPASS_HIGHLIGHTS=3, DRAWRESULT_OK=1, DRAWRESULT_SKIP=2, NOCLIP_D=2048, NOCLIP_Z=4096,
        Ospline=Ospline, Tpointselection=Tpointselection, Tpolygonselection=Tpolygonselection,
        Tedgeselection=Tedgeselection, SPLINEOBJECT_TYPE=SPLINEOBJECT_TYPE,
        SPLINETYPE_LINEAR=SPLINETYPE_LINEAR, SPLINETYPE_BEZIER=SPLINETYPE_BEZIER,
        VECTOR_X=VECTOR_X, VECTOR_Y=VECTOR_Y, VECTOR_Z=VECTOR_Z,
        CINTERPOLATION_SPLINE=CINTERPOLATION_SPLINE, CINTERPOLATION_LINEAR=CINTERPOLATION_LINEAR,
        CINTERPOLATION_STEP=CINTERPOLATION_STEP,
        DESCFLAGS_GET_NONE=0, DESCFLAGS_SET_NONE=0, DESCFLAGS_DESC_NONE=0,
        CUSTOMDATATYPE_INEXCLUDE_LIST=CUSTOMDATATYPE_INEXCLUDE_LIST, MSG_DESCRIPTION_COMMAND=MSG_DESCRIPTION_COMMAND,
    )
    for key, value in names.items():
        setattr(c4d, key, value)
    _DIRTY_NAMES.clear()
    _DIRTY_NAMES[c4d.DIRTYFLAGS_DATA] = "DATA"
    _DIRTY_NAMES[c4d.DIRTYFLAGS_MATRIX] = "MATRIX"
    _DIRTY_NAMES[c4d.DIRTYFLAGS_CHILDREN] = "CHILDREN"
    modules = types.ModuleType(str("c4d.modules"))
    modules.character = types.ModuleType(str("c4d.modules.character"))
    modules.character.CAWeightTag = WeightTag
    modules.character.CAPoseMorphTag = PoseMorphTag
    c4d.modules = modules
    c4d.BaseDraw = FakeBaseDraw
    c4d.BaseList2D = Atom
    for cls in (Vector, BaseTime, CPolygon, DescLevel, DescID, BaseObject, PolygonObject, BaseTag, InExcludeData,
                BaseMaterial, BaseShader, WeightTag, PoseMorphTag,
                CTrack, CCurve, CKey, SplineObject, SelectionTag, UVWTag, BaseSelect, Matrix):
        setattr(c4d, cls.__name__, cls)
    state = Documents()
    documents_module.state = state
    documents_module.BaseDocument = BaseDocument
    for name in ("GetActiveDocument", "SetActiveDocument", "InsertBaseDocument", "KillDocument", "GetFirstDocument"):
        setattr(documents_module, name, getattr(state, name))
    c4d.documents = documents_module
    return state
