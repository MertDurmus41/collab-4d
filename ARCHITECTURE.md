# Collab 4D: Architecture

This document is the **constitution** of the project. Before writing new code, check that it follows what is written here. If the architecture changes, update this document first.

> **About this document.** The test files, research notes and the original, longer Turkish version of this document (with the full measurement diary) live on the [`dev` branch](https://github.com/MertDurmus41/collab-4d/tree/dev). File paths such as `tests/...` below refer to that branch.
## 1. Goal
Let several people do real-time animation and modeling in the same Cinema 4D scene.
- Free, MIT licensed.
- Supports Cinema 4D **R17 (2016) to 2026+**.
- The server runs **inside the project owner's (host's) Cinema 4D**. A standalone Python/Docker server can be used instead.

## 2. Layers

```
┌──────────────────────── Cinema 4D (every user) ──────────────────────────────┐
│  c4d_collab.pyp  (entry point, plugin registrations)                         │
│  ┌── UI (depends on c4d) ─────────────────────────────────────────────────┐ │
│  │ CollabDialog: Host/Join, users, status, log                            │ │
│  │ PresenceDrawer: other users' lock colours, names, cameras              │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│  ┌── Adapter: c4d_collab_lib/c4d_adapter (depends on c4d) ────────────────┐ │
│  │ plugin_ids · compat · bridge · ui_dialog                               │ │
│  │ (Stage 3+: ids · tracker · serializers/* · applier · reconcile)       │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│  ┌── Core: c4d_collab_lib/core (pure Python, NEVER imports c4d) ──────────┐ │
│  │ protocol · net · model · diff · locks · client · mirror · session ·   │ │
│  │ server · compat · log                                                 │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘
        ▲  TCP: [4 byte length][1 byte flags][UTF-8 JSON (zlib optional)]
        ▼
  core.server inside the host's C4D  ── or ──  server/run_server.py / Docker
```

### Fixed rules
1. `core/` **never** imports `c4d`. That way it can be tested without C4D, the same server runs anywhere, and supporting Maya or Mine-imator later only needs a new adapter.
2. All plugin code runs on **Python 2.7 and 3.x**: no f-strings, type hints, `async`, `nonlocal`, `pathlib` or `selectors`. Every file starts with `from __future__ import absolute_import, division, print_function, unicode_literals`. Files with non-ASCII characters carry `# -*- coding: utf-8 -*-` on the first line, otherwise Python 2.7 will not open the file.
3. **Standard library only** (no pip, no `pickle`/`eval`).
4. The scene is changed **only from the main thread**. The network thread only writes to a queue and wakes the main thread with `SpecialEventAdd`.
5. Remote changes are not written to the undo history.
6. Every C4D API that depends on the version is called through `c4d_adapter/compat.py`.

## 3. Data model (`core/model.py`)
- **Node:** `uuid, kind (object|tag|track|doc), ctype, parent, prev, name, data`
  - The hierarchy belongs to the child: `parent` (the parent's uuid, or `null` = root) and `prev` (the previous sibling's uuid, or `null` = first). The `parent` of a tag or track is the object that owns it.
- **Op (operation):** `{"op": "create"|"update"|"delete"|"move", "uuid": ..., ...}`
  - `create`: `kind, ctype, parent, prev, name, data`
  - `update`: `patch` (see `core/diff.py`), and `name` if needed
  - `move`: `parent, prev`
  - `delete`: deletes together with the child nodes
- **Order:** The server gives every Op it accepts an increasing `seq`, and everyone applies Ops in that order.

## 4. Protocol (`core/protocol.py`)
Frame: `>I` length + `B` flags (bit0 = zlib) + JSON. Maximum message size is 64 MB.

| Direction | Message | Fields |
|---|---|---|
| S→C | `challenge` | `nonce, session, auth` |
| C→S | `hello` | `name, proof, client{app, app_version, python, plugin}` |
| S→C | `welcome` | `user_id, users, locks, seq` |
| S→C | `snapshot_begin` / `snapshot_chunk` / `snapshot_end` | `count` / `ops` / `seq` |
| C→S | `ops` | `cid, ops[]` |
| S→C | `ops` | `author, ops[]` (every op has a `seq`) |
| S→C | `ack` | `cid, seq, accepted[index], rejected[{i, uuid, reason}]` |
| C→S | `lock` / `release` | `uuids[]` (`release` can also have `all: true`) |
| S→C | `locks` | `changes{uuid: user_id \| null}` |
| S→C | `lock_denied` | `uuids[]` |
| C→S / S→C | `presence` | `data{frame, selection, camera}` / `user, data` |
| S→C | `users` | `users[]` |
| ↔ | `ping` / `pong` | `ts` |
| ↔ | `bye` | `reason` |
| S→C | `error` | `code, message` |

Every message has a `v` (protocol version) field. Authentication: `proof = HMAC-SHA256(password, nonce)`. The password is never sent over the network.

Large number arrays are carried with `protocol.pack_floats` / `pack_ints` (little-endian `struct` + base64).

## 5. Locks (`core/locks.py`)
- Lock on select: the client sends a `lock` message for the objects it selects. A lock also covers all child nodes (child objects, tags, tracks).
- Permission to edit a node: the node is unlocked **or** the lock is yours. Locks on the ancestor chain apply too.
- The server rejects unauthorized Ops. On the client side the `reconcile` module puts a local change back to the correct state held in the cache.
- When a user's connection drops, the server releases all of their locks.

## 6. Session
- Host: starts `core.server` in their own C4D and connects to **their own server like a normal client** over `127.0.0.1`. Their scene is uploaded as `create` Ops (the seed). There is a single code path, so there is no special case.
- Join: receives `welcome` + a snapshot and builds the scene **in a new document**.
- If the host's C4D closes, the session ends.

### Client side (`core/client.py`, `core/mirror.py`)
- `CollabClient`: runs the socket in a background thread. The main thread sends messages with `send_ops / lock / release / presence` and receives incoming messages with `poll()`. `notify` is called when a new message arrives (in C4D this becomes `SpecialEventAdd`).
- `SessionMirror`: a copy of the state the server has **confirmed**: snapshot + other users' Ops + our own acknowledged (ack) Ops. The adapter finds local changes by comparing the live scene with it. Rejected edits are also rolled back according to this copy.
- The server sends all messages over a single socket in `seq` order, so every client's copy is updated in the same order.

## 6b. Security
A session means sending data over the network to someone else's computer. The rules:

