# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-14

First public version. Early development: tested live on Cinema 4D R19 and 2023.

### Added

- Collab 4D panel inside Cinema 4D to host or join a session with a password.
- Live sync of objects, modeling, animation and materials between several people in one scene.
- Rig sync: XPresso graphs, Pose Morph, IK, skin and deformers.
- Object locks: selecting an object locks it to you, and everyone sees who is editing what.
- Presence: a colour for every user, a user list with Cinema 4D version and current frame.
- Standalone session server that runs without Cinema 4D, with a Dockerfile and a one-command setup for a free cloud machine.
- The panel shows the host's Tailscale address, and a guide for working from different homes (`docs/internet.md`).
- Optional MCP page so an AI assistant can drive a live Cinema 4D (off by default).
- Compatibility checks for Cinema 4D R17 to 2026 with fake version profiles.
- Project website in `website/`.

[Unreleased]: https://github.com/MertDurmus41/collab-4d/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/MertDurmus41/collab-4d/releases/tag/v0.1.0