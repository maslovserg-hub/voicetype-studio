"""Живые субтитры для видеозвонков: RU <-> EN, двусторонний перевод.

Слушает два независимых потока — системный звук (речь собеседника) и микрофон
(ваша речь). Оба стримятся в Deepgram, который отдаёт текст ещё до того, как
человек договорил. Готовые фразы переводит DeepSeek.

Перенесено из отдельного приложения live-subtitles одним модулем. Окно —
tk.Toplevel от корня VoiceType; точка входа — :func:`open_window`.
"""
import ctypes
import json
import os
import queue
import random
import socket as socket_module
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

import numpy as np
import soundcard as sc
from PIL import Image, ImageDraw, ImageFont, ImageTk
from deepgram import DeepgramClient

from core import settings as settings_io
from core.assets import icon_ico_path
from core.config import config

APP_NAME = "Живые субтитры"

SAMPLE_RATE = 16000
BLOCK = 1600  # 100 мс

# Экономия: в Deepgram уходит только звук, где кто-то говорит.
CALIBRATION_BLOCKS = 15  # 1.5 с замера фонового шума на старте
NOISE_FACTOR = 4.0
MIN_THRESHOLD = 0.005
# Хвост тишины должен быть длиннее utterance_end_ms (1000 мс), иначе Deepgram
# не успеет заметить паузу и не пришлёт UtteranceEnd.
TAIL_BLOCKS = 12  # 1.2 с
KEEPALIVE_EVERY = 5.0  # сек: пинг в тишине, иначе сокет отвалится

# На перевод отдаём законченное предложение, а не обрывок по паузе: перевести
# «как всё это» в отрыве от остального нельзя, получится ерунда.
SENTENCE_ENDINGS = (".", "!", "?", "…")
# Предохранители, чтобы не копить бесконечно, если знак так и не пришёл.
MAX_PENDING_CHARS = 300
MAX_PENDING_SECONDS = 8.0
# Реплики одного говорящего подряд склеиваются в один блок. Новый блок —
# когда заговорил другой или после долгого молчания.
TURN_GAP_SECONDS = 25.0
MIN_PHRASE_CHARS = 4  # короче — это обрывок, перевод вернёт пустоту
TRANSLATE_TIMEOUT = 15  # сек на запрос перевода; дольше ждать в разговоре бессмысленно

DEBUG = False  # включается ключом --debug: печатать распознанное в консоль

# Журнал сессии: пишется в config.data_dir, перезаписывается при каждом открытии окна.
# Пока окно не открыто, путь не задан и в файл ничего не пишется (так же в тестах).
LOG_PATH: str | None = None
LOG_EVERY = 5.0  # раз в сколько секунд писать сводку по уровню звука
_log_lock = threading.Lock()


def log(message: str):
    line = f"{time.strftime('%H:%M:%S')}  {message}"
    if LOG_PATH:
        with _log_lock:
            try:
                with open(LOG_PATH, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass
    if DEBUG:
        print(line, flush=True)

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
FALLBACK_DEEPSEEK_MODELS = ["deepseek-chat"]


@dataclass
class Stream:
    key: str
    title: str  # как называется в статусе и журнале
    who: str  # подпись говорящего в ленте


STREAMS = {
    "them": Stream("them", "Собеседник", "Собеседник"),
    "me": Stream("me", "Вы", "Вы"),
}


MY_LANGUAGE = "русский"

# Языки, которые Deepgram понимает в режиме language=multi. Русский здесь не
# нужен — это язык пользователя, он определяется по кириллице.
PARTNER_LANGUAGES = [
    "английский", "немецкий", "хинди", "испанский", "французский",
    "итальянский", "португальский", "японский", "нидерландский",
]
DEFAULT_PARTNER = "английский"


def has_cyrillic(text: str) -> bool:
    return any("а" <= char <= "я" or char == "ё" for char in text.lower())


def detect_direction(text: str, partner: str = DEFAULT_PARTNER) -> tuple[str, str]:
    """Куда переводить эту фразу.

    Различать языки собеседника между собой не нужно: важно лишь, наша это
    речь или его. Кириллица — значит говорит пользователь, переводим на язык
    собеседника; всё остальное, на каком бы языке ни было, переводим на русский.

    Метке языка от Deepgram не доверяем: на коротких словах она врёт
    (например, «Да.» приходит как португальский).
    Возвращает (язык перевода, подпись направления).
    """
    if has_cyrillic(text):
        return partner, f"{MY_LANGUAGE} → {partner}"
    return MY_LANGUAGE, f"→ {MY_LANGUAGE}"


class Settings:
    """Язык собеседника и куда идёт его звук. Живёт в config.data_dir."""

    def __init__(self):
        self.path = str(config.data_dir / "live_subtitles.json")
        self.partner = DEFAULT_PARTNER
        self.speakers = True  # по умолчанию считаем, что звук идёт в динамики
        self.on_top = True  # окно поверх Zoom — ради этого оно и нужно
        try:
            with open(self.path, encoding="utf-8") as handle:
                saved = json.load(handle)
            if saved.get("partner") in PARTNER_LANGUAGES:
                self.partner = saved["partner"]
            self.speakers = bool(saved.get("speakers", True))
            self.on_top = bool(saved.get("on_top", True))
        except (OSError, ValueError, AttributeError):
            pass  # нет файла или он битый — берём значения по умолчанию

    def set_partner(self, language: str):
        self.partner = language
        self._save()

    def set_speakers(self, speakers: bool):
        self.speakers = speakers
        self._save()

    def set_on_top(self, on_top: bool):
        self.on_top = on_top
        self._save()

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump({"partner": self.partner, "speakers": self.speakers,
                           "on_top": self.on_top}, handle, ensure_ascii=False)
        except OSError:
            pass  # не смогли сохранить — на этой сессии всё равно работает


class EchoGate:
    """Не даёт распознать речь собеседника дважды.

    Из динамиков его голос попадает обратно в микрофон, и та же фраза уходит
    в Deepgram второй раз — уже как речь пользователя. Поэтому пока собеседник
    говорит, микрофон не слушаем.

    В наушниках этого не происходит, а подавление там только мешает: пользователь
    не смог бы вставить слово. Отсюда переключатель в настройках.
    """

    HOLD = 0.6  # сек после его последнего звука: эхо в микрофоне запаздывает

    def __init__(self, settings: Settings):
        self.settings = settings
        self.loud_until = 0.0

    def heard_partner(self):
        self.loud_until = time.time() + self.HOLD

    def blocks_microphone(self) -> bool:
        return self.settings.speakers and time.time() < self.loud_until


def rms(block: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(block)))) if block.size else 0.0