| Threat | Measure | Where | Test |
|---|---|---|---|
| Remote code execution | No `pickle`/`eval`, JSON only. C4D items that carry Python code (Python tag/generator/effector/field, XPresso Python node) are **not synchronized by default** (Stage 3+) | protocol, serializers | test_protocol |
| Password leak | The password is not sent over the network (HMAC-SHA256 challenge). It is not written to disk or the log, and it is cleared from the panel after use | protocol, ui_dialog | test_protocol, test_adapter |
| Session without a password | Hosting requires a password of at least 4 characters | core/session | test_controller |
| Password guessing | 5 wrong logins from one IP within 60 s block that IP for 60 s | server | test_security |
| Memory exhaustion | At most a 64 KB message from a connection that has not logged in, 64 MB after login. The unpacked size of compressed data is limited too (zip bomb) | protocol, server | test_security, test_protocol |
| Message flood | 1000 messages per second per connection (3000 for a short burst) | server | test_security |
| Client that does not read | The connection is cut if pending output exceeds 128 MB | server | test_security |
| Corrupt or malicious data | Every Op and message field is validated. A bad Op is rejected on its own and the server does not crash | model, server | test_model, test_security |
| Breaking someone else's work | The lock system, with the permission check on the server | locks, server | test_session |
| UI/log injection | Control characters are removed from user names, and names are limited to 64 characters | server | test_security |
| Connection privacy | Traffic is **not encrypted**. For use over the internet, Tailscale/ZeroTier (an encrypted VPN) is recommended. Optional TLS will be considered in Stage 8 | docs | none |
| Unwanted access on the local network | When Windows Firewall asks, choosing only "Private networks" is recommended | docs | manual |

## 7. Adapter (C4D): scene synchronization (Stage 3)

```
 local edit ──► tracker.scan() ──► Ops ──► session.send_ops() ──► server
                    │  (applied to the shadow right away)
 server ──► mirror ──► "remote_ops" ──► sync.apply_remote():
                                         1) first tracker.scan() (send our own changes)
                                         2) applier.apply() → shadow + C4D scene
                                         3) dirty values of the touched atoms + the document baseline are saved (no echo)
```

- `ids.Registry`: uuid ↔ live atom. The identity is stored with `AddUniqueID(UNIQUE_ID_APP, 16 bytes)`. If the same uuid is seen twice in one scan, the second one gets a new uuid.
- `tracker.Tracker`: does nothing if the document counters have not changed. If they have, it walks the object tree: the structure (`parent/prev`) is compared with the shadow, and only atoms whose own `DATA/MATRIX` counter changed are serialized. If only `MATRIX` changed, only the transform parameters are read (a fast path while dragging). Op order: create/move/update in tree order (parent first), deletes last.
- `applier.Applier`: applies to the shadow first (validation), then to the scene. If an object's data cannot be loaded (corrupt geometry and so on), the object is still created and the error is written to the log.
- `sync.SceneSync`: on the host, when the snapshot arrives the active document becomes the session document and the whole scene is sent (seed). On a guest the snapshot is built **in a new document**. **What happens when the document changes** (`SceneSync._usable`, `SWITCH_GRACE` = 10 s): the documents that were open when the session started are remembered. (1) **If a new project is opened** (File > Open/New: a document that did not exist becomes active), the user **leaves the session immediately**. For a guest only their own connection closes and the others drop from the user list; **for the host the session ends for everyone** (the server runs in the host's Cinema 4D) and the guests are told. The reason is written to the panel log ("You left the session: you opened another project" / "The session ended: ..."). (2) If the user **switches to another tab that was already open before the session started**, synchronization stops ("paused", and the user leaves if they do not come back within 10 s). When they come back it resumes where it left off, so an accidental tab switch does not kill the session. (3) If the **session document is closed**, the user leaves. Rejoining after leaving is possible (a new document is built).
- `bridge`: a MessageData plugin. It is triggered by `SpecialEventAdd`, a 20 ms timer (two timers run at least 12 ms apart) and `EVMSG_CHANGE`. While the panel is open, scanning also happens while dragging, through `EVMSG_ASYNCEDITORMOVE`.

### Node data
| kind | data |
|---|---|
| `object` | `{"p": {param key: value}, "g": {...}}` (`g` only on polygon objects) |
| `tag` | `{"p": {...}}` |

- **Parameter key:** the whole DescID, in the form `"id:dtype:creator/id:dtype:creator"` (user data needs two levels).
- **Values:** numbers/bools/strings as they are; `{"V": [x,y,z]}` vector/colour; `{"T": [numerator, denominator]}` BaseTime; `{"L": uuid}` a link to another synchronized object.
- **Supported types:** Real, Long, Bool, String, Filename, Vector, Color, Time, Link. Others (spline, gradient, in/exclude...) are not sent for now.
- **Excluded:** the name (sent separately), layer link, `ABS_*`/`GLOBAL_*` (derived values).
- **Parameters are never deleted:** a patch never sends `del` for a parameter. Different C4D versions can have different parameters, and an older version must not delete a newer version's parameters.
- **Geometry (`g`):** `{"n": [points, polygons], "pts": pack_floats, "polys": pack_ints}`. The receiver validates index ranges and sizes. In Stage 3 the whole geometry is sent on every change; in Stage 6 only the changed points will be sent.

### Type policy (`serializers/nodes.py`)
| Group | Types | Behaviour |
|---|---|---|
| Carries code | Python generator/effector/field/tag, COFFEE, XPresso **with a Python/COFFEE node** | **Never sent, and never created if it arrives over the network.** (An XPresso graph made of standard nodes counts as data and is sent, see "XPresso graphs".) The plugin's own "Collab rig copy" Python tag stays on this computer only and, by this rule, is never sent. In place of an incoming object an empty Null is created. Detection has two layers: numeric IDs (R19 has no constants like `c4d.Opython`!) **and** the type name containing "Python/COFFEE/XPresso" |
| Tags not supported yet | Vertex map, normal and hidden-data tags (UVW and selection were added in Stage 6, Weight and Pose Morph in Stage 8) | Not sent |
| Type missing in this version | An object from a newer C4D, an object of a plugin that is not installed | A Null is put in its place so the hierarchy does not break. The data of this Null is **never sent back** and never overwrites the original |
## 7a. Locks and reconcile (Stage 4)

```
selection changed ──► sync._sync_selection(): newly selected → LOCK, released → RELEASE,
                  presence.selection is updated (session.set_presence merges the fields)
someone else's lock ──► selection is undone (DelBit) + status bar/log: "being edited by X"

tracker.scan():   a change on a locked node → NOT SENT
                    • parameter/name changed, parent changed, deleted → tracker.restore
                    • new object/tag under a locked parent → tracker.discard
                    • only the order changed → ignored (also happens when a sibling moves)
                  if the hierarchy changed, a quick ID scan runs first: if there is an
                  unauthorized delete it is restored first, then the comparison is made
                  (an order Op with side effects is not sent)
applier.restore(): put it back to the state in the shadow (if deleted, recreate with its subtree)

server rejected (race) ──► "rejected" ──► applier.reconcile(mirror.scene, uuid):
                  shadow + scene are brought to the state the server confirmed (mirror)
lock_denied / someone else took the lock ──► selection is released
```

- **The permission rules are the same as on the server:** to edit, neither the node nor any of its ancestors may be locked by someone else (`mirror.editable`). To delete or move, no child node may be locked by someone else either (`mirror.subtree_editable`).
- **Ctrl+Z:** undoing someone else's locked object is also a "local change": it is detected, not sent, and restored. No SceneHook (C++ only) is needed.
- **Visibility:** The panel has a "Being edited by others" list. Coloured display in the viewport arrives in Stage 7.

## 7c. Animation (Stage 5)

| Node | uuid | data |
|---|---|---|
| `track` (parent = the animated object/tag) | `md5(owner uuid \| parameter key)`: everyone computes the same ID, so no ID has to be written into the track | `{"d": parameter key, "p": track settings (before/after loop, offset...), "k": {"<seconds>": key}}` |
| `doc` | `"document"` | `{"fps", "min", "max", "lmin", "lmax"}` (seconds) |

- **Key:** `{"v": value, "i": interpolation, "tl"/"tr": tangent times, "vl"/"vr": tangent values, "q": {option id: value}}`. The options (auto tangent, clamp, break, mute, weighted...) are read directly by ID; walking the description for every key would be slow.
- Keys are keyed **by time**, so a patch only carries the keys that changed. On the apply side the curve is rebuilt from scratch (`FlushKeys` + `AddKey`), because that is simple and exact and curves are small.
- **Only value tracks (`CTbase` = 5350)** are synchronized. PLA, sound and morph tracks were left for after v1.
- **Change detection:** when the document's `ANIMATION` counter (`ALL` on R19) changes, the tracks are scanned. According to measurements **tangent and track-setting changes do not increase any counter**, so a content comparison also runs every `TRACK_SCAN_INTERVAL` (1 s). To avoid an echo, the comparison is made not against the shadow but against a cache of the "last seen/applied live content".
- **Animated parameters are not sent as plain values.** Otherwise an update would go out on every frame during playback. If only the X of a vector is animated, X is sent as `null` (the receiver keeps its own value) and Y/Z synchronize normally.
- **The current frame (playhead) belongs to each person.** It is not synchronized, only shared as `presence.frame` (at most 5 times a second). It shows in the panel as "frame N".
- **Locks apply to tracks too:** the parent of a track is the object. Changing the keys of a locked object is rolled back, and adding a new track to it is removed.

## 7d. Modeling (Stage 6)

Geometry and tag contents travel **split into chunks**: every 128 points/polygons are packed separately, and the generic `diff` only sends the chunks that changed. Moving a single point of a model with 100,000 points sends one chunk of about 4 KB (test: `test_modeling.py`).

| Data | Where | Format |
|---|---|---|
| Polygon object | `object.data["g"]` | `{"n": [points, polygons], "pts": {chunk: float x3}, "polys": {chunk: int x4}}` |
| Spline | `object.data["g"]` | `{"n": [points, segments], "pts": {...}, "seg": int (count, closed)*, "tan": {chunk: float x6}}` (tangents only if present) |
| Selection tag (point/polygon/edge) | `tag.data["s"]` | `{"n": element count, "r": int (first, last)* closed ranges}` |
| UVW tag | `tag.data["uv"]` | `{"n": polygon count, "uv": {chunk: float x8}}` (u,v values of corners a-d) |

- **Full load if the count changed, partial if not:** if `n` is the same, only the changed chunks are written (`SetPoint`/`SetSlow`). If `n` changed (extrude, cut...), the object is rebuilt from scratch with `ResizeObject`.
- **Dependent tags:** when the topology changes, C4D resizes the UVW tag itself. On the receiving side, after the object is loaded, the selection and UVW tags are re-applied from the shadow (`Applier._reload_dependents`). On the sending side, when the owner's element count changes, **the UVW and selection tags are re-read in that same scan**, so a topology change goes out as a single packet.
- **Content that does not move a counter:** selection-tag edits, spline tangent and segment flags (measured on 2023), and **UVW edits** (on 2023 the counter goes up, but on R19 a UV edit did not reach the other side, so it is put on the periodic check in every version; if the check costs more than 50 ms for a UV tag it is skipped for that tag, while the counters and topology triggers stay active). These are compared every `TRACK_SCAN_INTERVAL` (1 s), and the interval stretches up to 10 times the scan's own duration (at most about 10% of the main thread).
- **Slow-read guard:** if serializing one atom takes longer than 30 ms, it is not read again until 4 times that duration has passed (when dragging a point on huge models).
- **A new UVW tag is born empty (0 elements);** calling `ResizeObject(same, same)` on its owner sizes the tag. The receiver does this itself (`load_uvw`).
- **Out of scope (v1):** n-gon information, vertex map and vertex normal tags (weight and pose morph arrived in Stage 8), the active point/polygon selection (a view state specific to the user), extra spline types.

## 7e. Visibility: who is where, who clicked what (Stage 7)

Like the Multi-User plugin in Blender: other users' **cameras** (a coloured camera icon + a name label) and **selected objects** (a box in the user's colour + a name label) are drawn in the 3D view.

