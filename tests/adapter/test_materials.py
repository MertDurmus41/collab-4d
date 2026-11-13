# -*- coding: utf-8 -*-
"""Materials and shaders travel like objects: created, edited, relinked and deleted on both sides,
texture tags keep pointing at the right material, code-carrying shaders stay local.
Python 2.7 and 3.x."""
from __future__ import absolute_import, division, print_function, unicode_literals

import hashlib
import base64
import os
import shutil
import tempfile
import unittest
import zlib

import fake_scene as fs
from test_sync import SyncTestCase

TEXTURE = "C:/textures/wood.png"


def materials(doc):
    found = []
    material = doc.GetFirstMaterial()
    while material is not None:
        found.append(material)
        material = material.GetNext()
    return found


def shaders(owner):
    found = []
    shader = owner.GetFirstShader()
    while shader is not None:
        found.append(shader)
        shader = shader.GetNext()
    return found


def make_material(doc, name, color=(1, 0, 0), texture=None):
    material = fs.BaseMaterial(fs.Mmaterial)
    material.SetName(fs.native(name))
    material[fs.MATERIAL_COLOR_COLOR] = fs.Vector(*color)
    if texture:
        bitmap = fs.BaseShader(fs.Xbitmap)
        bitmap.SetName(fs.native("Bitmap"))
        bitmap[fs.BITMAP_FILENAME] = str(texture)
        material.InsertShader(bitmap)
        material[fs.MATERIAL_COLOR_SHADER] = bitmap
    doc.InsertMaterial(material)
    return material


