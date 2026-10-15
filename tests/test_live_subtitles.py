"""Проверки конвейера живых субтитров без обращения к Deepgram и DeepSeek.

Перенесено из live-subtitles/test_pipeline.py как есть, в формате pytest.
"""
import queue
import threading
import types
from contextlib import contextmanager

import numpy as np
import pytest

from core import config
from desktop import live_subtitles as subtitles
from desktop.live_subtitles import BLOCK, SAMPLE_RATE, STREAMS, read_transcripts, stream_audio, to_pcm

SILENCE = np.zeros(BLOCK, dtype="float32")
TONE = (np.sin(2 * np.pi * 440 * np.arange(BLOCK) / SAMPLE_RATE) * 0.3).astype("float32")


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """Settings субтитров лежат в config.data_dir — не трогаем настоящие данные."""
    monkeypatch.setattr(config, "data_dir", tmp_path)


def _tk_root():
    import tkinter as tk

    try:
        return tk.Tk()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Cannot create Tk root in this env: {exc}")


class FakeRecorder:
    """Отдаёт заданные блоки, затем просит остановиться."""

    def __init__(self, blocks, stop):
        self.blocks = list(blocks)
        self.stop = stop

    @contextmanager
    def recorder(self, samplerate, channels):
        yield self

    def record(self, numframes):
        if self.blocks:
            return self.blocks.pop(0)
        self.stop.set()
        return np.zeros(numframes, dtype="float32")


class FakeSocket:
    def __init__(self, messages=None):
        self.media = []
        self.pings = 0
        self.messages = messages or []

    def send_media(self, data):
        self.media.append(data)

    def send_keep_alive(self):
        self.pings += 1

    def __iter__(self):
        return iter(self.messages)


def message(text, is_final=False, speech_final=False):
    alt = types.SimpleNamespace(transcript=text)
    return types.SimpleNamespace(
        channel=types.SimpleNamespace(alternatives=[alt]),
        is_final=is_final,
        speech_final=speech_final,
    )


def run_stream(blocks, muted=None, socket=None):
    stop = threading.Event()
    socket = socket or FakeSocket()
    stream_audio(lambda: FakeRecorder(blocks, stop), STREAMS["them"], socket, stop,
                 muted or threading.Event(), lambda _: None)
    return socket


def test_to_pcm():
    data = to_pcm(np.array([0.0, 1.0, -1.0, 2.0], dtype="float32"))
    values = np.frombuffer(data, dtype="<i2")
    assert list(values) == [0, 32767, -32767, 32767], values
    print("OK  to_pcm даёт 16-битный PCM и режет выбросы за пределы диапазона")


def test_silence_is_not_sent():
    socket = run_stream([SILENCE] * 40)
    assert not socket.media, f"в тишину ушло {len(socket.media)} пакетов"
    print("OK  тишина в Deepgram не уходит — за неё не платим")


def test_speech_is_sent_with_tail():
    socket = run_stream([SILENCE] * 18 + [TONE] * 10 + [SILENCE] * 30)
    # 10 блоков речи + хвост, чтобы Deepgram успел закрыть фразу
    assert 20 <= len(socket.media) <= 23, f"отправлено {len(socket.media)} пакетов"
    assert all(len(chunk) == BLOCK * 2 for chunk in socket.media)
    print(f"OK  речь ушла целиком плюс хвост тишины ({len(socket.media)} пакетов по 100 мс)")


def test_mute_stops_sending():
    muted = threading.Event()
    muted.set()
    socket = run_stream([SILENCE] * 18 + [TONE] * 20, muted=muted)
    assert not socket.media, "на паузе звук уходить не должен"
    print("OK  пауза полностью отключает поток")


def test_keepalive_in_silence():
    original = subtitles.KEEPALIVE_EVERY
    # отрицательный порог: любая пауза считается длинной, не зависим от часов
    subtitles.KEEPALIVE_EVERY = -1.0
    try:
        socket = run_stream([SILENCE] * 30)
    finally:
        subtitles.KEEPALIVE_EVERY = original
    assert socket.pings > 0, "без пингов Deepgram закроет сокет на длинной паузе"
    print(f"OK  в тишине идут keep-alive ({socket.pings} шт.) — сокет не отвалится")


def test_meter_reports_levels():
    seen = []
    stop = threading.Event()
    blocks = [SILENCE] * 18 + [TONE] * 5 + [SILENCE] * 5
    stream_audio(lambda: FakeRecorder(blocks, stop), STREAMS["them"], FakeSocket(), stop,
                 threading.Event(), lambda _: None,
                 lambda level, threshold: seen.append((level, threshold)))

    assert seen, "индикатор не получил ни одного значения"
    levels = [level for level, _ in seen]
    assert max(levels) > seen[0][1], "громкий блок должен превышать порог"
    assert min(levels) == 0.0, "тишина должна показываться нулём"
    print(f"OK  индикатор получает уровень ({len(seen)} замеров, пик {max(levels):.3f})")