- **Drawing object:** from Python only objects can draw into the viewport (SceneHook is C++ only). During the session the document holds a hidden (`NBIT_OHIDE`) object of our own type (`PRESENCE_OBJECT`), and its `Draw()` draws the overlay. This object is **never synchronized** (`nodes.object_supported`) and is deleted when the session ends.
- **Camera:** each user's active viewport camera (position + right/up/forward axes, 12 numbers) is shared as `presence.cam` at most 5 times a second. On the receiving side the shape, finiteness and axes are validated (`overlay.parse_camera`). The icon size is proportional to the distance from the viewer, so it always looks about the same size on screen.
- **Selection:** someone else's selected object = their lock. The box is the object's oriented bounding box (`GetMp`/`GetRad`/`GetMg`); objects with no size (nulls) get a box of at least 15 cm. A locked tag is drawn as the object that owns it.
- **Name label:** a frame in the user's colour + `DrawHUDText` text. The frame colour shows who is who even when the text colour is fixed. Labels behind the field of view are skipped.
- **Colour names:** the server gives each user a colour and a name (Red, Blue, Green...). It shows in the panel as `Name [Colour]`, so people can say "the blue one".
- **Pure geometry:** `overlay.py` does not import c4d, so its tests run without C4D.
- **Limit:** that the pixels appear on screen can only be checked by hand in a C4D window (CHECKLIST 7.x). But the drawing code is run against the real C4D drawing API with `tests/c4d_real/draw_check.py` (c4dpy has a real off-screen `BaseDraw`): all calls are accepted, the camera and object box are read correctly, and the demo overlay draws 22 lines + 2 name labels. Command: `c4dpy tests/c4d_real/draw_check.py` (the result is written to the `C4D_COLLAB_DRAW_OUT` file).
- **Two files are needed for registration:** registering the object type requires `res/c4d_symbols.h` and `res/strings_us/c4d_strings.str` (they can be empty); without them it gives "Could not initialize global resource". Verified with `GeResource.Init(<absolute path>)`: `False` without the files, `True` with them.

### Registration bug found in the live window (Stage 7)
`RegisterObjectPlugin` looks for the global `__res__` variable of the **module** that makes the call, and only the `.pyp` file has it. When registration was done from `presence.py`, all three registration forms failed with `ReferenceError: Could not find required '__res__'` and the drawing object was never created (locks and sync worked normally). Fix: the registration call is made from the `_register_object` function in the `.pyp`, and if `__res__` is missing it is built with `GeResource`. The panel and message plugins do not need this variable, so they were fine. Registration results are written to `c4d_collab.log`.