class MaterialSyncTest(SyncTestCase):
    def scene(self):
        doc = fs.BaseDocument()
        plane = fs.BaseObject(fs.Ocube)
        plane.SetName(fs.native("Plane"))
        doc.InsertObject(plane)
        wood = make_material(doc, "Wood", (0.5, 0.3, 0.1), TEXTURE)
        layer = fs.BaseShader(fs.Xlayer)
        layer.SetName(fs.native("Layer"))
        wood.InsertShader(layer, wood.GetFirstShader())
        inner = fs.BaseShader(fs.Xbitmap)
        inner.SetName(fs.native("Inner"))
        inner[fs.BITMAP_FILENAME] = str("C:/textures/inner.png")
        layer.InsertShader(inner)
        code = fs.BaseShader(fs.Xpython_shader)
        code.SetName(fs.native("Evil"))
        code[1000] = str("import os")
        wood.InsertShader(code, layer)
        make_material(doc, "Metal", (0.8, 0.8, 0.9))
        tag = plane.MakeTag(fs.Ttexture)
        tag.SetName(fs.native("Texture"))
        tag[fs.TEXTURETAG_MATERIAL] = wood
        return doc

    def test_guest_receives_materials_shaders_and_links(self):
        self.host(self.scene())
        guest = self.join()
        names = [m.GetName() for m in materials(guest.doc)]
        self.assertEqual(sorted(names), ["Metal", "Wood"])
        wood = [m for m in materials(guest.doc) if m.GetName() == "Wood"][0]
        self.assertEqual(wood[fs.MATERIAL_COLOR_COLOR], fs.Vector(0.5, 0.3, 0.1))
        top = shaders(wood)
        self.assertEqual([s.GetName() for s in top], ["Bitmap", "Layer"], "the Python shader stayed local")
        self.assertEqual(top[0][fs.BITMAP_FILENAME], TEXTURE)
        self.assertIs(wood[fs.MATERIAL_COLOR_SHADER], top[0], "the colour channel points at the guest's own shader")
        self.assertEqual([s.GetName() for s in shaders(top[1])], ["Inner"], "nested shaders arrive too")
        plane = self.find(guest.doc, "Plane")
        self.assertIs(plane.GetFirstTag()[fs.TEXTURETAG_MATERIAL], wood, "the texture tag uses the guest's material")
        self.assert_no_echo()

    def test_material_order_is_kept(self):
        host = self.host(self.scene())
        guest = self.join()
        self.assertEqual([m.GetName() for m in materials(guest.doc)], [m.GetName() for m in materials(host.doc)])

    def test_edits_travel_both_ways(self):
        a = self.host(self.scene())
        b = self.join()
        wood_a = [m for m in materials(a.doc) if m.GetName() == "Wood"][0]
        wood_b = [m for m in materials(b.doc) if m.GetName() == "Wood"][0]
        self.edit(a, lambda: wood_a.SetParameter(fs.DescID(fs.MATERIAL_COLOR_COLOR), fs.Vector(0, 1, 0), 0))
        self.assertEqual(wood_b[fs.MATERIAL_COLOR_COLOR], fs.Vector(0, 1, 0))
        bitmap_b = shaders(wood_b)[0]
        self.edit(a, lambda: shaders(wood_a)[0].SetParameter(fs.DescID(fs.BITMAP_FILENAME), str("C:/textures/new.png"), 0))
        self.assertEqual(bitmap_b[fs.BITMAP_FILENAME], "C:/textures/new.png")
        self.edit(b, lambda: wood_b.SetName(fs.native("Oak")))
        self.assertEqual(wood_a.GetName(), "Oak")
        self.assert_no_echo()

    def test_new_and_deleted_materials(self):
        a = self.host(self.scene())
        b = self.join()
        self.edit(b, lambda: make_material(b.doc, "Glass", (0.9, 1, 1), "C:/textures/glass.png"))
        glass = [m for m in materials(a.doc) if m.GetName() == "Glass"]
        self.assertEqual(len(glass), 1)
        self.assertIs(glass[0][fs.MATERIAL_COLOR_SHADER], shaders(glass[0])[0], "a material created later is linked too")
        self.edit(a, lambda: glass[0].Remove())
        self.assertEqual(sorted(m.GetName() for m in materials(b.doc)), ["Metal", "Wood"])
        self.assert_no_echo()

    def test_a_new_shader_in_an_existing_material(self):
        a = self.host(self.scene())
        b = self.join()
        metal_a = [m for m in materials(a.doc) if m.GetName() == "Metal"][0]

        def add():
            bitmap = fs.BaseShader(fs.Xbitmap)
            bitmap.SetName(fs.native("Late"))
            bitmap[fs.BITMAP_FILENAME] = str("C:/textures/late.png")
            metal_a.InsertShader(bitmap)
            metal_a[fs.MATERIAL_COLOR_SHADER] = bitmap
        self.edit(a, add)
        metal_b = [m for m in materials(b.doc) if m.GetName() == "Metal"][0]
        self.assertEqual([s.GetName() for s in shaders(metal_b)], ["Late"])
        self.assertIs(metal_b[fs.MATERIAL_COLOR_SHADER], shaders(metal_b)[0])

    def test_code_shaders_are_never_created_from_the_network(self):
        a = self.host(self.scene())
        b = self.join()
        server = a.session.server.scene
        wood = [n for n in server.nodes.values() if n.kind == "material" and n.name == "Wood"][0]
        a.session.send_ops([{"op": "create", "uuid": "f" * 32, "kind": "shader", "ctype": fs.Xpython_shader,
                             "parent": wood.uuid, "prev": None, "name": "Injected", "data": {"p": {}}}])
        self.settle()
        wood_b = [m for m in materials(b.doc) if m.GetName() == "Wood"][0]
        self.assertNotIn("Injected", [s.GetName() for s in shaders(wood_b)])


