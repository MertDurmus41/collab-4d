# Run the Collab 4D server on a free cloud machine

The result is a Collab 4D server that is always online. Anyone in the world types its address into
**Host address** in Cinema 4D, adds the **Port** and **Password**, and presses **Join**. Nobody needs to
change router settings or install anything extra.

- **The first person to join shares their open scene**, and everyone who joins later enters that scene.
- When everyone has left, the session resets. The next person to join brings their own scene. Nobody's work
  is lost, because each person's copy stays in their own Cinema 4D.
- **Limit:** a server runs one session at a time. Everyone who knows the password enters the same scene, so
  only share the password with the people you work with.
- **Security:** the password is not sent over the network, but the scene traffic is not encrypted. This is
  not suitable for confidential (NDA) work.

Time needed: 10-20 minutes to open the account, 5 minutes to set up the server.

## 1. Free Oracle Cloud account

1. Go to <https://www.oracle.com/cloud/free/> and choose **Start for free**.
2. Oracle asks for card details. They are only used for verification. "Always Free" resources are not charged.
3. The **Home Region** cannot be changed later. If most of your users are in Europe or Turkey, choose
   **Germany Central (Frankfurt)**. It is also a reasonable middle point for users in the US and Asia.

> Note: Oracle says it may reclaim "Always Free" machines that sit almost unused for a long time. This server
> is idle most of the time, so the risk may apply to you. Upgrading the account to **Pay As You Go** removes
> that risk and you are still not charged inside the free limits (check this against Oracle's current terms).

## 2. Create the server (VM)

Names in Oracle's interface change a little over time. The ones below are approximate as of November 2026.

1. Menu → **Compute → Instances → Create instance**.
2. **Image:** Canonical **Ubuntu** (22.04 or 24.04).
3. **Shape:** **VM.Standard.E2.1.Micro** (it says "Always Free eligible" next to it). Ampere A1 is free too,
   but in most regions it fails with an "out of capacity" error.
4. **Networking:** create a new VCN and a **public subnet**, and keep **Assign a public IPv4 address** on.
5. **Add SSH keys → Generate a key pair for me → Save private key.** Keep the downloaded `.key` file. You
   cannot log in to the server without it.
6. Press **Create**. When the status is **Running**, note the **Public IP address** on the page
   (for example `203.0.113.10`).

## 3. Open the port (Oracle's own firewall)

1. On the server's page, click the subnet link → **Security Lists → Default Security List**.
2. **Add Ingress Rules:**
   - Source CIDR: `0.0.0.0/0`
   - IP Protocol: `TCP`
   - Destination Port Range: `47800`
3. Save with **Add Ingress Rules**.

The setup script opens the firewall inside the server by itself.

## 4. Install (from your own computer, one command)

On Windows, open **Git Bash** in the repository folder (a normal terminal on macOS or Linux) and run:

```bash
bash server/deploy/deploy.sh ubuntu@PUBLIC_IP ~/Downloads/ssh-key-XXXX.key
```

Replace `PUBLIC_IP` with the address from step 2 and the key with the path of the file you downloaded. If the
first connection asks "Are you sure you want to continue connecting", you do not need to answer. The script
accepts it automatically.

At the end a box like this appears:

```
 Collab 4D server is running.
 Port:     47800
 Password: Xy7...(16 characters)
```

To choose the password yourself: `... deploy.sh ubuntu@IP key.key --password MyPassword`.
Running the command again later updates the server, and the password stays the same.

## 5. In Cinema 4D

- **Host address:** the server's Public IP
- **Port:** 47800
- **Password:** the password the setup printed
- Press **Join** (not Host session!)

The person who has the scene joins **first**, and the others join after.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `deploy.sh`: "cannot log in" | The address is wrong, the server is not Running yet, or the key file is a different one. The user name on Ubuntu is `ubuntu`. |
| Join: "cannot connect ... timed out" | The port 47800 rule from step 3 is missing. |
| Join: "wrong password" | Type the password with the right upper and lower case. After 5 wrong attempts that IP waits 60 seconds. |
| See the server's log | `ssh -i key.key ubuntu@IP` then `sudo journalctl -u collab4d -f` |
| Restart the server | `sudo systemctl restart collab4d` |
| Change the password | `deploy.sh ... --password NewPassword` |

## Other options

- **Google Cloud free tier (e2-micro):** only in US regions (Oregon, Iowa, South Carolina), so the delay is
  higher from Europe or Turkey. The same `deploy.sh` works. On Google the user name is your account name.
- **Any Linux server:** Ubuntu, Debian, Oracle Linux, Python 3.6 or newer. The same `deploy.sh` works.
- **Docker:** `server/Dockerfile` and `server/docker-compose.yml` are still here.