### Two problems found in a live trial, and their fixes (Stage 7)

**1. On R19 the insides of objects were visible** (the cube had no front face, the cylinder had jagged edges). The overlay object drew in the `DRAWPASS_OBJECT` pass **before** the scene's objects, and it changed the screen matrix/line state and left it that way. 2023 resets the state for every object, but R19's older drawing pipeline does not, so the cube and cylinder drawn afterwards were drawn with a broken state. Fix (corrected with a live trial): first `DRAWPASS_HIGHLIGHTS` was tried, but C4D draws that pass in its own highlight colour (white, `GetHighlightPassColor`) and R19 does not show it at all, so the colours disappeared. The real cause is that the 2D drawing (name labels) turns the depth test off and leaves it off. Now `DRAWPASS_OBJECT` is used: the overlay object sits **at the very end** of the document (`presence.move_last`, checked by `SceneSync.tick`), so it draws after the scene objects. Also, when `Draw()` finishes it restores the `USE_Z/SETZ/OGL_CULLING` parameters, `SetDepth(True)` and the object matrix. The lines are in the user's colour. The drawing object also stayed selected when it was added; it is no longer left selected.

**2. Camera and object movement stuttered.**
- *Sending:* the camera now goes out 20 times a second (it was 5), with a dead zone of 0.5 cm / 0.002 (`sync.CAMERA_INTERVAL`).
- *Smoothing on the receiver:* the pose shown moves toward the incoming camera pose exponentially over time (`overlay.CameraSmoother`, τ = 70 ms, based on real elapsed time rather than a call count; the step is capped at 250 ms so the camera does not fly when the interface hangs). A camera seen for the first time appears in place.
- *Object movement:* the position/rotation/scale of an object someone else is dragging is no longer written instantly. It glides with `smoothstep` for the time between two updates (30-120 ms) (`tween.TransformTweens`). After each step the tracker is told "I wrote this" (no echo); rotation takes the short way round; only plain transform updates glide, and others are applied immediately. Delete/move/restore cancels the glide.
- *Light redraw:* `DrawViews` (view redraw) only when the camera/selection/user list changes; `EventAdd` (re-evaluation) when the scene changes. Before, every message called `EventAdd`.
- *Timer:* while the panel is open, 33 ms (30 Hz) drives the network, the scan and the animation.
- *Panel:* `local_addresses()` made a DNS query on every refresh (on some systems that blocks the main thread for hundreds of ms); now cached for 60 s. Panel texts are only written when they change.

### Stutter on R19 when hosting (a real rig: 1540 items, 106 XPresso, 51 Pose Morph, 335 unnamed tags)

When a session opened, two timers (the panel's at 16 ms and Cinema 4D's own message timer at 20 ms) did the same work twice, and every scan walked the whole scene. A rig with hundreds of expressions moves the document counter on every evaluation, so full scans ran constantly. Fixes: (1) `bridge.timer_pump` does not run the two timers more often than every 12 ms, and the panel uses 20 ms; (2) `EventAdd` (scene re-evaluation, including rig expressions) runs at most 25 times a second, and a delayed request is not lost (`_EVAL_PENDING`); (3) full scans stay under about 10% of the processor (`FULL_SCAN_DUTY`); (4) tag counting is only done in the sharing scan; (5) for an object whose only change is its transform, three cached parameters are read instead of the whole description (`params._dump_ids`); (6) the selection frame falls back to a box for meshes bigger than 400 polygons / 600 edges (850-1690 lines were seen in the R19 log); (7) diagnostics: full scans longer than 30 ms and pump rounds longer than 40 ms are written to the log (`Full scan took`, `Slow pump`) once every 10 s.
Pose Morph tags (face) are now synchronized, see "Face: Pose Morph".
## 7h. Rigs and big scenes: what went wrong and what fixed it

Characters with large rigs (a real test scene: 1,500 items, 100+ XPresso tags, 50+ Pose Morph tags) exposed many problems. The long measurement diary is kept in the original Turkish document on the `dev` branch. This is the English summary of the rules that came out of it.

**Skin weights and joints**
- The Weight tag travels (`serializers/rig.py`, data section `"cw"`): the joint list (uuids), the rest state per joint, the per-point weights (quantized to 0..65535) and the tag's bind matrix (`GetGeomMg`, sent as `"gm"`). Without the bind matrix a guest bound the mesh where it currently stands and Skin shifted every point by the difference.
- Joints are named by uuid and the receiver binds its own joint objects (`Applier._reload_weights` rebinds after a batch). `GetWeightMap` returns a wrong short list on sparse maps, so weights are read point by point with `GetWeight(joint, point)`.
- `SetAllPoints([])` raises "Invalid object length", so it is never called for an empty mesh.
- Layers are synchronized (`"ly"` in the document data, `serializers/doclayers.py`) and every object or tag names its layer through `ID_LAYER_LINK`. Layer flags change evaluation, and a hidden `JOINTS` layer hides the bone drawings. Layers are added or updated, never deleted.

**Links, lists and deformers**
- Forward references: a snapshot is built in depth order, so a link to an object that does not exist yet used to be skipped silently. `Applier._relink_forward` rewrites such links after the batch.
- Include/Exclude lists (IK "Objects", Mesh deformer "Cages", Skin list) travel as `{"I": [[uuid, flags], ...]}`; members that are not shared are skipped.
- The Mesh deformer's hidden bind data is computed from its "Cages" list. On the guest the `Initialize` button is pressed again after the list is in place (`Applier._bind_deformers`). Initialize also adds Display and Compositing tags to the cages in 2023, and those are removed again so the cages look like the host's.
- Hidden tag data of IK, IK-Spline and Constraint tags (integer, float, vector and matrix values that are not in the description) travels in `"hd"` (`serializers/hiddendata.py`). Without it the IK-Spline tag on the guest never moved its spline.

**XPresso and user data**
- An XPresso graph travels as data (`serializers/xpresso.py`, `"xp"`): nodes, settings, ports and connections. **Only standard operators are carried** (400001000-400001161, no COFFEE). A graph with a Python, COFFEE or third-party node stays local, and incoming data can never create such a node.
- Object-node ports only carry a display name ("Position . Y"). The DescID is found by looking that name up in the connected object's description, using the port data type to tell same-named parameters apart (an object's "Scale" and a user-data "Scale"). Port numbers follow first use, so the same numbers are produced on the guest with spare parameters and the spare ports are deleted.
- Constraint tag weights appear under the target object's name in XPresso (`8502 + target index` in Parent mode, `10002/10012` and `2116` in Transform mode), so ports are mapped to targets by order (`xpresso._constraint_parameters`).
- User data (`serializers/userdata.py`, `"ud"`) is rebuilt with `SetUserDataContainer` using the same ids. Filename parameters are written as item assignments, not plain strings, otherwise XPresso logs `CRITICAL: Stop` on every read.
- Parameters driven by a graph (`"dr"`) are not edits: both sides evaluate the same graph (`Tracker._mask_driven`). The same goes for Vibrate (camera shake) tags, joints that an enabled IK or IK-Spline chain solves, and objects carried by an enabled Constraint tag.
- **Global Matrix ports** of the XPresso Object node cannot be built from Python on R19 (`AddPort` always returns `None`). Those connections are applied another way: a local Python tag "Collab rig copy" (fixed code inside the plugin, never sent) copies the matrix, with a per-tick fallback (`xpresso.apply_mirrors`).
- On R19, `Description.GetParameter(id)` needs a second argument (`ar`), otherwise it raises `TypeError` and no vector port name could be resolved. `_level_name` tries one argument, then two.