def to_pcm(block: np.ndarray) -> bytes:
    """float32 [-1..1] -> PCM 16 бит little-endian."""
    return (np.clip(block, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def stream_audio(recorder_factory, stream: Stream, socket, stop: threading.Event,
                 muted: threading.Event, status, meter=None, echo=None):
    """Пишет звук и шлёт в Deepgram только те куски, где есть речь.

    meter(уровень, порог) — если передан, вызывается для индикатора в окне.
    echo — общий на оба потока EchoGate, глушит микрофон, пока говорит собеседник.
    """
    try:
        with recorder_factory().recorder(samplerate=SAMPLE_RATE, channels=1) as rec:
            noise = []
            threshold = MIN_THRESHOLD
            tail = 0
            last_ping = time.time()
            last_report = time.time()
            peak = 0.0
            sent = 0
            blocked = 0

            while not stop.is_set():
                block = rec.record(numframes=BLOCK)
                if block.ndim > 1:
                    block = block.mean(axis=1)

                if len(noise) < CALIBRATION_BLOCKS:
                    noise.append(rms(block))
                    if len(noise) == CALIBRATION_BLOCKS:
                        threshold = max(float(np.mean(noise)) * NOISE_FACTOR, MIN_THRESHOLD)
                        log(f"{stream.title}: порог тишины {threshold:.4f}")
                    continue

                level = rms(block)
                suppressed = False
                if echo is not None:
                    if stream.key == "them":
                        if level > threshold:
                            echo.heard_partner()
                    elif echo.blocks_microphone():
                        suppressed = True

                if meter is not None:
                    # пока микрофон заглушён, показываем ноль: полоски не должны
                    # прыгать зелёным на звуке, который никуда не уходит
                    meter(0.0 if suppressed else level, threshold)

                if muted.is_set() or suppressed:
                    tail = 0
                elif level > threshold:
                    tail = TAIL_BLOCKS
                elif tail > 0:
                    tail -= 1

                if tail > 0:
                    socket.send_media(to_pcm(block))
                    last_ping = time.time()
                    sent += 1
                elif time.time() - last_ping > KEEPALIVE_EVERY:
                    socket.send_keep_alive()
                    last_ping = time.time()

                peak = max(peak, level)
                blocked += suppressed
                if time.time() - last_report >= LOG_EVERY:
                    log(f"{stream.key}: пик {peak:.4f} при пороге {threshold:.4f}, "
                        f"отправлено блоков {sent}"
                        + (f", заглушено эхом {blocked}" if blocked else "")
                        + (" — ЗВУКА НЕТ ВООБЩЕ" if peak == 0.0 else ""))
                    peak, sent, blocked = 0.0, 0, 0
                    last_report = time.time()
    except Exception as exc:  # noqa: BLE001
        log(f"ОШИБКА захвата ({stream.key}): {exc!r} — поток остановлен, "
            "полоска уровня замрёт")
        status(f"ОШИБКА захвата ({stream.title}): {exc}")


def read_transcripts(socket, stream: Stream, jobs: queue.Queue, ui: queue.Queue,
                     stop: threading.Event):
    """Черновики шлёт сразу в интерфейс, готовые фразы — на перевод."""
    pending: list[str] = []
    pending_since = 0.0

    def flush():
        nonlocal pending, pending_since
        if not pending:
            return
        text = " ".join(pending).strip()
        pending, pending_since = [], 0.0
        # обрывки вроде "The" переводить бессмысленно: вернётся пустота,
        # а запрос к API уже оплачен
        if len(text) >= MIN_PHRASE_CHARS:
            # Распознавание закончено, перевод ещё идёт. Показываем текст сразу:
            # если стереть его до прихода перевода, реплика на секунду исчезает
            # и кажется, что программа сломалась.
            ui.put(("pending", stream.key, text, "", ""))
            jobs.put((stream, text))
        else:
            ui.put(("live", stream.key, "", "", ""))

    try:
        for message in socket:
            if stop.is_set():
                break
            # Deepgram шлёт UtteranceEnd отдельным сообщением без текста.
            # Это страховка: если speech_final не пришёл, фразу закрываем по нему.
            # UtteranceEnd = человек замолчал. Закрываем реплику, только если она
            # выглядит законченной, иначе пауза посреди фразы рвала бы её надвое.
            if getattr(message, "type", "") == "UtteranceEnd":
                joined = " ".join(pending).rstrip()
                stale = pending_since and time.time() - pending_since >= MAX_PENDING_SECONDS
                if joined.endswith(SENTENCE_ENDINGS) or stale:
                    flush()
                continue

            channel = getattr(message, "channel", None)
            if channel is None:
                continue
            alternatives = getattr(channel, "alternatives", None) or []
            if not alternatives:
                continue
            text = (getattr(alternatives[0], "transcript", "") or "").strip()

            if not getattr(message, "is_final", False):
                if text:
                    ui.put(("live", stream.key, text, "", ""))
                continue

            if text:
                if not pending:
                    pending_since = time.time()
                pending.append(text)

            # ждём конца предложения, а не любой паузы: человек делает паузы
            # и в середине фразы, а переводить обрывок бессмысленно
            joined = " ".join(pending).rstrip()
            too_long = len(joined) >= MAX_PENDING_CHARS
            too_slow = pending_since and time.time() - pending_since >= MAX_PENDING_SECONDS
            if joined.endswith(SENTENCE_ENDINGS) or too_long or too_slow:
                flush()
    except Exception as exc:  # noqa: BLE001
        log(f"ОШИБКА распознавания ({stream.key}): {exc!r} — соединение с Deepgram "
            "оборвалось, текста больше не будет")
        ui.put(("status", "", "", f"ОШИБКА распознавания ({stream.title}): {exc}", ""))
        ui.put(("service", "Распознавание", "fail", "", ""))


class Translator:
    """DeepSeek: текст на одном языке -> текст на другом."""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.models = [model] + [m for m in FALLBACK_DEEPSEEK_MODELS if m != model]
        # deepseek-v4-flash по умолчанию «размышляет»: на перевод одной фразы
        # уходит под 300 токенов рассуждений, ответ идёт вдвое дольше, а когда
        # рассуждения съедают весь лимит — content возвращается пустым.
        self.no_thinking = True

    def _request(self, model: str, prompt: str) -> str:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 500,
            "stream": False,
        }
        if self.no_thinking:
            body["thinking"] = {"type": "disabled"}
        request = urllib.request.Request(
            DEEPSEEK_URL, data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=TRANSLATE_TIMEOUT) as response:
            data = json.loads(response.read())
        return (data["choices"][0]["message"].get("content") or "").strip()

    def __call__(self, dst: str, text: str) -> str:
        prompt = (
            f"Переведи реплику из живого разговора на {dst}. "
            "Ответь только переводом, без пояснений и кавычек. "
            "Сохрани разговорный тон.\n\nРеплика: " + text
        )
        last_error = None
        for model in list(self.models):
            for attempt in range(2):  # вторая попытка — на случай обрыва связи
                try:
                    result = self._request(model, prompt)
                    # удачная модель — используем её первой в следующий раз
                    self.models = [model] + [m for m in self.models if m != model]
                    return result
                except urllib.error.HTTPError as exc:
                    detail = exc.read()[:200].decode("utf-8", "replace")
                    last_error = f"{exc.code} {detail}"
                    # модель не понимает отключение размышлений — попробуем без него
                    if self.no_thinking and "thinking" in detail.lower():
                        self.no_thinking = False
                        continue
                    break
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    last_error = f"сеть: {exc}"
        raise RuntimeError(last_error or "перевод недоступен")


def translate_worker(jobs: queue.Queue, ui: queue.Queue, translate: Translator,
                     stop: threading.Event, settings: Settings):
    while not stop.is_set():
        try:
            stream, text = jobs.get(timeout=0.3)
        except queue.Empty:
            continue
        dst, caption = detect_direction(text, settings.partner)
        try:
            translated = translate(dst, text)
            if not translated:
                translated = "—"  # чтобы под оригиналом не было пустоты
            log(f"[{stream.key}] {caption}: {text}  ->  {translated}")
            ui.put(("line", stream.key, text, translated, caption))
            ui.put(("service", "Перевод", "ok", "", ""))
        except Exception as exc:  # noqa: BLE001
            log(f"ОШИБКА перевода: {exc!r}")
            ui.put(("status", "", "", f"ОШИБКА перевода: {exc}", ""))
            ui.put(("service", "Перевод", "fail", "", ""))


# --- интерфейс ------------------------------------------------------------
# Оформление в духе macOS: тёмные слои, один акцентный цвет, минимум рамок.
# Переключатели, индикаторы и иконки рисуются через Pillow и показываются
# картинкой: Canvas у Tk рисует без сглаживания, и круги выходят лесенкой.

BG = "#1c1c1e"
CARD = "#2c2c2e"
ROW_LINE = "#3a3a3c"
LABEL = "#ffffff"
SECONDARY = "#9a9aa0"
TERTIARY = "#6c6c70"
ACCENT = "#0a84ff"
GREEN = "#30d158"
TRACK_OFF = "#39393d"
KNOB = "#ffffff"
PRESSED = "#3a3a3c"

FONT_FAMILY = "Segoe UI"


def pick_font_family():
    """Segoe UI Variable — системный шрифт Windows 11, у него лучше рисунок."""
    global FONT_FAMILY
    from tkinter import font as tkfont

    families = set(tkfont.families())
    for name in ("Segoe UI Variable Text", "Segoe UI"):
        if name in families:
            FONT_FAMILY = name
            return


def font(size: int, weight: str = "normal", slant: str = "roman"):
    return (FONT_FAMILY, size, weight, slant)


def blend(color_a: str, color_b: str, k: float) -> str:
    """Плавный переход цвета: без него переключатель менял бы цвет рывком."""
    a = [int(color_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(color_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * k):02x}" for x, y in zip(a, b))


# Размеры ниже заданы для обычного экрана; при системном масштабе 125/150%
# всё умножается на SCALE, иначе иконки и переключатели выглядят игрушечными
# рядом с подписями, которые Tk увеличивает сам.
SCALE = 1.0
SUPERSAMPLE = 4  # рисуем крупнее и уменьшаем — так получается сглаживание

FONTS_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
ICON_FONTS = ("SegoeIcons.ttf",  # Windows 11, Segoe Fluent Icons
              "segmdl2.ttf")     # Windows 10, Segoe MDL2 Assets
# коды глифов одинаковые в обоих наборах
GLYPHS = {"mic": "\ue720", "speaker": "\ue767",
          "globe": "\ue774", "more": "\ue712"}


def px(value: float) -> int:
    return max(1, round(value * SCALE))


def load_font(names, size: int):
    for name in names:
        try:
            return ImageFont.truetype(os.path.join(FONTS_DIR, name), size)
        except OSError:
            continue
    return None


def paint(width: float, height: float, bg: str, painter) -> ImageTk.PhotoImage:
    """Рисует картинку с запасом по разрешению и уменьшает её со сглаживанием.

    painter получает холст и множитель k: рисовать надо в единицах макета,
    умножая координаты на k.
    """
    w, h = px(width), px(height)
    k = SUPERSAMPLE * SCALE
    image = Image.new("RGB", (round(width * k), round(height * k)), bg)
    painter(ImageDraw.Draw(image), k)
    return ImageTk.PhotoImage(image.resize((w, h), Image.LANCZOS))


def glyph(kind: str, size: float, color: str, bg: str,
          box: float | None = None) -> ImageTk.PhotoImage:
    """Иконка системным шрифтом Windows — рисованные вручную дуги рядом не стоят."""
    side = box if box is not None else size

    def draw_glyph(draw, k):
        icon = load_font(ICON_FONTS, round(size * k * 0.98))
        if icon is not None:
            draw.text((side * k / 2, side * k / 2), GLYPHS[kind],
                      font=icon, fill=color, anchor="mm")

    return paint(side, side, bg, draw_glyph)


class Switch(tk.Label):
    """Переключатель как в iOS. Реагирует на нажатие, а не на отпускание."""

    W, H, PAD = 30, 17, 2
    DURATION = 0.18

    def __init__(self, parent, value=True, command=None, bg=CARD):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.value = value
        self.command = command
        self.bg = bg
        self.position = 1.0 if value else 0.0
        self.bind("<Button-1>", self._pressed)
        self.draw()

    def draw(self):
        track = blend(TRACK_OFF, ACCENT, self.position)
        size = self.H - self.PAD * 2
        left = self.PAD + self.position * (self.W - self.PAD * 2 - size)

        def painter(draw, k):
            draw.rounded_rectangle((0, 0, self.W * k - 1, self.H * k - 1),
                                   radius=self.H * k / 2, fill=track)
            draw.ellipse((left * k, self.PAD * k,
                          (left + size) * k, (self.PAD + size) * k), fill=KNOB)

        self._photo = paint(self.W, self.H, self.bg, painter)
        self.configure(image=self._photo)

    def _pressed(self, _event):
        self.value = not self.value
        if self.command:
            self.command(self.value)
        self._animate()

    def _animate(self):
        target = 1.0 if self.value else 0.0
        start, started = self.position, time.time()

        def step():
            k = min(1.0, (time.time() - started) / self.DURATION)
            eased = 1 - (1 - k) ** 3  # гасится к концу, без перелёта
            self.position = start + (target - start) * eased
            self.draw()
            if k < 1.0:
                self.after(16, step)

        step()


class Meter(tk.Label):
    """Эквалайзер: полоски скачут в такт звуку, сразу видно, доходит он или нет."""

    BARS, BAR, GAP, H = 5, 3, 2, 14
    FALL = 0.16  # насколько полоска оседает за одно обновление, если звук стих
    W = BARS * BAR + (BARS - 1) * GAP

    def __init__(self, parent, bg=CARD):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0)
        self.bg = bg
        self.heights = [0.0] * self.BARS
        self.active = False
        self.draw()

    def set_level(self, level: float, threshold: float):
        # порог приходится на треть шкалы: виден и запас, и превышение
        fraction = max(0.0, min(1.0, level / (threshold * 3))) if threshold else 0.0
        active = level > threshold
        # разброс по полоскам — иначе они ходили бы одной сплошной ступенькой
        heights = [max(fraction * random.uniform(0.5, 1.0), old - self.FALL)
                   for old in self.heights]
        if heights == self.heights and active == self.active:
            return  # в тишине картинка не меняется — не тратим время на перерисовку
        self.heights, self.active = heights, active
        self.draw()

    def draw(self):
        color = GREEN if self.active else "#6a6a70"

        def painter(draw, k):
            for index, share in enumerate(self.heights):
                x = index * (self.BAR + self.GAP)
                height = max(self.BAR, share * self.H)  # минимум — точка, чтобы не пропадала
                draw.rounded_rectangle(
                    (x * k, (self.H - height) * k, (x + self.BAR) * k, self.H * k - 1),
                    radius=self.BAR * k / 2,
                    fill=color if share > 0.02 else TRACK_OFF)

        self._photo = paint(self.W, self.H, self.bg, painter)
        self.configure(image=self._photo)


class IconButton(tk.Label):
    """Иконка-кнопка. Подсвечивается в момент нажатия, действие — по отпусканию."""

    SIZE, GLYPH = 26, 15

    def __init__(self, parent, kind: str, command, bg=BG):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.kind = kind
        self.command = command
        self.bg = bg
        self.bind("<Button-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self._images = {}
        self.draw(False)

    def _press(self, _event):
        self.draw(True)

    def _release(self, _event):
        self.draw(False)
        self.command(self)

    def draw(self, held: bool):
        if held not in self._images:
            def painter(draw, k):
                if held:
                    draw.ellipse((0, 0, self.SIZE * k - 1, self.SIZE * k - 1),
                                 fill=PRESSED)
                icon = load_font(ICON_FONTS, round(self.GLYPH * k))
                if icon is not None:
                    draw.text((self.SIZE * k / 2, self.SIZE * k / 2), GLYPHS[self.kind],
                              font=icon, fill=LABEL, anchor="mm")

            self._images[held] = paint(self.SIZE, self.SIZE, self.bg, painter)
        self.configure(image=self._images[held])


class ServiceLight(tk.Frame):
    """Состояние одного сервиса: серый — ещё не отвечал, зелёный — работает,
    красный — последний запрос сорвался.

    Подпись — обычный tk.Label, чтобы шрифт был ровно тот же, что у названий
    источников звука; кружок рисуем картинкой ради сглаживания.
    """

    DOT = 8
    COLORS = {"wait": TERTIARY, "ok": GREEN, "fail": "#ff453a"}

    def __init__(self, parent, name: str):
        super().__init__(parent, bg=BG)
        self.state = "wait"
        self.dot = tk.Label(self, bg=BG, highlightthickness=0, bd=0)
        self.dot.pack(side="left")
        tk.Label(self, text=name, bg=BG, fg=LABEL,
                 font=font(10)).pack(side="left", padx=(5, 0))
        self.draw()

    def set_state(self, state: str):
        if state == self.state:
            return
        self.state = state
        self.draw()

    def draw(self):
        color = self.COLORS[self.state]

        def painter(draw, k):
            draw.ellipse((0, 0, self.DOT * k - 1, self.DOT * k - 1), fill=color)

        self._photo = paint(self.DOT, self.DOT, BG, painter)
        self.dot.configure(image=self._photo)


class SourceIcon(tk.Label):
    """Пиктограмма источника звука — глиф из системного набора Windows."""

    SIZE, GLYPH = 22, 15

    def __init__(self, parent, kind: str, bg=CARD):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0)
        self._photo = glyph(kind, self.GLYPH, SECONDARY, bg, box=self.SIZE)
        self.configure(image=self._photo)


class Popup(tk.Toplevel):
    """Выпадающее меню в стиле приложения.

    tk.Menu на Windows рисует система: толстая светлая рамка, квадратные углы,
    выгравированный текст. Настройками это не лечится, поэтому меню — обычное
    окно без рамки: тонкая линия в цвет интерфейса, углы скругляет Windows 11.
    """

    def __init__(self, master: tk.Widget):
        super().__init__(master, bg=ROW_LINE)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.body = tk.Frame(self, bg=CARD)
        self.body.pack(padx=px(1), pady=px(1))
        self.bind("<Escape>", lambda _: self.destroy())

    def header(self, text: str):
        """Подпись к группе пунктов. Линии под ней быть не должно: с линией
        подпись читается как отдельный пункт, а не как заголовок своей группы."""
        tk.Label(self.body, text=text, bg=CARD, fg=SECONDARY, font=font(9),
                 anchor="w").pack(fill="x", padx=px(14), pady=(px(10), px(3)))

    def separator(self):
        tk.Frame(self.body, bg=ROW_LINE, height=px(1)).pack(fill="x", pady=px(4))

    def item(self, text: str, command, checked: bool = False):
        row = tk.Label(self.body, text=("✓   " if checked else "     ") + text,
                       bg=CARD, fg=LABEL, font=font(10), anchor="w")
        row.pack(fill="x", padx=px(4), pady=px(1), ipadx=px(10), ipady=px(4))
        row.bind("<Enter>", lambda _: row.configure(bg=ACCENT))
        row.bind("<Leave>", lambda _: row.configure(bg=CARD))
        row.bind("<Button-1>", lambda _: (self.destroy(), command()))

    def show(self, x: int, y: int):
        self.update_idletasks()
        # если меню не влезает вниз (языков много), поднимаем его над кнопкой —
        # системное меню Windows так делало само
        height = self.winfo_reqheight()
        if y + height > self.winfo_screenheight():
            y = max(0, y - height - px(28))
        self.geometry(f"+{x}+{y}")
        round_corners(self)
        self.focus_force()
        # закрытие по уходу фокуса вешаем позже: сразу после показа окно ещё не
        # успевает его получить, и меню схлопнулось бы само
        self.after(300, lambda: self.bind("<FocusOut>", lambda _: self.destroy()))


def round_corners(window: tk.Toplevel):
    """Просит Windows 11 скруглить углы окна. На Windows 10 просто не сработает."""
    try:
        window.update_idletasks()
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            window.winfo_id(), 33, ctypes.byref(ctypes.c_int(2)), 4)  # CORNER_PREFERENCE
    except Exception:  # noqa: BLE001 — украшение, без него тоже работает
        pass


