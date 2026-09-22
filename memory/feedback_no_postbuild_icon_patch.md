---
name: feedback-no-postbuild-icon-patch
description: Never patch icon resources in a built PyInstaller exe after the fact — always do a full rebuild via VoiceTypeStudio.spec
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 43902ec8-ea9c-4a90-8726-b0dedbd3ba64
---

Never use `PyInstaller.utils.win32.icon.CopyIcons_FromIco` (or any
`BeginUpdateResource`/`EndUpdateResource` flow) on an **already built**
PyInstaller onedir exe to swap the icon. The Windows resource-update API
rewrites the PE and discards everything appended after it — that includes
the PyInstaller CArchive (PKG). Result: the exe shrinks to ~450 KB (bare
bootloader) and at launch shows
"Could not load PyInstaller's embedded PKG archive from the executable".

**Why:** Lost about 30 minutes on 2026-05-13 trying to shortcut an icon
refresh; broke the installed exe at `%APPDATA%\VoiceTypeStudio\`. The user
got the PyInstaller PKG error on next launch.

**How to apply:**
- The ONLY supported way to change the icon in a built exe is a full
  rebuild: `venv\Scripts\pyinstaller VoiceTypeStudio.spec --noconfirm`
  (PyInstaller does `Copying icon to EXE` *before* `Appending PKG archive`,
  so the order is safe).
- After rebuild, sync `dist/VoiceTypeStudio/` → `%APPDATA%\VoiceTypeStudio\`
  with `robocopy /MIR` on `_internal/` and a plain Copy-Item for the exe.
- If the Start-menu / taskbar icon still looks stale, the cause is Windows
  icon cache, not the exe — clear `IconCache.db` + `iconcache_*.db` +
  `thumbcache_*.db` under `%LOCALAPPDATA%` and restart Explorer.
- Related: [[project_session_2026-05-11]] mentioned `dist/ устарел и ждёт
  пересборки` — that was the right call; resist the temptation to patch.
