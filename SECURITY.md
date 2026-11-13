# Security Policy

## Supported versions

Collab 4D is in early development. Only the latest release and the `main` branch receive security fixes.

| Version | Supported |
|---|---|
| 0.1.x | Yes |
| Older | No |

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Report it privately through GitHub: open the [Security tab](https://github.com/MertDurmus41/collab-4d/security/advisories/new) and choose *Report a vulnerability*. Include what you found, how to reproduce it, and the Cinema 4D version and operating system.

You can expect a first answer within 7 days. We will tell you when the problem is fixed and credit you in the release notes if you want.

## Things to know when you use Collab 4D

- **Session traffic is not encrypted.** The password protects who can join, but the data between computers travels as plain network traffic. Over the internet, connect through [Tailscale](docs/internet.md) or another VPN, which encrypts it for you.
- **Do not expose the session port to the open internet** without a VPN or another protection in front of it.
- The optional MCP page lets an AI assistant control Cinema 4D. It is off by default, only accepts connections from the same computer, and needs its access token. Leave it off unless you use it.
- Never share the session password in a public place.