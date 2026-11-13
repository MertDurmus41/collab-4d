# -*- coding: utf-8 -*-
"""R19 numbers the Pose Morph sliders differently: a track made on this side must read back with the sender's key (same uuid as the host's track).
c4dpy tests/c4d_real/morph_track_check.py  -> three TRK lines, the first two ending OK / True."""
from pairlib import *
from c4d_collab_lib.c4d_adapter.serializers import morph, tracks, params
p = Pair()
tag = None
for o in walk(p.dst):
    for t in tags_of(o):
        if t.GetType() in morph.POSE_MORPH_TYPES and t.GetMorphCount() > 2:
            tag = t; break
    if tag: break
uid = p.registry2.uuid_of(tag)
local = [morph._number(tag, i) for i in range(tag.GetMorphCount())]
morph.NUMBER_MAP[uid] = dict((n + 2, n) for n in local)   # pretend the sender numbered everything +2
sender_key = "4000:5:0/%d:19:0" % (1001 + 100 * (local[1] + 2))
track = tracks.find_or_create_track(tag, sender_key)
d = params.key_of(track.GetDescriptionID())
print("TRK local key of the track", d, "-> back to sender", morph.sender_key(uid, d), "expected", sender_key, "OK" if morph.sender_key(uid, d) == sender_key else "WRONG")
print("TRK same uuid as host would compute", tracks.track_uuid(uid, morph.sender_key(uid, d)) == tracks.track_uuid(uid, sender_key))
print("TRK untouched key", morph.sender_key(uid, "903:23:5155"))