def test_utterance_end_flushes():
    jobs: queue.Queue = queue.Queue()
    ui: queue.Queue = queue.Queue()
    # speech_final не пришёл — фразу должен закрыть UtteranceEnd
    socket = FakeSocket([
        message("Hello Marina.", is_final=True),
        types.SimpleNamespace(type="UtteranceEnd"),
    ])
    read_transcripts(socket, STREAMS["them"], jobs, ui, threading.Event())

    _, text = jobs.get_nowait()
    assert text == "Hello Marina.", text
    print("OK  фраза закрывается по UtteranceEnd, даже если speech_final не пришёл")


def test_long_speech_flushes_without_pause():
    """В монологе без пауз перевод должен идти кусками, а не одним в конце."""
    jobs: queue.Queue = queue.Queue()
    ui: queue.Queue = queue.Queue()
    chunk = "Wide grip pull ups reduce the range of motion and change the mechanics. "
    socket = FakeSocket([message(chunk, is_final=True) for _ in range(6)])

    read_transcripts(socket, STREAMS["them"], jobs, ui, threading.Event())

    assert jobs.qsize() >= 2, f"перевод ушёл {jobs.qsize()} куском вместо нескольких"
    print(f"OK  длинная речь без пауз режется на части ({jobs.qsize()} шт.)")


def test_fragments_are_dropped():
    jobs: queue.Queue = queue.Queue()
    ui: queue.Queue = queue.Queue()
    socket = FakeSocket([message("The", is_final=True, speech_final=True)])
    read_transcripts(socket, STREAMS["them"], jobs, ui, threading.Event())

    assert jobs.empty(), "обрывок из одного слова не должен уходить на перевод"
    print("OK  короткие обрывки отсекаются, лишний запрос к API не уходит")


def test_direction_follows_the_spoken_language():
    """Кириллица — наша речь, всё остальное — речь собеседника."""
    from desktop.live_subtitles import detect_direction

    assert detect_direction("Как ты меня слышишь?") == (
        "английский", "русский → английский")
    assert detect_direction("Can you hear me?") == ("русский", "→ русский")
    # короткое слово, на котором Deepgram ошибался с меткой языка
    assert detect_direction("Да.")[0] == "английский"
    # смешанная фраза: есть кириллица — значит говорим мы
    assert detect_direction("Открой pull request, пожалуйста")[0] == "английский"
    print("OK  сторона определяется по кириллице")


def test_partner_language_is_configurable():
    """Клиенты бывают из Германии и Индии: наша речь идёт на выбранный язык."""
    from desktop.live_subtitles import detect_direction

    assert detect_direction("Добрый день, рада знакомству.", "немецкий") == (
        "немецкий", "русский → немецкий")
    assert detect_direction("Здравствуйте.", "хинди")[0] == "хинди"

    # речь собеседника всегда переводится на русский, каким бы алфавитом ни была
    for phrase in ("Guten Tag, wie geht es Ihnen?",  # немецкий
                   "नमस्ते, आप कैसे हैं?",              # хинди
                   "Good morning."):                  # английский
        assert detect_direction(phrase, "немецкий") == ("русский", "→ русский"), phrase
    print("OK  язык собеседника настраивается, входящая речь всегда идёт в русский")