class ReflectanceLayerTest(SyncTestCase):
    """A material must look the same on every side: no extra specular layer on materials that have none,
    and the layers (with their settings) of the others arrive whatever ids each Cinema 4D gave them."""

    STRENGTH, COLOR = 16, 32   # offsets inside a layer's block of ids (fake_scene.BaseMaterial)

    def scene(self):
        doc = fs.BaseDocument()
        matte = make_material(doc, "Matte", (0.2, 0.6, 0.2))
        matte.RemoveReflectionAllLayers()
        shiny = make_material(doc, "Shiny", (0.9, 0.9, 0.9))
        shiny.AddReflectionLayer()
        shiny.AddReflectionLayer()
        shiny.RemoveReflectionLayerIndex(1)   # ids are no longer a clean run: [top, bottom]
        for index, value in enumerate((0.25, 0.75)):
            layer = shiny.GetReflectionLayerIndex(index)
            shiny.SetParameter(fs.DescID(layer.GetDataID() + self.STRENGTH), value, 0)
            shiny.SetParameter(fs.DescID(layer.GetDataID() + self.COLOR), fs.Vector(value, 0, 1 - value), 0)
            layer.SetName(fs.native("Layer %d" % index))
        shiny.GetReflectionLayerIndex(1).SetFlags(3)
        return doc

    def named(self, instance, name):
        return [m for m in materials(instance.doc) if m.GetName() == name][0]

    def layers_of(self, material):
        """What a user would see: per layer (name, flags, strength, colour) from top to bottom."""
        found = []
        for index in range(material.GetReflectionLayerCount()):
            layer = material.GetReflectionLayerIndex(index)
            found.append((layer.GetName(), layer.GetFlags(),
                          material.GetParameter(fs.DescID(layer.GetDataID() + self.STRENGTH), 0),
                          material.GetParameter(fs.DescID(layer.GetDataID() + self.COLOR), 0).x))
        return found

    def test_a_material_without_layers_stays_without(self):
        self.host(self.scene())
        guest = self.join()
        self.assertEqual(self.named(guest, "Matte").GetReflectionLayerCount(), 0,
                         "the default layer of a new material must not appear on the other side")
        self.assert_no_echo()

    def test_layers_arrive_with_their_settings_under_the_guests_own_ids(self):
        host = self.host(self.scene())
        guest = self.join()
        shiny_a, shiny_b = self.named(host, "Shiny"), self.named(guest, "Shiny")
        self.assertNotEqual(
            [shiny_a.GetReflectionLayerIndex(i).GetDataID() for i in range(2)],
            [shiny_b.GetReflectionLayerIndex(i).GetDataID() for i in range(2)],
            "the two sides really use different layer ids in this test")
        self.assertEqual(self.layers_of(shiny_b), self.layers_of(shiny_a))
        self.assertEqual(self.layers_of(shiny_b), [("Layer 0", 7, 0.25, 0.25), ("Layer 1", 3, 0.75, 0.75)])
        self.assert_no_echo()

    def test_a_layer_setting_travels_both_ways(self):
        host = self.host(self.scene())
        guest = self.join()
        shiny_a, shiny_b = self.named(host, "Shiny"), self.named(guest, "Shiny")
        bottom_a = shiny_a.GetReflectionLayerIndex(1).GetDataID()
        self.edit(host, lambda: shiny_a.SetParameter(fs.DescID(bottom_a + self.STRENGTH), 0.5, 0))
        self.assertEqual(self.layers_of(shiny_b)[1][2], 0.5)
        self.assertEqual(self.layers_of(shiny_b)[0][2], 0.25, "the other layer is untouched")
        top_b = shiny_b.GetReflectionLayerIndex(0).GetDataID()
        self.edit(guest, lambda: shiny_b.SetParameter(fs.DescID(top_b + self.STRENGTH), 0.9, 0))
        self.assertEqual(self.layers_of(shiny_a)[0][2], 0.9)
        self.assert_no_echo()

    def test_adding_and_removing_layers_travels(self):
        host = self.host(self.scene())
        guest = self.join()
        shiny_a, shiny_b = self.named(host, "Shiny"), self.named(guest, "Shiny")
        self.edit(host, lambda: shiny_a.AddReflectionLayer())
        self.assertEqual(self.layers_of(shiny_b), self.layers_of(shiny_a))
        self.assertEqual(shiny_b.GetReflectionLayerCount(), 3)
        self.edit(guest, lambda: shiny_b.RemoveReflectionLayerIndex(0))
        self.assertEqual(self.layers_of(shiny_a), self.layers_of(shiny_b))
        self.assertEqual(shiny_a.GetReflectionLayerCount(), 2)
        matte_a = self.named(host, "Matte")
        self.edit(host, lambda: matte_a.AddReflectionLayer())
        self.assertEqual(self.named(guest, "Matte").GetReflectionLayerCount(), 1)
        self.edit(host, lambda: shiny_a.RemoveReflectionAllLayers())
        self.assertEqual(shiny_b.GetReflectionLayerCount(), 0)
        self.assert_no_echo()

    def test_a_new_material_keeps_its_layer_count_on_the_other_side(self):
        host = self.host(self.scene())
        guest = self.join()
        self.edit(guest, lambda: make_material(guest.doc, "Fresh", (1, 1, 1)))      # born with one layer
        self.assertEqual(self.named(host, "Fresh").GetReflectionLayerCount(), 1)

        def bare():
            material = make_material(guest.doc, "Bare", (1, 1, 1))
            material.RemoveReflectionAllLayers()
        self.edit(guest, bare)
        self.assertEqual(self.named(host, "Bare").GetReflectionLayerCount(), 0)
        self.assert_no_echo()

    def test_keys_by_layer_index_round_trip(self):
        from c4d_collab_lib.c4d_adapter.serializers import layers
        ids = [527872, 526848, 526336]
        expected = {527872: 526336, 527872 + 16: 526336 + 16, 526848 + 511: 526848 + 511, 526336 + 32: 526336 + 1024 + 32}
        for first, canonical_first in expected.items():
            key = "%d:15:5703" % first
            canonical = layers.to_canonical(key, ids)
            self.assertEqual(canonical, "%d:15:5703" % canonical_first)
            self.assertEqual(layers.to_local(canonical, ids), key)
        self.assertEqual(layers.to_canonical("2000:400006001:5703", ids), "2000:400006001:5703", "not a layer key")
        self.assertEqual(layers.to_canonical("527888:15:5703/3:3:3", ids), "526352:15:5703/3:3:3", "deeper levels stay")
        self.assertEqual(layers.to_local("1041670:3:110050", ids), "1041670:3:110050")


