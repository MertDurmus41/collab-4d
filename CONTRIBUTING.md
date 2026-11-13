# Contributing to Collab 4D

Thanks for helping. Bug reports, test results from other Cinema 4D versions, documentation fixes and pull requests are all welcome.

## Before you start

- Search the [existing issues](https://github.com/MertDurmus41/collab-4d/issues) first.
- For a bigger change, open an issue or a [discussion](https://github.com/MertDurmus41/collab-4d/discussions) before you write code, so we agree on the approach.
- Questions about using the plugin belong in Discussions, not in issues.

## Reporting results from a Cinema 4D version we have not tested

Most versions are listed as untested in the [README](README.md#supported-cinema-4d-versions). If you have one, run the steps in [tests/c4d_manual/CHECKLIST.md](tests/c4d_manual/CHECKLIST.md) and open an issue with the Cinema 4D version, your operating system and what happened. This is one of the most useful things you can do.

## Development setup

You need Python 2.7 or 3.x. The tests do not need Cinema 4D.

```bash
git clone https://github.com/MertDurmus41/collab-4d.git
cd collab-4d
python -m unittest discover -s tests/core -v
python -m unittest discover -s tests/adapter -v
python -m unittest discover -s tests/compat -v
```

The website in `website/` needs Node.js 20 or newer:

```bash
cd website
npm ci
npm run dev
```

## Code rules

The fixed rules are listed in [ARCHITECTURE.md](ARCHITECTURE.md#değişmez-kurallar) (in Turkish). In short:

1. `core/` never imports `c4d`. That keeps it testable without Cinema 4D and lets the same server run anywhere.
2. **Plugin code runs on Python 2.7 and 3.x.** Cinema 4D R17 to R22 still use Python 2.7, so no f-strings, type hints, `async`, `nonlocal`, `pathlib` or `selectors`. Every file starts with `from __future__ import absolute_import, division, print_function, unicode_literals`. The tests in `tests/compat` check this.
3. **A Python file with non-ASCII characters needs a `# -*- coding: utf-8 -*-` first line**, otherwise Cinema 4D on Python 2.7 will not load the plugin.
4. Standard library only: no `pip` packages, no `pickle` or `eval`.
5. The scene is only changed from the main thread. The network thread only writes to a queue and wakes the main thread with `SpecialEventAdd`.
6. Remote changes are not written to the undo history.
7. Every Cinema 4D API that differs between versions is called through `c4d_adapter/compat.py`.

Also:

- Add or update tests for what you change. `tests/adapter/fake_scene.py` lets you test scene code without Cinema 4D.
- Keep the panel text in English.
## Pull requests

1. Fork the repository and create a branch from `main`.
2. Keep the pull request small and about one thing.
3. Make sure the three test commands above pass.
4. Describe what changed and why, and link the issue (`Closes #123`).
5. Add a line to [CHANGELOG.md](CHANGELOG.md) under *Unreleased* when users will notice the change.

Commit messages are short sentences in the present tense, for example `Fix the guest losing skin weights after a join`.

## Conduct

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).