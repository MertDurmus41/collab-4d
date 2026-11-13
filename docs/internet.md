# Working together from different homes

Collab 4D connects computers on the same home or office network directly. From different homes, cities or
countries the routers are in the way. The easiest free fix is **Tailscale**: every computer installs it once,
then Collab 4D works as it does at home. Tailscale encrypts the traffic and needs no router settings.

Menu and button names may differ slightly between Tailscale versions.

## Host (the person who starts the session)

1. Install Tailscale from <https://tailscale.com/download> and open it. Sign in with a Google, Microsoft,
   GitHub or Apple account.
2. Invite your friends. There are two ways:
   - **Share only this computer (recommended):** on <https://login.tailscale.com/admin/machines> open the
     **…** menu on your computer's row → **Share**, then copy the link and send it to your friend. They only
     see this one computer of yours.
   - **Invite them to your network:** **Users → Invite users**. The free plan has a user limit, so check the
     current limit on Tailscale's pricing page.
3. In Cinema 4D open the Collab 4D panel, type your **Your name** and a **Password** (at least 4
   characters), and press **Host session**.
4. The panel shows a line like
   `Others join with port 47800 at:  Tailscale 100.101.102.103 (any home)  192.168.1.5 (same network)`.
   Send your friends the **Tailscale** address, the port and the password.
5. If **Windows Firewall** asks, allow **both Private and Public networks**. Windows may count the Tailscale
   network as "Public". If you only chose "Private" before, go to **Windows Security → Firewall & network
   protection → Allow an app through firewall → Cinema 4D** and tick **Public** as well.

## Joining

1. Install Tailscale, sign in with your own account and open the link the host sent you.
2. In the Collab 4D panel type the host's Tailscale address (it starts with `100.`) in **Host address**, then
   the **Port** and **Password**, and press **Join**.

## Good to know

- The session runs inside the host's Cinema 4D. If the host closes Cinema 4D or presses **Leave**, the
  session ends for everyone.
- Tailscale must be running on the host's computer. If the panel shows no "Tailscale ..." address, Tailscale
  is off or not signed in. The panel refreshes its addresses every minute.
- Everything is free. You only pay for Tailscale's large team plans.

## ZeroTier (alternative)

1. The host opens a free account at <https://my.zerotier.com>, chooses **Create A Network** and copies the
   **Network ID**.
2. Everyone installs ZeroTier from <https://www.zerotier.com/download/>, right-clicks the icon →
   **Join New Network** and enters the Network ID.
3. The host ticks the **Auth** box next to each new member on the network page at my.zerotier.com.
4. The host's ZeroTier address is on the same page, in the host's row. The panel lists it as
   "(same network)". Everyone joining types it as the Host address. The firewall step is the same as for
   Tailscale.

## No VPN at all

Run the [standalone server](../server/deploy/README.md) on a free cloud machine. Everyone joins that one
address and nobody needs a VPN or router settings.