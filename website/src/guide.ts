/**
 * Panel guide text. Every line here is checked against
 * plugin/c4d_collab/c4d_collab_lib/c4d_adapter/ui_dialog.py and core/session.py on main.
 * Keep it in step with the "Panel guide" section of README.md and docs/internet.md.
 */

export type GuideItem = { n: number; title: string; body: string; example?: string }

export const QUICK_START = [
  {
    who: 'Hosting',
    text: 'Install Tailscale and sign in. In the panel, type your name and a password of at least 4 characters, then press Host session. Send the "Tailscale 100.x.x.x" address, the port (47800) and the password to your team.',
  },
  {
    who: 'Joining',
    text: 'Install Tailscale and open the link the host sent you. In the panel, put the host\u2019s Tailscale address in Host address, the same number in Port and the same password in Password, then press Join.',
  },
  {
    who: 'Finishing',
    text: 'Press Leave. If the host presses Leave or closes Cinema 4D, the session ends for everyone.',
  },
]

/** The Tailscale walkthrough shown between the quick start and the panel reference. Source: docs/internet.md on main. */
export const TAILSCALE_INTRO =
  'If you are not in the same home or office, your routers get in the way. The easiest free fix is Tailscale: everyone installs it once, then Collab 4D works as it does at home. Tailscale encrypts the traffic and needs no router settings.'

export const TAILSCALE_HOST = [
  'Install Tailscale from tailscale.com/download and open it. Sign in with a Google, Microsoft, GitHub or Apple account.',
  'Invite your friends: on login.tailscale.com/admin/machines open the \u2026 menu on your computer \u2192 Share, and send them the link. They only see that one computer. (Alternative: Users \u2192 Invite users; the free plan has a user limit.)',
  'In Cinema 4D open the Collab 4D panel, type your name and a password, and press Host session.',
  'The panel shows a line like "Others join with port 47800 at: Tailscale 100.x.x.x (any home)". Send the Tailscale address, the port and the password to your friends.',
  'If Windows Firewall asks, allow both Private and Public networks. Windows may count the Tailscale network as Public.',
]

export const TAILSCALE_JOIN = [
  'Install Tailscale, sign in with your own account and open the link the host sent you.',
  'In the Collab 4D panel, type the host\u2019s Tailscale address (it starts with 100.) in Host address, then the Port and Password you were given, and press Join.',
]

export const TAILSCALE_NOTES = [
  'The session runs inside the host\u2019s Cinema 4D. If the host closes Cinema 4D or presses Leave, the session ends for everyone.',
  'Tailscale must be running on the host\u2019s computer. If the panel shows no "Tailscale \u2026" address, Tailscale is off or not signed in.',
  'Tailscale is free; you only pay for its large team plans. Menu names may differ slightly between versions.',
]

export const GUIDE: GuideItem[] = [
  {
    n: 1,
    title: 'Your name',
    body: 'The name other users see in the list. If you leave it empty it becomes "User". It is remembered for the next time you open the panel.',
  },
  {
    n: 2,
    title: 'Host address',
    body: 'Used when joining: the address of the computer that hosts the session. The host does not use this field. If you are in different homes, type the host\u2019s Tailscale address (100.x.x.x). When testing two Cinema 4D copies on one computer, type 127.0.0.1.',
    example: '100.101.102.103',
  },
  {
    n: 3,
    title: 'Port',
    body: 'The network port the session uses. The default is 47800, and it must be between 1024 and 65535. The host and everyone joining type the same number.',
  },
  {
    n: 4,
    title: 'Password',
    body: 'Protects the session. Hosting needs at least 4 characters and everyone joining types the same password. It is never saved, and the field is cleared when you press a button.',
  },
  {
    n: 5,
    title: 'Host session',
    body: 'Starts a session on this computer and connects you to it. You can only press it while you are not connected.',
  },
  {
    n: 6,
    title: 'Join',
    body: 'Connects to an existing session using Host address, Port and Password. You can only press it while you are not connected.',
  },
  {
    n: 7,
    title: 'Leave',
    body: 'Leaves the session. If the host presses it, the session ends for everyone. You can only press it while you are connected.',
  },
  {
    n: 8,
    title: 'Status line',
    body: '"Not connected" means you are not in a session and "Connecting to \u2026" means a connection is being made. While connected it shows how many people are in and the delay (ms). Under it, the host sees the addresses and port that others type to join: the Tailscale address works from anywhere, the local address only from the same network.',
    example: 'Hosting - 2 user(s) - 12 ms\nOthers join with port 47800 at:\n  Tailscale 100.101.102.103 (any home)\n  192.168.1.20 (same network)',
  },
  {
    n: 9,
    title: 'Users',
    body: 'Everyone who is connected: name, [colour] (the colour that marks that person in the scene), Cinema 4D and Python versions, and the frame they are on.',
    example: 'Ufuk [Blue]  (C4D 2023.2, Python 3.9)  -  frame 24',
  },
  {
    n: 10,
    title: 'Being edited by others',
    body: 'Objects someone else is editing right now, and who. Selecting an object locks it to you, and the lock opens when you deselect it. You cannot select an object someone else has locked, and any change you make to it is put back.',
    example: 'Cube  -  Ufuk',
  },
  {
    n: 11,
    title: 'Log',
    body: 'Session events, newest on top: who joined, who left, scene shared, sync stopped or resumed, session ended. If an error happens, the details are in the c4d_collab.log file.',
    example: 'Ufuk joined',
  },
]

export const TROUBLESHOOTING = [
  'If you are in different homes, the local address in the "same network" line (192.168\u2026) will not work. Use the Tailscale address (100.x.x.x).',
  'If the panel shows no Tailscale address, Tailscale is off or signed out on the host\u2019s computer. Open Tailscale and wait a minute; the panel refreshes its addresses every minute.',
  'The host\u2019s firewall must allow connections to the port: Windows Security \u2192 Allow an app through firewall \u2192 tick both Private and Public on the Cinema 4D line.',
  'The password and port must be the same on both sides.',
  'If you would rather not use Tailscale, you can forward the port on your router to the host\u2019s computer and share your public IP, or run the separate server from the repository on a machine everyone can reach. The plugin\u2019s own traffic is not encrypted; Tailscale encrypts it.',
]