**Echo and noise (a guest must not send back what it only computed)**
- The guest starts at the host's frame (`SceneSync._align_time`); after that everyone keeps their own playhead. Changing the frame must not send edits.
- Constraint tags recompute their offsets after targets are linked, so new Constraint tags are evaluated once and then the host's values are written (`Applier._settle_constraints`). New solved joints start at scale 1 and the guest's IK brings them to the host's value.
- A rig writes transforms with small noise, so edits below 0.02 cm / 0.003 rad (joints 0.05 cm / 0.03 rad) are not edits (`tracker.NOISE`, `JOINT_NOISE`). Weight tags store 16 bit, so 1/65535 differences count as the same skin. Different versions store some values differently (UVW, Texture and Phong tags, joint transforms), so after applying remote data the local reading is remembered and only a difference from that reading counts as an edit (`Tracker._capture_applied`).
- Tags a guest creates locally (Display, Compositing, "Collab rig copy") are skipped (`tracker.skip`) and are never counted as the "previous sibling" of a shared tag.
- At session start the plugin reports what stays local ("Not shared (stays local): 3 x XPresso (tag) ...") in the panel log and the log file.

**Speed on big scenes**
- Only selected objects are looked at while dragging (`Tracker._scan_selected`), a full scan follows at least every 0.5 s and never takes more than about a third of the processor, and while the timeline plays or the user dragged in the last 0.6 s only the quick look runs. Full scans are limited to about 10% of the main thread (`FULL_SCAN_DUTY`), scene re-evaluation (`EventAdd`) to 25 per second, and the two timers never run more often than every 12 ms.
- Material and shader walks only run when the MATERIAL/SHADER counter moves. Selection outlines fall back to a box for big meshes. Diagnostics (`Full scan took`, `Slow pump`, `Pump stats`) are written to the log at most every 10 s.
- The log `c4d_collab.log` rotates at 5 MB (+2 old files).

**R19 guest specifics (measured on real R19)**
- Pose Morph numbers are not reused by `AddMorph` on R19, so `morph.NUMBER_MAP` translates the sender's numbers into local ones for XPresso ports and animation tracks.
- A graph that could not be built completely is marked `xpresso.DEGRADED`, and its `xp` data is pinned to the shadow so the host never rebuilds its working graph from a guest's incomplete one.
- A texture path that C4D shortens to a bare name is redirected to the cached copy (`assets.apply_files`, repeated every 2 s by `Applier.repair_textures`). R19 adds a default Reflectance layer when the channel is switched on, so extra layers are removed (`layers.has_extra`).

**Known limits:** hand edits to joints inside an IK chain are not sent while IK is on; some face meshes still deform slightly differently on the guest; Normal tags are not carried; XPresso Object nodes tied to a Layer are not built; the R19 fixes above for ports were reproduced by simulation and still need a real R19 check.

## 7i. Face: Pose Morph (Stage 8)

Code: `serializers/morph.py`.

- **Data (tag section `"pm"`):** `{"n": owner's point count, "m": [{"n": name, "d": {chunk: float x3}}...], "s": [strength | null]}`. Morphs keep the tag's order; 0 = base pose, moves no points. `"d"` holds differences **relative** to the base in chunks of 128 points; chunks that are all zero are not sent. Strength `null` = driven by a track, and the receiver keeps its own value. Only point morphs travel; position/rotation/scale, UV, parameter, map and hierarchy morphs stay local.
- **Read/write:** a morph is opened with `SetMode(EXPAND, REL)`, `GetFirst().GetPoint/SetPoint`, then `SetMode(COLLAPSE, AUTO)`. The strength slider of morph `i` is DescID `(4000, 1001 + 100*i)`, which is **not** in the tag's description, so the generic parameter serializer does not see it.
- **Counters:** editing morph points, renaming, adding a morph and changing the active pose move **no counter**, so there is a periodic check. Reading is expensive (about 3 ms for 10,000 points, writing 17 ms), so points are only re-read when the tag's **signature** (mode, active pose, morph names, point count) changes.
- **The tag writes its result into the mesh's own points** (no deform cache). The mesh is only taken as the base when the tag is in edit mode with the base pose selected (`morph.mesh_shows_base`); otherwise the shared geometry is kept, so a slider move never sends the deformed face as geometry.
- **Receiver:** all points are written to a Pose Morph mesh, then the base pose is stored again (`morph.store_base`). If the morph list differs the tag is rebuilt from scratch. The tag is always kept in animation mode. Edit/animation mode (`ID_CA_POSE_MODE`) is each user's own view and is not sent.
- **Locks:** on a locked face, a change of strengths only (rig evaluation) is not rolled back; a change of shape or name is. If the point counts do not match, points are not written and a warning is logged.
- Morph numbers are kept: drivers (XPresso ports, tracks) recognise a morph by its number, not its position in the list. `AddMorph` gives the lowest free number on 2023, so the guest fills every number with a placeholder, frees each morph's own number just before adding it, and deletes the extra placeholders.

## 7j. Materials, shaders and texture files

- **New node kinds:** `material` (document level, sibling chain), `shader` (parent: a material or a shader; nested shaders) and `asset` (texture file). `KIND_ORDER` = asset, material, shader, object, tag, track, doc, so files and materials come first in a snapshot.
- **Data:** the same generic parameter serializer as objects and tags (`params.dump`, driven by the description), so plugin materials such as Corona's travel too if that plugin is installed on the other side. A type that is not installed becomes a standard material placeholder.
- **Links:** shader links such as `MATERIAL_COLOR_SHADER` are rewritten after a batch (`Applier._relink`). Every scan first gives ids to all materials and shaders.
- **Change detection:** `GetDirty` per material and shader, and the document counters `HDIRTYFLAGS_MATERIAL` and `HDIRTYFLAGS_SHADER` (on R19: ALL). Shader children are only walked with `GetFirstShader()/GetNext()`.
- **Texture files (`serializers/assets.py`):** a relative path is searched next to the document, in `tex` and in the global texture folders. A file (at most 25 MB, image extensions only) becomes an `asset` node whose uuid is its SHA-1, so a file travels once (300 MB total per session). The receiver uses the file on its own path if it exists, otherwise a cached copy; the redirect stays local. Safety rules: image extensions only, the file name is cleaned and can never leave the cache folder, the unpacked size is limited, and the content must match the SHA-1.
- **Reflectance layers (`serializers/layers.py`):** a new standard material is born with one default reflectance layer, so a material without layers would get an extra specular on the other side. The layer list travels as `"rl"`, and layer parameters are keyed by layer **order**, not by C4D's version-specific ids (`to_canonical`/`to_local`). Needs R17.032+.
- **Message size:** `protocol.chunk_ops` splits batches and snapshot parts by count and estimated bytes (8 MB).
- **Known limits:** the inner layer list of the Layer shader, node-based materials and sound/video files do not travel; shaders that carry Python stay local.

