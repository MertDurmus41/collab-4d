"""What the buttons and the confirmation of the MCP page do, with their dialogs.

Every public function runs on the main thread (Cinema 4D calls the page's `Message` there). `run` never raises:
whatever goes wrong is logged and shown in a dialog.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import c4d

from ..core import clipboard, mcp_clients
from ..core.compat import error_text, is_fatal
from ..core.log import get_logger
from . import compat, mcp_prefs, mcp_service
from .mcp_prefs import Settings

LOG = get_logger("mcp")

ENABLE_QUESTION = (
    "Enable the MCP server?\n\n"
    "A connected AI app (an MCP client) will be able to read your open scene (objects, tags, parameters) and the "
    "Collab 4D log. If you also switch on \"Allow Python Execution\" it can run any Python inside Cinema 4D, "
    "with full access to this computer.\n\n"
    "The server only accepts connections from this computer and needs the access token. What the AI app reads is "
    "sent to its provider: check that provider's data-use and training policy before you connect a scene with "
    "licensed content.\n\n"
    "Python execution is off by default and can be switched on under Security.")

RESTART_NOTE = ("Restart %s to load the Cinema 4D tools. It only connects at startup, so leave Cinema 4D running "
                "and quit the app completely first: some keep running in the background after their window is closed.")


def ask(text):
    return bool(c4d.gui.QuestionDialog(compat.native_str(text)))


def tell(text):
    c4d.gui.MessageDialog(compat.native_str(text))


def status_line(text):
    try:
        c4d.StatusSetText(compat.native_str(text))
    except BaseException as exc_:
        if is_fatal(exc_):
            raise


def _copy(text, what):
    """Copy `text`; say so in the status bar, or show it in a dialog when the clipboard is not available."""
    if clipboard.set_text(text):
        status_line("%s copied to the clipboard." % what)
        return True
    if what == "Access token":
        tell("The clipboard is not available. Switch on \"Show Token\" and select the token on the page.")
    else:
        tell("The clipboard is not available.\n\n%s" % text)
    return False


def confirm_enable():
    return ask(ENABLE_QUESTION)


def copy_token():
    _copy(Settings.token(), "Access token")


def regenerate_token():
    Settings.renew_token()
    mcp_service.apply(force=True)   # the new token is in the config file the AI apps read: nothing to redo there
    status_line("New access token generated.")


def copy_config():
    client = Settings.client()
    _copy(mcp_clients.copy_text(client, mcp_service.client_entry()), "%s configuration" % mcp_clients.NAMES[client])


def update_client():
    client = Settings.client()
    name = mcp_clients.NAMES[client]
    entry = mcp_service.client_entry()
    if client == mcp_clients.OTHER:
        tell("Use Copy Config for this client and paste it where the app keeps its MCP servers.")
        return
    if client == mcp_clients.CLAUDE_CODE:
        command = mcp_clients.claude_code_command(entry)
        if _copy(command, "Claude Code command"):
            tell("Claude Code is set up from a terminal.\n\nThe command was copied to the clipboard. Paste it into a "
                 "terminal and press Enter:\n\n%s" % command)
        return
    path = mcp_clients.config_file(client)
    if not ask("Add the Cinema 4D MCP server to %s?\n\n%s\n\nThe existing file is backed up first." % (name, path)):
        return
    ok, message, backup = mcp_clients.update(client, entry)
    mcp_service.forget_client_status()
    if ok:
        tell("%s\n\n%s%s" % (message, RESTART_NOTE % name,
                             "\n\nBackup of the previous file:\n%s" % backup if backup else ""))
    else:
        tell(message)


def remove_client():
    client = Settings.client()
    name = mcp_clients.NAMES[client]
    if client == mcp_clients.CLAUDE_CODE:
        if _copy(mcp_clients.CLAUDE_CODE_REMOVE, "Claude Code command"):
            tell("The command to remove it was copied to the clipboard. Paste it into a terminal:\n\n%s"
                 % mcp_clients.CLAUDE_CODE_REMOVE)
        return
    if client == mcp_clients.OTHER:
        tell("Remove the entry from that app's MCP settings yourself.")
        return
    if not ask("Remove the Cinema 4D MCP server from %s?\n\n%s\n\nThe existing file is backed up first."
               % (name, mcp_clients.config_file(client))):
        return
    ok, message, _backup = mcp_clients.remove(client)
    mcp_service.forget_client_status()
    tell(message + ("\n\n" + RESTART_NOTE % name if ok else ""))


HANDLERS = {
    mcp_prefs.MCP_COPY_TOKEN: copy_token,
    mcp_prefs.MCP_REGENERATE_TOKEN: regenerate_token,
    mcp_prefs.MCP_UPDATE_CLIENT: update_client,
    mcp_prefs.MCP_REMOVE_CLIENT: remove_client,
    mcp_prefs.MCP_COPY_CONFIG: copy_config,
}


def run(ident):
    """A button of the page was pressed. Does nothing when the server is off (the buttons are ghosted then).
    Returns True when `ident` is one of our buttons."""
    handler = HANDLERS.get(ident)
    if handler is None:
        return False
    try:
        if Settings.enabled():
            handler()
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        LOG.exception("MCP page: button %s failed", ident)
        try:
            tell("Something went wrong: %s\n\nDetails are in c4d_collab.log." % error_text(exc_))
        except BaseException as inner:
            if is_fatal(inner):
                raise
    return True
