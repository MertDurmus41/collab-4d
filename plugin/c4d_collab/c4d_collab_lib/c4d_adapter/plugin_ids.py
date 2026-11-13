"""Plugin ids.

DEVELOPMENT VALUES: Maxon reserves 1000001-1000010 for testing. Before the
first public release these must be replaced by ids registered (free) at
https://developers.maxon.net/ -> "Plugin IDs". Changing UNIQUE_ID_APP later
makes ids stored in older .c4d files unreadable, so register it before v1.0.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

MESSAGE_PLUGIN = 1000001   # MessageData: wakes the main thread (SpecialEventAdd) and runs the timer
COMMAND_PLUGIN = 1000002   # CommandData: opens the Collab dialog; also the dialog's layout id
UNIQUE_ID_APP = 1000003    # AddUniqueID application id for node uuids
SETTINGS = COMMAND_PLUGIN  # World plugin data container (name, last address, port)
PRESENCE_OBJECT = 1000005  # hidden ObjectData that draws other users (cameras, selections)
MCP_PREFS = 1000006        # PreferenceData page "MCP" in Edit > Preferences
MCP_CONTAINER = 1000007    # world container that stores the MCP page's values
MCP_MESSAGE = 1000008      # MessageData that runs the MCP commands on the main thread