## 7k. Selection shape and smooth motion

- **Selected object's own shape:** the object's real edges are drawn in the user's colour instead of a box (`presence.object_outline`). The mesh is only re-read when the object's `GetDirty(DATA|CACHE)` changes. Empty objects, splines and big meshes (over 6,000 polygons / 9,000 edges) fall back to a box.
- **Object motion:** an object is shown one update interval behind and moves **linearly** from each received pose to the next (`tween.TransformTweens`, a queue of segments), so the speed stays constant between updates. If more than 0.25 s accumulates, the queue is dropped.
- **A bug found by real C4D:** creating an unregistered object or tag type raises a bare `BaseException` rather than a normal error, which `except Exception` does not catch. An unknown type coming over the network could have broken a whole sync round. `compat.allocate()` and the boundary points catch `BaseException` (except `KeyboardInterrupt`/`SystemExit`, `core.compat.is_fatal`).

## 7l. MCP page: an AI assistant can use a live Cinema 4D (Stage 8, development aid)

Maxon's own MCP server only exists in 2026.4+, not in 2023 or R19. We offer the same with an **Edit → Preferences → MCP** page and a local server from R19 onward. The page follows the layout of Maxon's 2026 page: **Server, Clients, Security, Toolset** sections, buttons, a client drop-down and a confirmation question when switching on. The goal is development, not Collab itself: an app such as Claude Code can run probes, tests and scene reads in a live C4D without waiting for a person. It has nothing to do with Collab's network sessions and does not change their security rules.

```
AI app ──stdio──► mcp_stdio_bridge.py ──127.0.0.1──► core/rpc.RpcServer (socket thread, only writes to a queue)
(Claude Desktop/Code,  (system Python)                      │ notify → SpecialEventAdd(MCP_MESSAGE)
 Codex, Cursor...)                                          ▼
                              McpMessageData (main thread, 50 ms timer) ► mcp_service.execute ► mcp_tools.*
```

### Page (`c4d_adapter/mcp_prefs.py`, `res/description/mcpprefs.*`)
A `PreferenceData` page (R19+; on older versions the page is skipped and Collab still loads). Values live in the world container (`MCP_CONTAINER`).