class TextureFileTest(SyncTestCase):
    """Texture files travel as assets: a guest that does not have the file gets a cached copy."""

    def setUp(self):
        SyncTestCase.setUp(self)
        from c4d_collab_lib.c4d_adapter.serializers import assets
        self.assets = assets
        self.folder = tempfile.mkdtemp()
        self.cache = tempfile.mkdtemp()
        self._cache_dir = assets.cache_dir
        assets.cache_dir = lambda: self.cache
        self.payload = b"PNG fake image bytes " * 50
        self.path = os.path.join(self.folder, "wood.png")
        with open(self.path, "wb") as handle:
            handle.write(self.payload)

    def tearDown(self):
        self.assets.cache_dir = self._cache_dir
        SyncTestCase.tearDown(self)
        shutil.rmtree(self.folder, ignore_errors=True)
        shutil.rmtree(self.cache, ignore_errors=True)

    def scene(self):
        doc = fs.BaseDocument()
        make_material(doc, "Wood", (1, 1, 1), self.path)
        return doc

    def bitmap_of(self, instance):
        return shaders(materials(instance.doc)[0])[0]

    def test_a_guest_without_the_file_gets_a_cached_copy(self):
        host = self.host(self.scene())
        os.remove(self.path)  # the guest is "another computer": this path does not exist for it
        guest = self.join()
        cached = self.bitmap_of(guest)[fs.BITMAP_FILENAME]
        self.assertNotEqual(cached, self.path)
        self.assertTrue(cached.startswith(self.cache))
        with open(cached, "rb") as handle:
            self.assertEqual(handle.read(), self.payload)
        self.assert_no_echo()
        # the redirect is local: the host still sees its own path, and a guest edit does not leak the cache path
        self.edit(guest, lambda: materials(guest.doc)[0].SetParameter(
            fs.DescID(fs.MATERIAL_COLOR_COLOR), fs.Vector(0, 0, 1), 0))
        self.assertEqual(self.bitmap_of(host)[fs.BITMAP_FILENAME], self.path)
        self.assertEqual(self.bitmap_of(guest)[fs.BITMAP_FILENAME], cached)
        self.assert_no_echo()

    def test_a_bare_name_on_a_guest_is_found_whatever_the_search_paths(self):
        """Cinema 4D shortens a texture path to its bare name now and then (after a click). A guest's document without a
        folder found Mert's skin only through a search path (it lies in ten projects) and not Auges', whose alpha then
        made the body transparent. The guest's document has a folder that holds every received texture by name."""
        self.host(self.scene())
        os.remove(self.path)
        guest = self.join()
        folder = guest.doc.GetDocumentPath()
        self.assertTrue(folder, "the session document has a folder")
        with open(os.path.join(folder, "tex", "wood.png"), "rb") as handle:
            self.assertEqual(handle.read(), self.payload)
        bitmap = self.bitmap_of(guest)
        bitmap.SetParameter(fs.DescID(fs.BITMAP_FILENAME), "wood.png", 0)   # what Cinema 4D left of the path
        elsewhere = tempfile.mkdtemp()   # another project that a texture search path reaches: must not matter
        self.addCleanup(shutil.rmtree, elsewhere, True)
        with open(os.path.join(elsewhere, "wood.png"), "wb") as handle:
            handle.write(b"another picture")
        import c4d
        saved = getattr(c4d, "GenerateTexturePath", None)
        c4d.GenerateTexturePath = lambda *args: os.path.join(elsewhere, "wood.png")
        self.addCleanup(lambda: setattr(c4d, "GenerateTexturePath", saved) if saved is not None
                        else delattr(c4d, "GenerateTexturePath"))
        guest.sync._textures_checked = 0.0
        guest.sync._repair_textures()
        held = self.bitmap_of(guest)[fs.BITMAP_FILENAME]
        resolved = held if os.path.isabs(held) else os.path.join(folder, "tex", held)
        with open(resolved, "rb") as handle:
            self.assertEqual(handle.read(), self.payload, "the name resolves to the host's texture")
        self.assert_no_echo()

    def test_another_file_of_the_same_name_in_the_session_folder_is_not_used(self):
        self.host(self.scene())
        os.remove(self.path)
        guest = self.join()
        folder = guest.doc.GetDocumentPath()
        with open(os.path.join(folder, "tex", "wood.png"), "wb") as handle:
            handle.write(b"a different wood.png from an earlier session")
        self.bitmap_of(guest).SetParameter(fs.DescID(fs.BITMAP_FILENAME), "wood.png", 0)
        guest.sync._textures_checked = 0.0
        guest.sync._repair_textures()
        self.assertTrue(self.bitmap_of(guest)[fs.BITMAP_FILENAME].startswith(self.cache))

    def test_a_guest_that_has_the_file_keeps_its_own_path(self):
        self.host(self.scene())
        guest = self.join()
        self.assertEqual(self.bitmap_of(guest)[fs.BITMAP_FILENAME], self.path)

    def test_the_asset_is_content_addressed_and_sent_once(self):
        a = self.host(self.scene())
        nodes = [n for n in a.session.server.scene.nodes.values() if n.kind == "asset"]
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].uuid, hashlib.sha1(self.payload).hexdigest())
        self.assertEqual(nodes[0].name, "wood.png")
        # a second material using the same file adds no second asset
        self.edit(a, lambda: make_material(a.doc, "Wood2", (1, 1, 1), self.path))
        self.assertEqual(len([n for n in a.session.server.scene.nodes.values() if n.kind == "asset"]), 1)

    def test_a_new_texture_chosen_later_travels(self):
        a = self.host(self.scene())
        other = os.path.join(self.folder, "stone.jpg")
        with open(other, "wb") as handle:
            handle.write(b"jpeg bytes" * 40)
        b = self.join()
        self.edit(a, lambda: self.bitmap_of(a).SetParameter(fs.DescID(fs.BITMAP_FILENAME), str(other), 0))
        os.remove(other)
        c = self.join("Third")
        self.assertTrue(os.path.isfile(self.bitmap_of(c)[fs.BITMAP_FILENAME]) or os.path.isfile(other))

    def test_unacceptable_assets_are_refused(self):
        store = self.assets.store
        good = base64.b64encode(zlib.compress(self.payload)).decode("ascii")
        sha = hashlib.sha1(self.payload).hexdigest()
        self.assertIsNotNone(store(sha, "ok.png", good))
        self.assertIsNone(store(sha, "run.exe", good), "only image types")
        self.assertIsNone(store("0" * 40, "wrong.png", good), "content must match its id")
        self.assertIsNone(store("nothex", "x.png", good))
        self.assertIsNone(store(sha, "x2.png", "!!!not base64"))
        bomb = base64.b64encode(zlib.compress(b"0" * (self.assets.MAX_ASSET_BYTES + 10))).decode("ascii")
        self.assertIsNone(store(hashlib.sha1(b"0" * (self.assets.MAX_ASSET_BYTES + 10)).hexdigest(), "big.png", bomb))
        path = store(sha, "../../evil.png", good)
        self.assertTrue(os.path.dirname(os.path.abspath(path)) == os.path.abspath(self.cache), "stays in the cache folder")

    def test_files_that_are_too_big_or_not_images_are_not_described(self):
        script = os.path.join(self.folder, "payload.py")
        with open(script, "wb") as handle:
            handle.write(b"print(1)")
        self.assertIsNone(self.assets.describe(script))
        self.assertIsNone(self.assets.describe("relative/wood.png"))
        self.assertIsNone(self.assets.describe(os.path.join(self.folder, "missing.png")))


if __name__ == "__main__":
    unittest.main()