class App:
    def __init__(self, root: tk.Misc, ui: queue.Queue, stop: threading.Event,
                 mutes: dict[str, threading.Event], font_size: int,
                 settings: "Settings | None" = None, on_close=None):
        self.root = root
        self.on_close = on_close
        self.ui = ui
        self.stop = stop
        self.mutes = mutes
        self.font_size = font_size
        self.settings = settings or Settings()
        self.meters: dict[str, Meter] = {}
        self.live_texts: dict[str, str] = {key: "" for key in STREAMS}
        # распознано окончательно, но перевод ещё в пути
        self.pending: dict[str, list[str]] = {key: [] for key in STREAMS}
        self._settling = False
        self._settle_at = 0.0
        self.turn_key: str | None = None
        self.turn_caption = ""
        self.turn_at = 0.0
        self.turn_original = ""
        self.turn_translated = ""

        pick_font_family()
        global SCALE
        SCALE = max(1.0, root.winfo_fpixels("1i") / 96.0)  # системный масштаб экрана
        root.title(APP_NAME)
        root.configure(bg=BG)
        root.geometry("760x560")
        root.minsize(520, 420)
        root.attributes("-topmost", self.settings.on_top)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        try:
            root.iconbitmap(icon_ico_path())
        except tk.TclError:
            pass  # без иконки жить можно

        self._header()
        self._feed()
        self.poll()

    # --- сборка окна ---

    def _header(self):
        """Шапка: источники звука слева, состояние и меню справа.

        Названия приложения здесь нет — его уже показывает заголовок окна,
        а место лучше отдать переключателям.
        """
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=20, pady=(10, 4))

        IconButton(bar, "more", self._more_menu).pack(side="right")
        IconButton(bar, "globe", self._language_menu).pack(side="right", padx=(0, 2))

        self.services = {}
        for name in ("Перевод", "Распознавание"):  # пакуем справа налево
            light = ServiceLight(bar, name)
            light.pack(side="right", padx=(12, 0))
            self.services[name] = light

        sources = [("me", "Микрофон", "mic"),
                   ("them", "Собеседник", "speaker")]
        for index, (key, title, icon) in enumerate(sources):
            if index:
                tk.Frame(bar, bg=ROW_LINE, width=1, height=18).pack(
                    side="left", padx=10)

            SourceIcon(bar, icon, bg=BG).pack(side="left", padx=(0, 4))
            tk.Label(bar, text=title, bg=BG, fg=LABEL,
                     font=font(10)).pack(side="left")

            meter = Meter(bar, bg=BG)
            meter.pack(side="left", padx=6)
            self.meters[key] = meter

            Switch(bar, value=True, bg=BG,
                   command=lambda on, k=key: self.set_listening(k, on)).pack(side="left")

    def _feed(self):
        tk.Label(self.root, text="ЛЕНТА СУБТИТРОВ", bg=BG, fg=TERTIARY,
                 font=font(9, "bold"), anchor="w").pack(fill="x", padx=22, pady=(10, 0))
        # сюда попадают только ошибки: техническая диагностика уходит в журнал,
        # поэтому цвет и кегль нормальные, а не «мелким серым»
        self.status = tk.Label(self.root, text="", bg=BG, fg=SECONDARY,
                               font=font(10), anchor="w")
        self.status.pack(side="bottom", fill="x", padx=22, pady=(0, 12))

        self.feed = tk.Text(self.root, bg=BG, fg=LABEL, bd=0, highlightthickness=0,
                            wrap="word", padx=2, pady=4, state="disabled",
                            cursor="arrow", height=6, width=20,
                            selectbackground="#2f4a6d", insertwidth=0)
        self.feed.pack(fill="both", expand=True, padx=20, pady=(14, 6))
        # ниже этой метки лента ещё меняется: текущий блок и черновики
        self.feed.mark_set(self.TURN_START, "1.0")
        self.feed.mark_gravity(self.TURN_START, "left")
        self._apply_fonts()

        self.root.bind("<Control-plus>", lambda e: self.zoom(1))
        self.root.bind("<Control-equal>", lambda e: self.zoom(1))
        self.root.bind("<Control-minus>", lambda e: self.zoom(-1))

    def _apply_fonts(self):
        size = self.font_size
        self.feed.configure(font=font(size))
        self.feed.tag_configure("who", foreground=SECONDARY, spacing1=14, spacing3=2,
                                font=font(max(8, size - 4), "bold"))
        self.feed.tag_configure("orig", foreground=LABEL, spacing3=1, font=font(size))
        self.feed.tag_configure("trans", foreground=ACCENT, spacing3=4, font=font(size))
        # черновик: тот же кегль, что и готовая реплика, но приглушённый —
        # видно, что текст ещё уточняется, и не прыгает при закреплении
        self.feed.tag_configure("draft", foreground=SECONDARY, spacing3=1, font=font(size))
        # распознанное окончательно: цвет живёт своей жизнью, его ведёт _settle
        self.feed.tag_configure("settling", spacing3=1, font=font(size))

    # --- меню ---

    def _language_menu(self, widget: tk.Widget):
        menu = Popup(self.root)
        menu.header("Собеседник говорит на:")
        for language in PARTNER_LANGUAGES:
            menu.item(language, lambda l=language: self.settings.set_partner(l),
                      checked=language == self.settings.partner)
        menu.show(widget.winfo_rootx(),
                  widget.winfo_rooty() + widget.winfo_height() + px(4))

    def _more_menu(self, widget: tk.Widget):
        menu = Popup(self.root)
        menu.header("Собеседника слышно через")
        menu.item("Динамики", lambda: self.settings.set_speakers(True),
                  checked=self.settings.speakers)
        menu.item("Наушники", lambda: self.settings.set_speakers(False),
                  checked=not self.settings.speakers)
        menu.separator()
        menu.item("Поверх всех окон", self.toggle_on_top,
                  checked=self.settings.on_top)
        menu.separator()
        menu.header("Текст субтитров")
        menu.item("Крупнее   Ctrl +", lambda: self.zoom(1))
        menu.item("Мельче    Ctrl −", lambda: self.zoom(-1))
        menu.show(widget.winfo_rootx() - px(120),
                  widget.winfo_rooty() + widget.winfo_height() + px(4))

    # --- поведение ---

    def toggle_on_top(self):
        """Окно поверх Zoom нужно не всегда: при демонстрации экрана оно мешает."""
        self.settings.set_on_top(not self.settings.on_top)
        self.root.attributes("-topmost", self.settings.on_top)

    def set_listening(self, key: str, listening: bool):
        (self.mutes[key].clear if listening else self.mutes[key].set)()
        if not listening:
            self.live_texts[key] = ""
            self.render_live()

    def zoom(self, delta: int):
        self.font_size = max(9, min(30, self.font_size + delta))
        self._apply_fonts()

    def set_level(self, key: str, level: float, threshold: float):
        self.meters[key].set_level(level, threshold)

    def _paint_tail(self, with_drafts: bool = True):
        """Перерисовывает незакреплённый хвост ленты: текущий блок и черновики.

        Черновик того, кто говорит сейчас, дописывается бледным прямо за уже
        распознанным текстом — реплика растёт на месте, а не появляется отдельно
        снизу. Когда фраза закрепляется, бледный хвост просто становится белым.
        """
        self.feed.configure(state="normal")
        self.feed.delete(self.TURN_START, "end-1c")

        if self.turn_key is not None:
            stream = STREAMS[self.turn_key]
            self.feed.insert("end-1c", f"{stream.who}  ·  {self.turn_caption}\n", "who")
            self.feed.insert("end-1c", self.turn_original, "orig")
            if with_drafts:
                self._insert_tail(self.turn_key, bool(self.turn_original))
            self.feed.insert("end-1c", "\n", "orig")
            self.feed.insert("end-1c", self.turn_translated + "\n", "trans")

        if with_drafts:
            # заговорил не тот, чей сейчас блок — ему отдельная строка
            for key in STREAMS:
                if key == self.turn_key:
                    continue
                if self.pending[key] or self.live_texts[key]:
                    self.feed.insert("end-1c", f"{STREAMS[key].who}\n", "who")
                    self._insert_tail(key, False)
                    self.feed.insert("end-1c", "\n", "orig")

        self.feed.configure(state="disabled")
        self.feed.see("end")

    def _insert_tail(self, key: str, spaced: bool):
        """Дописывает распознанное: сначала уже окончательное, потом черновик."""
        settled = " ".join(self.pending[key])
        draft = self.live_texts[key]
        if settled:
            self.feed.insert("end-1c", (" " if spaced else "") + settled, "settling")
            spaced = True
        if draft:
            self.feed.insert("end-1c", (" " if spaced else "") + draft, "draft")

    def render_live(self):
        self._paint_tail()

    def _settle(self):
        """Распознанный текст плавно белеет из бледного.

        Без этого он менял бы цвет рывком, и было бы непонятно, что происходит.
        """
        self._settle_at = time.time()
        if self._settling:
            return  # анимация уже идёт, она подхватит новое время
        self._settling = True

        def step():
            if self.stop.is_set():
                self._settling = False
                return  # окно закрывается
            k = min(1.0, (time.time() - self._settle_at) / self.SETTLE_SECONDS)
            self.feed.tag_configure("settling", foreground=blend(SECONDARY, LABEL, k))
            if k < 1.0:
                self.root.after(16, step)
            else:
                self._settling = False

        step()

    def add_line(self, key: str, original: str, translated: str, caption: str = ""):
        """Реплика в ленте.

        Пока говорит один и тот же человек, фразы дописываются в тот же блок:
        оригинал к оригиналу, перевод к переводу.
        """
        if not caption:
            caption = detect_direction(original, self.settings.partner)[1]

        now = time.time()
        same_turn = (self.turn_key == key
                     and self.turn_caption == caption
                     and now - self.turn_at < TURN_GAP_SECONDS)

        if same_turn:
            self.turn_original = f"{self.turn_original} {original}".strip()
            self.turn_translated = f"{self.turn_translated} {translated}".strip()
        else:
            self._paint_tail(with_drafts=False)  # закрепляем предыдущий блок начисто
            self.feed.configure(state="normal")
            if int(self.feed.index("end-1c").split(".")[0]) > 300:
                self.feed.delete("1.0", "90.0")  # обрезаем только между блоками
            self.feed.mark_set(self.TURN_START, "end-1c")
            self.feed.mark_gravity(self.TURN_START, "left")
            self.feed.configure(state="disabled")
            self.turn_original, self.turn_translated = original, translated
            self.turn_key, self.turn_caption = key, caption

        self.turn_at = now
        self._paint_tail()

    def poll(self):
        while True:
            try:
                kind, key, original, translated, caption = self.ui.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.status.configure(text=translated)
            elif kind == "service":
                self.services[key].set_state(original)
            elif kind == "level":
                self.set_level(key, original, translated)
            elif kind == "live":
                self.live_texts[key] = original
                self.render_live()
            elif kind == "pending":
                # распознано окончательно, ждём перевод: текст остаётся на месте
                self.live_texts[key] = ""
                self.pending[key].append(original)
                self.render_live()
                self._settle()
            else:
                self.live_texts[key] = ""
                try:
                    self.pending[key].remove(original)
                except ValueError:
                    self.pending[key].clear()  # перевод не для той фразы — не копим мусор
                self.add_line(key, original, translated, caption)
        if not self.stop.is_set():
            self.root.after(100, self.poll)

    def quit(self):
        if self.on_close is not None:
            self.on_close()  # окно закрывает владелец: ему же останавливать потоки
            return
        self.stop.set()
        self.root.after(150, self.root.destroy)