| Section | Field | Note |
|---|---|---|
| **Server** | Allow MCP Server | **Off** by default. Switching on asks for confirmation (what can be read, the Python warning, the provider's data policy); No = stays off. It comes back by itself when the program starts, without asking |
| | Status, Host (127.0.0.1) | Information fields (greyed) |
| | Port (1024-65535, 47921) | The server moves to the new port when it changes |
| | Auth Token + Show Token | The field is masked (`****…` + last 4 characters); full when Show Token is on. Typed text is discarded |
| | Copy Token, Regenerate Token | Buttons. A new token is written to the config file, so nothing changes on the AI side |
| | Request Timeout (s) | 30 s, at least 0.1. If a command would keep the main thread waiting this long (C4D rendering/a long script) it is **not run** and "timed out" is returned; a late command never runs afterwards |
| **Clients** | MCP Client | Claude Desktop, Claude Code, Codex, Cursor, Other |
| | Client Status, Config File | "Set up" / "Not set up" / "Not installed" / "Set up by hand" (cached for 2 s) |
| | Update Selected Client | Asks for confirmation, makes a backup, only adds or changes the `cinema4d` entry. Claude Code: puts the command on the clipboard (it is set up from a terminal); Other: points to Copy Config |
| | Remove from Client, Copy Config | The same rules; Copy Config copies each client's own format (JSON `mcpServers`, a Codex TOML block, a `claude mcp add` line) |
| **Security** | Allow Python Execution (run_script) | **Off** by default, read again on every call |
| | Log Incoming Calls | On by default (audit log) |
| | Show Rejected Calls in Console | Writes rejected calls to the Python console |
| **Toolset** | Basics, Hierarchy, Entities, System | The tools of a switched-off group are not listed to the AI (after the client restarts) and are rejected **immediately** if called |

Not taken from Maxon's page on purpose: *Allow Remote Connections / Allowed IPs / Browser Client Origins* (a remote connection would be unauthenticated and is not needed) and *Allow Python-Bearing Operations* (we have no tool that creates objects carrying Python).

Buttons and drop-downs work on a Preferences page: buttons are caught in `Message` as `MSG_DESCRIPTION_COMMAND`, and the drop-down is a `CYCLE`. Value ids 1000-1002 and 1005 keep their meaning from the first version; everything new starts at 1100.

### Client setup (`core/mcp_clients.py`)
- **Finding Python:** AI apps start from the desktop and may not see the shell's PATH, so the **absolute path** of a real install is written: PATH (except the Microsoft Store `WindowsApps` shortcut, which opens the Store) → known install locations → `py -3` → `python3`.
- **Files:** Claude Desktop 4.x keeps its data in `%LOCALAPPDATA%\Claude-Data\claude_<id>\claude_desktop_config.json` and does not read the old locations. Older versions use the Microsoft Store package folder or `%APPDATA%\Claude\claude_desktop_config.json`; Cursor `~/.cursor/mcp.json`; Codex `~/.codex/config.toml` (`CODEX_HOME` applies); Claude Code `~/.claude.json` (only read for status, never written).
- **Safety rules:** a `.bak-<time>` backup before any change; a temporary file + replace; **a file that cannot be parsed is never written**; only the `cinema4d` entry is added, changed or deleted and the rest stays as it is; in Codex a marked block (`# >>> C4D Collab MCP ... <<<`) is used and a hand-written `[mcp_servers.cinema4d]` entry is left alone; if the app is not installed nothing is created.
- **Clipboard:** `core/clipboard.py` (ctypes with Unicode on Windows; Cinema 4D's Python can read the clipboard but not write it).

### Server and security
- Off by default; it binds to `127.0.0.1` only and accepts local connections only; a random 128-bit token, HMAC-SHA256 on the wire (the token is never sent); 4 KB / 5 s before login, a 60 s lock after 5 wrong logins, at most 4 clients and 8 pending commands at a time, a 4 MB result limit; the socket thread never runs a command (fixed rule 4); JSON only; every command is written to `c4d_collab_mcp_audit.log` (the token is never written); when off, the config file (which contains the token) is deleted. `run_script` needs a separate permission; while it runs C4D does not respond (this cannot be limited), and `sys.exit()` ends the script, not Cinema 4D.
- **Config file** (`c4d_collab_mcp.json`, while on): port, token, `timeout`, `enabled_tools`, the bridge's path and the entry written to the AI app. The bridge reads it on every call and filters the tool list by `enabled_tools`.
- **Tools:** `ping`, `get_info`, `get_hierarchy`, `get_object`, `get_log`, `run_script`.
## 7g. Version compatibility: R17 to 2026 (Stage 8)

The targets are R17, R18, R19, R20, R21, R22, R23, 2023, 2024, 2025 and 2026. Only **R19** and **2023** (and 2024, waiting for a licence login) are available as real copies. For the others we cannot prove that the code **runs**, only that **the API it uses exists in that version and the fallbacks are tested**. The two are kept apart:

| Version | Python | Status |
|---|---|---|
| **R19** | 2.7 | **Verified with real tests** (two-process scenario, manual checklist) |
| **2023** | 3.9 | **Verified with real tests** (c4dpy scenarios, MCP page live) |
| R17, R18 | 2.7 | API compatibility checked against the documentation + fake version profile tests; **not tried in a real copy** |
| R20, R21, R22 | 2.7 | profile tests (R20 and R21 profiles; R22 = R21); **not tried in a real copy** |
| R23 | 3.7 | profile tests + Python 3.7 static audit; **not tried in a real copy** |
| 2024 | 3.11 | **not tried in a real copy** (installed, but `c4dpy` asks for a licence login on first use) |
| 2025, 2026 | 3.11+ | same API surface as the R23 profile (2026 SDK examples and 2023 docs); **not tried in a real copy** |

### How we verify (four layers)
1. **API inventory** (`tests/compat/api_inventory.py`): every Cinema 4D name the plugin uses (AST scan: 60 module-level symbols, 174 object methods, 95 guarded names). On 2023 all of them were checked with a real `c4dpy` (the only one missing is `c4d.storage.ByteSeq`, which is R19-specific and guarded); on R19 all of them appear in the binding library.
2. **Version knowledge** (`tests/compat/api_matrix.py`): for each name, "since which version it exists". The source is Maxon's Python API docs ("New in version ..." notes on the 2023.2 class pages: the docs only mark what is newer than their own base version, so no note means R16 or older), the 2026 SDK examples and what was measured on R19/2023. `test_api_matrix.py` **does not let the tests pass while a new API name is unclassified**, and checks that every name newer than R17 has a guard next to it.
3. **Version profiles** (`tests/adapter/profiles.py`, `test_profiles.py`): a fake `c4d` module behaves like R17, R18, R19, R20, R21 or R23 (missing methods really raise `AttributeError`, flag names change); in every profile 9 test classes (sync, editing, rig, selection, mesh, animation, locks, Pose Morph, materials) and the plugin load run. On Python 2.7 and 3.x.
4. **Python audit** (`tests/compat/python_audit.py`): scans the source without running it for syntax that 2.7 and 3.7 cannot read (f-strings, `:=`, annotations, `async`, `nonlocal`, `yield from`, keyword-only parameters...), unguarded new standard library names (`time.monotonic`, `shutil.which`, `subprocess.run`, `pathlib`...), and missing `__future__` and `coding` lines. It tests itself (`test_python_audit.py` shows it catches bad examples).

Python interpreters used: 2.7.9 (R19's own), 3.9.1 (2023's own, runs standalone), 3.14 (the machine's). 3.7 and 3.11 are not available; the static audit above covers 3.7.

### Real gaps found in the docs, and their fallbacks
| API | Since | What happens in versions without it | Where |
|---|---|---|---|
| `BaseDocument.GetHDirty` | R19 | R17/R18 have no document counters: the snapshot changes on every call, so the scene is **scanned every tick** (the tick's own rest limits the main thread to about a third). Slow but correct, no crash | `compat.doc_dirty`, `HAS_HDIRTY` |
| `BaseSelect.GetRange` | R19 | The selection is read from the flags (`GetAll`); the same ranges come out | `geometry.dump_selection` |
| `BaseDraw.DrawHUDText` | R18.020 | On R17 the name label of a selected object is not drawn, the coloured frame is | `presence.draw_overlay` |
| `CAWeightTag.SetWeightMap` | R21 | On R17-R20 skin weights are written point by point (`SetWeight`): slow on big meshes, same result | `rig.load_weights` |
| `RegisterPreferencePlugin`, `PreferenceData` | R19 | R17/R18 have no MCP page (and no needless timer is registered); Collab is not affected | `mcp_prefs.available`, `c4d_collab.pyp` |
| `AddUniqueID`/`FindUniqueID` | R16.021 | Present on R17 (document); `ByteSeq` is only tried on a TypeError | `compat.write_uid` |

Also: R17/R18/R19 have no `DESCFLAGS_*_NONE` names (`_0` is used) and R19 has no detailed `HDIRTYFLAGS_*` (measured); both are already handled in `compat`. At start-up a `Capabilities: ...` line is written to the log (which fallbacks are in use); a report from any version is read by looking at it first.

### What cannot be proven (a real version is needed)
- **Behaviour:** which edit moves which counter (R17-R22, 2024+), drawing order, version-specific details such as `GetDeformCache`. Someone who can open a real version should run `tests/c4d_manual/spike_probe.py` and `CHECKLIST.md` and send the result.
- **The Python 2.7 string rule** (`str`/`unicode`): enforced by the fake module on R17-R22, measured on real R19 only.
- **2024-2026:** the docs and SDK examples show the API surface has not changed, but if something was newly removed in 2025/2026 only a real run will catch it. For 2024, activating `c4dpy` once is enough.

### Before using a new API
Run `python tests/compat/api_inventory.py` to see the names in use; `python -m unittest discover -s tests/compat` fails for a new name and says what to do. Look for the "New in version" note on the class page in Maxon's docs; if it is newer than R17, write a fallback, add it to `api_matrix.NEWER`, mark it missing in that version in `profiles.py` and add a test.

## 7b. Adapter summary
- `bridge`: a MessageData plugin. It is triggered by `SpecialEventAdd` and a timer and runs on the main thread.

## 8. Testing
The test files live on the [`dev` branch](https://github.com/MertDurmus41/collab-4d/tree/dev) so that this branch only holds what is needed to use and read the project.
- `tests/core`: the core (protocol, model, locks, real server + client scenarios, security, session manager).
- `tests/adapter`: the C4D adapter, with a **fake `c4d` module** (`fake_c4d.py`). It tests loading of the `.pyp` file, the `str`/`unicode` rule on Python 2, R19's `ByteSeq` behaviour and the panel's Host/Join/Leave flow without opening C4D. It verifies our logic, not real API behaviour.
- `tests/c4d_real`: a two-process scenario on the **real C4D API** (host + guest; sending a scene, edits, hierarchy, delete, geometry, links, blocking code types, no echo). The two sides synchronize through the session's own presence messages.
  - `python tests/c4d_real/run.py` → two hidden (no GUI) C4D 2023 `c4dpy` processes, fully automatic.
  - `python tests/c4d_real/run.py --manual-peer guest|host --label r19` → one role in `c4dpy`, the other in a C4D with a GUI through **Plugins → C4D Collab Self-Test (dev)** (for R17-R20, which have no c4dpy). This command only shows if the file `plugin/c4d_collab/selftest.enabled` exists (it contains a repository path and is not committed to git).
- `tests/c4d_manual/CHECKLIST.md`: manual tests in a real C4D (mouse dragging, Ctrl+Z and other things that cannot be automated).
- Tests only use the standard library `unittest`, so they run on any Python:
  - Python 3: `python -m unittest discover -s tests/core -v`
  - Python 2.7 (R19's own Python): `python.exe -m unittest discover -s tests/core` with the interpreter from R19's `resource/modules/python/Python.win64.framework`
- In-C4D measurement script: `tests/c4d_manual/spike_probe.py` (run it from the Script Manager).

## 9. Measurements (Stage 0)
Source: `tests/c4d_manual/spike_probe.py`. Measured with programmatic edits; edits made with the mouse are verified separately.

| C4D version | Python | Status |
|---|---|---|
| 2023.1.3 | 3.9.1 | Done |
| R19.024 | 2.7.9 | Done (the save/load test could not be done because of a test bug) |
| 2024 | 3.11 | Pending |

### Version differences
- **R19 only has `HDIRTYFLAGS_ALL` at document level.** The detailed flags (`OBJECT`, `OBJECT_MATRIX`, `OBJECT_HIERARCHY`, `ANIMATION`, `TAG`) do not exist on R19. The `compat` module maps them per version: if they are missing, `ALL` is used and "anything may have changed" is assumed (a full scan). With the measured speeds this is fast enough on R19 too.
- **On R19 `AddUniqueID` does not accept `str`/`bytearray`**, it wants `c4d.storage.ByteSeq`. What works: `seq = c4d.storage.ByteSeq(None, 16)`, then writing the bytes one by one with `seq[i] = chr(b)`. Reading back is correct, and a cloned object does not carry the ID (same as 2023). `compat.write_uid()` tries `bytes` first and uses this method if that fails.
- **On Python 2 (R17-R22) the c4d API does not accept `unicode` text**, it wants `str` (for example the `SaveDocument` file path). Because we use `unicode_literals`, every string that goes to c4d passes through `compat.native_str()`. This rule only concerns the adapter layer.
- Storing the ID in the object's BaseContainer was tried as a fallback and works, but the ID also passes to a cloned object, so it is not used; `AddUniqueID` is used.
- `GetMarker` does not exist in Python in either version and is not used.

### Findings shared by R19 and 2023
| Edit | Counters that change on the object | Counters that change on the document (`GetHDirty`) |
|---|---|---|
| Move / rotate | `MATRIX` | `OBJECT_MATRIX` |
| Parameter (for example cube size) | `DATA` | `OBJECT` |
| Rename | `DATA` | `OBJECT` |
| Selection | **none** | **none** |
| Reparent | **none** | `OBJECT_HIERARCHY` |
| Add a tag | `DATA, MATRIX` | `OBJECT, OBJECT_MATRIX, TAG` |
| Add/change/move a keyframe in time | object and CTrack: **none**, CCurve: `CHILDREN` | `ANIMATION` |
| Change frame | none | none |
| Move a point | `DATA` | `OBJECT, TAG` |
| Extrude (topology change) | `DATA, SELECT` | `OBJECT, TAG` |

(The document column is from 2023; on R19 only `ALL` changes for all of them.)

**Effects on the design (the tracker):**
1. **Two-stage scan:** first the document's `GetHDirty` counters are checked (very cheap). If none changed, nothing is scanned in that tick. On R19 this check is made with `ALL`.
2. If `OBJECT` / `OBJECT_MATRIX` / `TAG` changed, the objects' `DATA`/`MATRIX` counters are scanned.
3. If `OBJECT_HIERARCHY` changed, the object counters cannot be trusted: the `parent/prev` structure in the scene is compared with the mirror.
4. If `ANIMATION` changed, only the `CHILDREN` counters of CCurves are scanned.
5. **Selection** and the **current frame** are not reflected in the counters: `doc.GetActiveObjects()` and `doc.GetTime()` are checked separately in the timer (for locks and presence).

**UniqueID:**
- An ID written with `AddUniqueID` survives save/load.
- **A cloned object does not carry the ID** (`GetClone`). So an object without an ID means a new object, and no extra step is needed to detect copies.
- `AddUniqueID` does not overwrite for the same application ID, it **adds** a new record (the count became 2). Reading returns the last one written. Rule: an ID is written to an object only once.

- Modeling: moving a point and extrude raise the object's `DATA` counter. Looking at the point and polygon counts tells "only points changed" from "topology changed".

### Animation measurements (Stage 5, 2023, `tests/c4d_manual/probe_animation.py`, automatic with c4dpy)
| Edit | Object / Track / Curve counter | Document counter |
|---|---|---|
| Add, delete a key; value, time, interpolation | – / – / `CHILDREN` | `ANIMATION` |
| **Tangent value** | – / – / – | **–** |
| **Track setting (after-loop and so on)** | – / – / – | **–** |
| Change frame (`SetTime`) | – | – |

Other findings: the value track type is `CTbase` = 5350, `CCurve.AddKey` returns a dict (`key`, `nidx`), and long/bool parameter tracks also use a numeric value with `GetValue()`.

### Modeling measurements (Stage 6, 2023, `tests/c4d_manual/probe_modeling.py`, automatic with c4dpy)
| Edit | Counter |
|---|---|
| `SetPoint` | object `DATA`; document `OBJECT, TAG` |
| **Selection change in a selection tag** | **no counter** |
| UVW `SetSlow` | UVW tag `DATA`; document `TAG` |
| Active polygon selection (`GetPolygonS`) | object `SELECT` (not synchronized) |
| Spline `SetPoint` | object `DATA`; **`SetTangent` and `SetSegment`: no counter**; a type change `DATA` |

Other findings:
- `SelectAll(a, b)` selected nothing; the selection is written with **`SetAll(list)`** and verified with `GetCount` (a `Select(i)` loop if that fails). Reading uses `GetSegments`/`GetRange(i, count)` (closed ranges).
- After `MakeTag(Tuvw)` the UVW tag has 0 elements; `ResizeObject(same size)` sizes the tag to the polygon count. After that C4D keeps the tag in step with the polygon count (extrude: 4→8 polygons, UVW 8).
- `ResizeObject` keeps existing point and UV data (growing and same size).
- `BaseObject(Ospline)` is born empty (0 points); `SetSegment` works after `ResizeObject(points, segments)`. For Bezier the tangent count = the point count.
- UV values are stored as **float32** in C4D; we cannot read back exactly what we wrote, but the sender always reads from C4D, so it stays consistent (no echo).
- Speed (2023): reading 100,000 points about 20 ms, polygons about 27 ms, writing about 26 ms.

**Performance (reading 2 counters per object, full scan):**

| | 1,000 objects | 10,000 objects |
|---|---|---|
| R19 | 0.56 ms | 6.13 ms |
| 2023 | 0.51 ms | 4.86 ms |

The target was under 10 ms per 1,000 objects; the real value is about 20 times faster.