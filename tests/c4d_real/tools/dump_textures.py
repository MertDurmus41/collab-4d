# -*- coding: utf-8 -*-
"""Writes what the open document knows about the textures of the character meshes to %LOCALAPPDATA%\Temp\hp\tex_<version>.txt:
for every polygon object under "Auges" and "Mert": its texture tags (material, projection), its UVW tag, and for each
material the colour shader, its file and whether that file exists. R19 GUI: Script Manager > open > Execute. c4dpy: loads the
Fight_test11 scene (run it for the host side and compare the files)."""
import os
import traceback

import c4d

VERSION = c4d.GetC4DVersion()
OUT = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Temp", "hp", "tex_%s.txt" % VERSION)
FN = getattr(c4d, "Filename", str)   # R19: a class; 2023: plain text
SCENE = os.environ.get("C4D_SCENE", "")   # c4dpy only: the scene to load (set C4D_SCENE); the R19 GUI uses the open document


def walk(doc):
    stack = [doc.GetFirstObject()]
    while stack:
        obj = stack.pop()
        while obj is not None:
            yield obj
            if obj.GetDown() is not None:
                stack.append(obj.GetDown())
            obj = obj.GetNext()


def root_of(obj):
    while obj.GetUp() is not None:
        obj = obj.GetUp()
    return obj.GetName()


def shader_text(material, channel):
    shader = material[channel]
    if shader is None:
        return "-"
    if shader.GetType() == c4d.Xbitmap:
        name = shader[c4d.BITMAPSHADER_FILENAME]
        return "bitmap %r exists=%s" % (str(name), os.path.isfile(str(name)))
    return shader.GetTypeName()


def main():
    lines = []
    w = lines.append
    try:
        doc = c4d.documents.GetActiveDocument()
        if doc is None or "Auges" not in [o.GetName() for o in walk(doc)]:
            doc = c4d.documents.LoadDocument(SCENE, c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS)
            c4d.documents.InsertBaseDocument(doc)
        w("version %s doc %r path %r" % (VERSION, doc.GetDocumentName(), doc.GetDocumentPath()))
        names = []
        material = doc.GetFirstMaterial()
        while material is not None:
            names.append(material.GetName())
            material = material.GetNext()
        w("materials %d" % len(names))
        for obj in walk(doc):
            if obj.GetType() != c4d.Opolygon or root_of(obj) not in ("Auges", "Mert"):
                continue
            tags = []
            uvw = None
            tag = obj.GetFirstTag()
            while tag is not None:
                if tag.GetType() == c4d.Ttexture:
                    linked = tag[c4d.TEXTURETAG_MATERIAL]
                    tags.append("tex(%s proj=%s)" % (linked.GetName() if linked else None, tag[c4d.TEXTURETAG_PROJECTION]))
                elif tag.GetType() == c4d.Tuvw:
                    uvw = tag.GetDataCount()
                tag = tag.GetNext()
            w("%s | %s | points %d polys %d | uvw %s | %s" % (root_of(obj), obj.GetName(), obj.GetPointCount(), obj.GetPolygonCount(), uvw, ", ".join(tags)))
        w("-- materials named skin / eyes / Mat*")
        material = doc.GetFirstMaterial()
        while material is not None:
            if material.GetName() in ("skin", "Mat", "Mat.2", "Mat.3"):
                w("MAT %s type %s color-channel=%s use=%s colour=%s | %s" % (material.GetName(), material.GetType(), material[c4d.MATERIAL_USE_COLOR],
                                                                          material[c4d.MATERIAL_USE_COLOR], material[c4d.MATERIAL_COLOR_COLOR],
                                                                          shader_text(material, c4d.MATERIAL_COLOR_SHADER)))
            material = material.GetNext()
        w("-- where Cinema 4D itself finds the skin textures (GenerateTexturePath) and the global texture folders")
        try:
            w("global texture folders: %s" % [str(c4d.GetGlobalTexturePath(i)) for i in range(10) if str(c4d.GetGlobalTexturePath(i))])
        except Exception:
            w("global texture folders: %s" % traceback.format_exc().splitlines()[-1])
        material = doc.GetFirstMaterial()
        while material is not None:
            if material.GetName().startswith("skin"):
                shader = material[c4d.MATERIAL_COLOR_SHADER]
                found = None
                name = shader[c4d.BITMAPSHADER_FILENAME] if shader is not None else None
                try:
                    found = c4d.GenerateTexturePath(doc.GetDocumentPath(), FN(name), FN(), None)
                    w("%s: %r -> found %r exists=%s" % (material.GetName(), str(name), str(found) if found else None, bool(found) and os.path.isfile(str(found))))
                except Exception:
                    w("%s: GenerateTexturePath failed %s" % (material.GetName(), traceback.format_exc().splitlines()[-1]))
                try:
                    bitmap = c4d.bitmaps.BaseBitmap()
                    ok = bitmap.InitWith(str(found)) if found else None
                    w("   loaded by InitWith: %s size %s" % (ok, (bitmap.GetBw(), bitmap.GetBh()) if found else None))
                except Exception:
                    w("   InitWith failed %s" % traceback.format_exc().splitlines()[-1])
            material = material.GetNext()
        w("-- every material: use-reflection, layer count, colour shader")
        material = doc.GetFirstMaterial()
        while material is not None:
            try:
                count = material.GetReflectionLayerCount()
                detail = []
                for index in range(count):
                    layer = material.GetReflectionLayerIndex(index)
                    detail.append("%s/flags=%s" % (layer.GetName(), getattr(layer, "GetFlags", lambda: "?")()))
                w("M %s | refl=%s layers=%d %s | %s" % (material.GetName(), material[c4d.MATERIAL_USE_REFLECTION], count, detail, shader_text(material, c4d.MATERIAL_COLOR_SHADER)))
            except Exception:
                w("M %s | failed %s" % (material.GetName(), traceback.format_exc().splitlines()[-1]))
            material = material.GetNext()
    except Exception:
        w(traceback.format_exc())
    folder = os.path.dirname(OUT)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with open(OUT, "w") as handle:
        handle.write("\n".join(lines) + "\nDONE\n")
    print("dump_textures: wrote", OUT)


if __name__ == "__main__":
    main()
