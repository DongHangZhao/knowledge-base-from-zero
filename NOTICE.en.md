NOTICE

[简体中文](NOTICE) ｜ **English**

knowledge-base-from-zero — a knowledge base you build from scratch
Copyright (c) 2026 FallenPetal
License: KBL-1.0 (Knowledge Base From Zero Open License v1.0)
         See LICENSE (English) / LICENSE.zh-CN.md (Chinese; Chinese prevails on conflict)

This file covers three things: how to attribute, what this repository contains
(and deliberately does not contain), and which licenses apply to third-party
components.


================================================================
1. Attribution templates (copy and use)
================================================================

For use in this project's README, at the end of an article, in a video
description, on a course landing page, or in a derivative project's About page.

[Template A · Repository / project README]

  > This project is built on [knowledge-base-from-zero](original repository URL),
  > by FallenPetal, licensed under KBL-1.0 (non-commercial).
  > Modified: <one sentence describing your changes>.   <- derivatives only

[Template B · Article / blog / newsletter]

  This method is adapted from FallenPetal's "Build Your Knowledge Base From Zero"
  (knowledge-base-from-zero, KBL-1.0, original repository URL).

[Template C · Video / course / livestream]

  State on screen or in the description:
  Method source: FallenPetal · Build Your Knowledge Base From Zero · KBL-1.0
  <original repository URL>

[Template D · Software / app "About" page]

  This software includes code from knowledge-base-from-zero,
  Copyright (c) 2026 FallenPetal, used under KBL-1.0.
  <original repository URL>

Hard requirements (per Section 3.2 of the license):
  · project name + author name + license name + original URL — all four;
  · placed where readers can see it; hidden source-code comments do not count;
  · derivative works must say "modified" and summarize the change;
  · must not imply that the original author endorses you.


================================================================
2. What this repository contains / deliberately does not contain
================================================================

Contains (all methodology and runnable code):
  · kb-hub/            the knowledge base hub (Python standard library + vanilla JS, zero third-party deps)
  · templates/         blank templates for AGENTS.md, wiki, modules and prompt assets
  · docs/              architecture, setup steps, skill workbench, collaboration, dual pipeline, licensing
  · kb-hub/inventory.example.json   a purely fictional example inventory (6 modules / 8 cards)

Deliberately does not contain (verified item by item before release, see tools/privacy_scan.py):
  · the author's real name, student ID, contact details, accounts, keys, tokens or cookies;
  · real project, client or company names, or employment details;
  · chat logs, address books, emails, calendars, photos, audio, video or any media original;
  · a real knowledge-base inventory (inventory.json is git-ignored and not in the repo);
  · any absolute local path (examples use the ~/my-knowledge-base placeholder).

If you find content that should not be here, please open an issue and it will be
removed immediately.


================================================================
3. Third-party component licenses
================================================================

The source code itself has zero third-party Python dependencies (standard library
only). The following applies only to the prebuilt kb-hub.exe distributed in
Releases:

  · Python runtime
      Python Software Foundation License Version 2
      https://docs.python.org/3/license.html
      Note: PyInstaller --onefile bundles the Python interpreter into the exe.

  · PyInstaller (build tool, used only at build time; its bootloader is embedded)
      Dual-licensed: GNU GPL 2.0 (with a bundling exception) + Apache License 2.0 (a few files)
      https://pyinstaller.org/en/stable/license.html
      Note: the official exception states that executables produced with it may be
            distributed under any license you choose, without shipping PyInstaller's
            license file and without crediting it in an "About" box.
            Therefore the original code inside kb-hub.exe remains under KBL-1.0;
            if you modify and distribute PyInstaller's own source, that part stays GPL.

  · PyInstaller build-time dependencies (build only, not distributed)
      packaging (Apache-2.0 OR BSD-2-Clause), altgraph (MIT),
      pefile (MIT), pywin32-ctypes (BSD-3-Clause)

  · Frontend
      Vanilla HTML/CSS/JavaScript. No frameworks, no CDN links, no remote fonts.

  · Documentation
      Plain Markdown. No third-party themes or templates.

KBL-1.0 governs only the author's original work; the third-party components above
are not restricted by KBL-1.0, but you must still comply with their own licenses.


================================================================
4. Commercial licensing
================================================================

Commercial use is prohibited (see Section 4 of the LICENSE). Obtain written
permission first: open an issue describing your organization, intended use,
scale, and whether you will modify the work.

Unauthorized commercial use terminates your rights automatically under Section 6.


================================================================
5. Disclaimer
================================================================

This work is provided "as is", without warranty of any kind. Use at your own risk.
The licensor is not liable for any direct or indirect damages arising from its use.


— END OF NOTICE —
