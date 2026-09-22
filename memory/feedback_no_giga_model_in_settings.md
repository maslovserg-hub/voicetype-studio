---
name: GigaAM model is NEVER exposed in Settings UI
description: Hard rule for VoiceType Studio — никаких UI-переключателей для ASR-модели; зашитая v3_e2e_ctc одна на всех
type: feedback
originSessionId: fe63cb54-151d-4c19-9782-ed89599a2d68
---
В окне настроек (и где-либо ещё в UI) НЕ должно быть выбора ASR-модели GigaAM. `gigaam_model` остаётся жёстко зашитым в `core/config.py` (`v3_e2e_ctc`).

**Why:** так договорились заранее. В spec и `core/config.py` уже стоит комментарий «Hard-coded model. Settings UI does NOT expose this — all users use the punctuated v3_e2e_ctc to keep saved transcripts comparable». Пользователь напомнил повторно во время Этапа 5 — значит этот инвариант реально важный, не разовое дизайн-решение.

**How to apply:** при добавлении/правке любого UI (settings_window, transcriptor_window, tray, будущие окна) — не предлагать dropdown / radio / entry для GigaAM-модели. Если просьба возникнет — переспросить, действительно ли пользователь хочет нарушить договорённость, не делать молча. То же самое распространяется и на спрятанные настройки в `settings.json` — поле `gigaam_model` в `Settings` dataclass добавлять нельзя.