def test_translator_disables_thinking_and_retries():
    """Размышления должны быть выключены, а обрыв связи — переживаться повтором."""
    import json
    import urllib.error
    from unittest import mock

    from desktop.live_subtitles import Translator

    sent_bodies = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return json.dumps(self.payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    attempts = {"n": 0}

    def fake_urlopen(request, timeout=None):
        sent_bodies.append(json.loads(request.data))
        attempts["n"] += 1
        if attempts["n"] == 1:  # первая попытка обрывается
            raise urllib.error.URLError("connection reset")
        return Response({"choices": [{"message": {"content": "Всё хорошо."}}]})

    with mock.patch("desktop.live_subtitles.urllib.request.urlopen", fake_urlopen):
        result = Translator("key", "deepseek-v4-flash")("русский", "All good.")

    assert result == "Всё хорошо.", result
    assert attempts["n"] == 2, "повтора после обрыва связи не было"
    assert sent_bodies[0]["thinking"] == {"type": "disabled"}, sent_bodies[0]
    print("OK  размышления выключены, обрыв связи переживается повтором")


def test_empty_translation_is_not_lost():
    """Если модель вернула пустоту, реплика всё равно должна попасть в ленту."""
    import json
    from unittest import mock

    from desktop.live_subtitles import Translator

    class Response:
        def read(self):
            return json.dumps({"choices": [{"message": {"content": ""}}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with mock.patch("desktop.live_subtitles.urllib.request.urlopen", lambda *a, **k: Response()):
        assert Translator("key", "deepseek-v4-flash")("русский", "All good.") == ""
    print("OK  пустой ответ модели не роняет перевод, обрабатывается выше")


def test_feed_is_single_and_chronological():
    """Обе стороны идут одной лентой в порядке разговора."""
    from desktop.live_subtitles import App

    root = _tk_root()
    root.geometry("720x520")
    app = App(root, queue.Queue(), threading.Event(),
              {key: threading.Event() for key in STREAMS}, 15)

    app.add_line("me", "Привет, как слышно?", "Hi, how do you read me?")
    app.add_line("them", "Loud and clear.", "Слышу отлично.")
    app.add_line("me", "Отлично.", "Great.")
    root.update()

    content = app.feed.get("1.0", "end")
    height = app.feed.winfo_height()
    root.destroy()

    assert "русский → английский" in content and "→ русский" in content
    positions = [content.index(marker) for marker in
                 ("Привет, как слышно?", "Loud and clear.", "Отлично.")]
    assert positions == sorted(positions), "реплики идут не в порядке разговора"
    assert content.count("Вы  ·") == 2 and content.count("Собеседник  ·") == 1
    assert height > 100, f"лента сжата: {height}"
    print(f"OK  одна лента, три реплики по порядку, высота {height} px")


def test_same_speaker_merges_into_one_block():
    """Фраза из нескольких предложений — один блок с одним заголовком."""
    from desktop.live_subtitles import App

    root = _tk_root()
    app = App(root, queue.Queue(), threading.Event(),
              {key: threading.Event() for key in STREAMS}, 15)

    app.add_line("me", "Итак, сейчас мы поговорим о том,", "So, now we're gonna talk about")
    app.add_line("me", "как всё это", "how all this")
    app.add_line("me", "происходит.", "happens.")
    app.add_line("them", "Got it.", "Понял.")
    app.add_line("me", "Отлично.", "Great.")
    root.update()

    content = app.feed.get("1.0", "end")
    root.destroy()

    assert content.count("Вы  ·") == 2, f"три фразы подряд не слились:\n{content}"
    assert content.count("Собеседник  ·") == 1
    assert "Итак, сейчас мы поговорим о том, как всё это происходит." in content, content
    assert "So, now we're gonna talk about how all this happens." in content, content
    # оригинал и перевод не должны перемешаться
    assert content.index("происходит.") < content.index("So, now"), content
    print("OK  подряд идущие фразы одного говорящего сливаются в один блок")


def test_transcripts_routing():
    jobs: queue.Queue = queue.Queue()
    ui: queue.Queue = queue.Queue()
    socket = FakeSocket([
        message("Hello"),                                   # черновик
        message("Hello Marina"),                            # черновик
        message("Hello Marina.", is_final=True),            # финал, фраза не закрыта
        message("How are you?", is_final=True, speech_final=True),
    ])
    read_transcripts(socket, STREAMS["them"], jobs, ui, threading.Event())

    # каждое законченное предложение переводится отдельно — так точнее,
    # а в ленте они всё равно сливаются в один блок говорящего
    phrases = []
    while not jobs.empty():
        stream, text = jobs.get_nowait()
        assert stream.key == "them"
        phrases.append(text)
    assert phrases == ["Hello Marina.", "How are you?"], phrases

    lives = [item for item in list(ui.queue) if item[0] == "live"]
    assert [item[2] for item in lives[:2]] == ["Hello", "Hello Marina"]

    # Отправленная на перевод фраза уходит в интерфейс как "pending": текст
    # остаётся на экране, пока идёт перевод, иначе реплика на секунду исчезает.
    # Живую строку под ним очищает уже сам App, обрабатывая это сообщение.
    pendings = [item for item in list(ui.queue) if item[0] == "pending"]
    assert [item[2] for item in pendings] == ["Hello Marina.", "How are you?"], pendings
    print("OK  черновики идут в интерфейс, законченные предложения — на перевод")


def test_echo_gate_mutes_microphone_only_on_speakers():
    """Из динамиков голос собеседника попадает в микрофон и распознаётся дважды.

    Пока он говорит, микрофон не слушаем. В наушниках эха нет, и подавление
    только мешало бы вставить слово, поэтому оно отключается настройкой.
    """
    settings = types.SimpleNamespace(speakers=True)
    gate = subtitles.EchoGate(settings)

    assert not gate.blocks_microphone(), "молчит собеседник — микрофон свободен"
    gate.heard_partner()
    assert gate.blocks_microphone(), "говорит собеседник — микрофон заглушён"

    settings.speakers = False
    assert not gate.blocks_microphone(), "в наушниках не глушим никогда"

    settings.speakers = True
    gate.loud_until = 0.0  # выдержка истекла
    assert not gate.blocks_microphone(), "после паузы микрофон снова слушаем"
    print("OK  подавление эха работает только при выводе в динамики")


def test_microphone_is_not_sent_while_partner_speaks():
    """Заглушённый микрофон не должен уходить в Deepgram — это дубли и деньги."""
    stop = threading.Event()
    socket = FakeSocket()
    gate = subtitles.EchoGate(types.SimpleNamespace(speakers=True))
    gate.heard_partner()

    blocks = [SILENCE] * subtitles.CALIBRATION_BLOCKS + [TONE] * 5
    stream_audio(lambda: FakeRecorder(blocks, stop), STREAMS["me"], socket, stop,
                 threading.Event(), lambda _: None, None, gate)
    assert socket.media == [], f"ушло {len(socket.media)} блоков вместо нуля"

    # тот же звук без подавления обязан уйти, иначе тест ничего не доказывает
    stop = threading.Event()
    loud = FakeSocket()
    blocks = [SILENCE] * subtitles.CALIBRATION_BLOCKS + [TONE] * 5
    stream_audio(lambda: FakeRecorder(blocks, stop), STREAMS["me"], loud, stop,
                 threading.Event(), lambda _: None, None, None)
    assert loud.media, "без подавления речь должна уходить"
    print("OK  пока говорит собеседник, микрофон в Deepgram не уходит")


def test_window_opens_once_and_closes_cleanly(monkeypatch):
    """open_window: одно окно на всех, закрытие гасит свои потоки и сокеты,
    повторное открытие работает. Сеть и звуковые устройства подменены."""
    import time

    import customtkinter as ctk

    class Device:
        name = "fake"

    class Recorder:
        name = "fake"

        @contextmanager
        def recorder(self, samplerate, channels):
            yield self

        def record(self, numframes):
            time.sleep(0.05)
            return np.zeros(numframes, dtype="float32")

    class Socket:
        def __init__(self):
            self.inbox = queue.Queue()
            self.closed = False

        def send_media(self, data):
            pass

        def send_keep_alive(self):
            pass

        def send_close_stream(self):
            self.closed = True
            self.inbox.put(None)

        def __iter__(self):
            return iter(self.inbox.get, None)

    sockets = []

    class Connection:
        def __enter__(self):
            sockets.append(Socket())
            return sockets[-1]

        def __exit__(self, *args):
            return False

    class Client:
        def __init__(self, api_key):
            self.listen = types.SimpleNamespace(
                v1=types.SimpleNamespace(connect=lambda **kw: Connection()))

    monkeypatch.setattr(subtitles, "DeepgramClient", Client)
    monkeypatch.setattr(subtitles.sc, "default_speaker", lambda: Device())
    monkeypatch.setattr(subtitles.sc, "default_microphone", lambda: Recorder())
    monkeypatch.setattr(subtitles.sc, "get_microphone", lambda **kw: Recorder())
    monkeypatch.setattr(subtitles, "LOG_PATH", None)
    monkeypatch.setattr(subtitles, "_current", None)

    try:
        root = ctk.CTk()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Cannot create Tk root in this env: {exc}")
    root.withdraw()

    def pump(until, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end and not until():
            root.update()
            time.sleep(0.02)

    def live_threads():
        return [t.name for t in threading.enumerate() if t.name.startswith("live-subs")]

    try:
        for _ in range(2):  # закрыли и открыли заново
            window = subtitles.open_window(root, deepgram_key="dg", deepseek_key="ds")
            pump(lambda: len(window.thread_names()) == 6)
            assert len(window.thread_names()) == 6, window.thread_names()
            assert subtitles.open_window(
                root, deepgram_key="dg", deepseek_key="ds") is window

            window.app.quit()  # то же, что крестик окна
            pump(lambda: not window.alive())
            pump(lambda: not live_threads(), timeout=2.0)
            assert not window.alive()
            assert live_threads() == []
            assert all(s.closed for s in sockets)
            assert root.winfo_exists(), "VoiceType не должен закрываться вместе с окном"
    finally:
        root.destroy()


def test_open_window_requires_keys():
    with pytest.raises(ValueError):
        subtitles.open_window(None, deepgram_key="dg", deepseek_key=" ")