App.TURN_START = "turn_start"  # метка начала текущего блока в ленте
App.SETTLE_SECONDS = 0.35  # за столько распознанный текст белеет из бледного


def drop_unreachable_proxy():
    """Убирает HTTP(S)_PROXY из окружения, если прокси не отвечает.

    Переменные могут указывать на локальный туннель; когда он выключен, клиент
    Deepgram падает с WinError 10061 ещё до выхода в интернет, хотя прямое
    подключение работает.
    """
    proxy = urllib.request.getproxies()
    url = proxy.get("https") or proxy.get("http")
    if not url:
        return
    address = urllib.parse.urlparse(url if "://" in url else f"http://{url}")
    try:
        socket_module.create_connection((address.hostname, address.port or 80),
                                        timeout=2).close()
        return
    except OSError as exc:
        log(f"прокси {address.hostname}:{address.port} не отвечает ({exc}) — "
            f"подключаемся напрямую")
    for name in ("http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(name, None)
        os.environ.pop(name.upper(), None)


# --- окно внутри VoiceType --------------------------------------------------

DEFAULT_DEEPGRAM_MODEL = "nova-3"
FONT_SIZE = 15
CLOSE_JOIN_SECONDS = 5.0  # сколько ждём остановки потоков при закрытии окна


def _close_socket(connection, socket):
    try:
        socket.send_close_stream()
        connection.__exit__(None, None, None)
    except Exception:  # noqa: BLE001 — при закрытии уже неважно
        pass


class LiveWindow:
    """Открытое окно субтитров вместе со всем, что к нему привязано:
    очереди, потоки захвата/распознавания/перевода и сокеты Deepgram.

    Закрытие окна не трогает VoiceType: останавливаются только свои потоки,
    потом уничтожается свой Toplevel.
    """

    def __init__(self, parent, deepgram_key: str, deepseek_key: str,
                 sources: dict, model: str, translate_model: str):
        self.deepgram_key = deepgram_key
        self.deepseek_key = deepseek_key
        self.sources = sources
        self.model = model
        self.translate_model = translate_model

        self.stop = threading.Event()
        self.jobs: queue.Queue = queue.Queue()
        self.ui: queue.Queue = queue.Queue()
        self.mutes = {key: threading.Event() for key in STREAMS}
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._sockets: list[tuple] = []
        self._closing = False
        self._cleaner: threading.Thread | None = None

        # Сначала показываем окно, затем устанавливаем сетевые соединения.
        # Иначе медленный ответ Deepgram выглядит как будто приложение не запустилось.
        self.settings = Settings()
        self.echo = EchoGate(self.settings)
        log("создание окна")
        self.top = tk.Toplevel(parent)
        self.top.bind("<Destroy>", self._on_destroy)
        self.app = App(self.top, self.ui, self.stop, self.mutes, FONT_SIZE,
                       self.settings, on_close=self.close)
        log("окно создано")
        log("подключение к Deepgram…")  # в окне это видно по серым индикаторам
        self.top.update_idletasks()
        self._spawn("connect", self._connect_services)

    # --- состояние ---

    @property
    def closing(self) -> bool:
        return self._closing

    def alive(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False

    def raise_window(self):
        self.top.deiconify()
        self.top.lift()
        self.top.focus_force()

    def thread_names(self) -> list[str]:
        with self._lock:
            return [t.name for t in self._threads if t.is_alive()]

    # --- потоки ---

    def _spawn(self, name: str, target, args=()):
        thread = threading.Thread(target=target, args=args, daemon=True,
                                  name=f"live-subs-{name}")
        with self._lock:
            self._threads.append(thread)
        thread.start()

    def _status(self, message: str):
        self.ui.put(("status", "", "", message, ""))

    def _connect_services(self):
        """Подключает сервисы после показа окна, не блокируя интерфейс."""
        try:
            drop_unreachable_proxy()
            client = DeepgramClient(api_key=self.deepgram_key)
            for key, stream in STREAMS.items():
                if self.stop.is_set():
                    return
                connection = client.listen.v1.connect(
                    model=self.model, language="multi", encoding="linear16",
                    sample_rate=SAMPLE_RATE, channels=1, interim_results=True,
                    punctuate=True, endpointing=300, utterance_end_ms=1000,
                )
                socket = connection.__enter__()
                with self._lock:
                    closed = self._closing
                    if not closed:
                        self._sockets.append((connection, socket))
                if closed:  # окно закрыли, пока шло подключение
                    _close_socket(connection, socket)
                    return

                def meter(level, threshold, k=key):
                    self.ui.put(("level", k, level, threshold, ""))

                self._spawn(f"audio-{key}", stream_audio,
                            (self.sources[key], stream, socket, self.stop,
                             self.mutes[key], self._status, meter, self.echo))
                self._spawn(f"read-{key}", read_transcripts,
                            (socket, stream, self.jobs, self.ui, self.stop))

            translate = Translator(self.deepseek_key, self.translate_model)
            for index in range(2):
                self._spawn(f"translate-{index}", translate_worker,
                            (self.jobs, self.ui, translate, self.stop, self.settings))
            self.ui.put(("service", "Распознавание", "ok", "", ""))
            log(f"подключено: Deepgram {self.model}, перевод {self.translate_model}")
        except Exception as exc:  # noqa: BLE001
            log(f"ОШИБКА подключения: {exc!r}")
            self._status(f"ОШИБКА подключения: {exc}")
            self.ui.put(("service", "Распознавание", "fail", "", ""))

    # --- закрытие ---

    def _on_destroy(self, event):
        if event.widget is self.top:
            self.stop.set()  # окно убили снаружи (например, закрылся корень)

    def _stop_everything(self):
        """Стоп-событие, сокеты Deepgram, ожидание потоков. Вызывается не из Tk-потока."""
        self.stop.set()
        with self._lock:
            sockets, self._sockets = self._sockets, []
        for connection, socket in sockets:
            _close_socket(connection, socket)
        deadline = time.time() + CLOSE_JOIN_SECONDS
        index = 0
        while True:
            with self._lock:
                if index >= len(self._threads):
                    break
                thread = self._threads[index]
            thread.join(max(0.0, deadline - time.time()))
            index += 1
        left = self.thread_names()
        log("потоки остановлены" + (f", не успели: {left}" if left else ""))

    def close(self, wait: bool = False):
        """Закрыть окно. По умолчанию потоки останавливаются в фоне, а Tk-цикл
        не блокируется: окно сразу прячется и уничтожается, когда всё стихло."""
        with self._lock:
            if self._closing:
                return
            self._closing = True
        log("закрытие окна")
        self.stop.set()
        try:
            self.top.withdraw()
        except tk.TclError:
            pass
        if wait:
            self._stop_everything()
            self._destroy()
            return
        self._cleaner = threading.Thread(target=self._stop_everything, daemon=True,
                                         name="live-subs-cleanup")
        self._cleaner.start()
        self._finish()

    def _finish(self):
        if self._cleaner is not None and self._cleaner.is_alive():
            try:
                self.top.after(50, self._finish)
                return
            except tk.TclError:
                pass  # корень уже уничтожен — окно ушло вместе с ним
        self._destroy()

    def _destroy(self):
        global _current
        try:
            self.top.destroy()
        except tk.TclError:
            pass
        if _current is self:
            _current = None


_current: LiveWindow | None = None


def open_window(parent, *, deepgram_key: str | None = None,
                deepseek_key: str | None = None,
                model: str = DEFAULT_DEEPGRAM_MODEL,
                translate_model: str = DEFAULT_DEEPSEEK_MODEL) -> "LiveWindow | None":
    """Открывает окно субтитров поверх ``parent`` (корень VoiceType).

    Окно одно: повторный вызов поднимает уже открытое. Ключи, если не переданы,
    берутся из настроек VoiceType. Возвращает None, если звуковых устройств нет.
    Пока предыдущее окно ещё закрывается (до нескольких секунд), новое не
    открывается — вызов вернёт закрывающееся окно.
    """
    global _current, LOG_PATH
    if _current is not None and not _current.alive():
        _current = None
    if _current is not None:
        if not _current.closing:
            _current.raise_window()
        return _current

    if deepgram_key is None or deepseek_key is None:
        saved = settings_io.load()
        deepgram_key = saved.deepgram_api_key if deepgram_key is None else deepgram_key
        deepseek_key = saved.deepseek_api_key if deepseek_key is None else deepseek_key
    if not deepgram_key.strip() or not deepseek_key.strip():
        raise ValueError("Нужны ключи Deepgram и DeepSeek")

    try:
        speaker = sc.default_speaker()
        sources = {
            "them": lambda: sc.get_microphone(id=str(speaker.name), include_loopback=True),
            "me": sc.default_microphone,
        }
        microphone_name = sc.default_microphone().name
    except Exception as exc:  # noqa: BLE001
        from tkinter import messagebox

        messagebox.showerror(APP_NAME, f"Не найдено звуковое устройство: {exc}",
                             parent=parent)
        return None

    try:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        LOG_PATH = str(config.data_dir / "live_subtitles.log")
        open(LOG_PATH, "w", encoding="utf-8").close()  # журнал на каждую сессию свой
    except OSError:
        LOG_PATH = None
    log("=== запуск ===")
    log(f"вывод (речь собеседника): {speaker.name}")
    log(f"ввод (ваша речь): {microphone_name}")
    log(f"модели: Deepgram {model}, перевод {translate_model}")

    _current = LiveWindow(parent, deepgram_key.strip(), deepseek_key.strip(),
                          sources, model, translate_model)
    return _current


def shutdown():
    """Закрыть окно и дождаться потоков — при выходе из VoiceType."""
    if _current is not None and not _current.closing:
        _current.close(wait=True)
