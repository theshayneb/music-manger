"""The player bar: play/pause, previous/next, a seek bar and volume.

The file is always released (player source cleared) before the app moves,
renames, converts, deletes or re-tags it, so Windows never says it's in use.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QToolButton, QWidget

from .model import fmt_time

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    HAVE_MULTIMEDIA = True
except ImportError:  # pragma: no cover
    HAVE_MULTIMEDIA = False


class PlayerBar(QWidget):
    next_requested = Signal()
    previous_requested = Signal()
    play_requested = Signal()
    finished = Signal()  # the song played to the end

    def __init__(self, volume: int = 70, parent: QWidget | None = None):
        super().__init__(parent)
        self.path = ""
        self._seeking = False
        self.player = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        self.prev_btn = QToolButton(text="⏮")
        self.play_btn = QToolButton(text="▶")
        self.next_btn = QToolButton(text="⏭")
        for b, tip in ((self.prev_btn, "Previous song"), (self.play_btn, "Play / pause (Space)"),
                       (self.next_btn, "Next song")):
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setMinimumWidth(34)
            layout.addWidget(b)
        self.back_btn = QPushButton("−10s")
        self.fwd_btn = QPushButton("+30s")
        self.back_btn.setToolTip("Back 10 seconds (Ctrl+Left)")
        self.fwd_btn.setToolTip("Forward 30 seconds (Ctrl+Right)")
        layout.addWidget(self.back_btn)
        layout.addWidget(self.fwd_btn)
        self.pos_label = QLabel("0:00")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.len_label = QLabel("0:00")
        layout.addWidget(self.pos_label)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.len_label)
        self.now_label = QLabel("")
        self.now_label.setMinimumWidth(160)
        self.now_label.setMaximumWidth(420)
        layout.addWidget(self.now_label)
        layout.addWidget(QLabel("🔊"))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(volume)
        self.volume.setFixedWidth(90)
        layout.addWidget(self.volume)

        self.prev_btn.clicked.connect(self.previous_requested)
        self.next_btn.clicked.connect(self.next_requested)
        self.play_btn.clicked.connect(self.toggle)
        self.back_btn.clicked.connect(lambda: self.skip(-10))
        self.fwd_btn.clicked.connect(lambda: self.skip(30))
        self.slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.slider.sliderReleased.connect(self._seek_released)
        self.slider.sliderMoved.connect(lambda v: self.pos_label.setText(fmt_time(v / 1000)))

        if HAVE_MULTIMEDIA:
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self._apply_volume(volume)
            self.volume.valueChanged.connect(self._apply_volume)
            self.player.positionChanged.connect(self._position)
            self.player.durationChanged.connect(self._duration)
            self.player.playbackStateChanged.connect(self._state)
            self.player.mediaStatusChanged.connect(self._status)
            self.player.errorOccurred.connect(lambda _e, msg: self.now_label.setText(f"Can't play: {msg}"))

    # ------------------------------------------------------------ control
    def available(self) -> bool:
        return self.player is not None

    def play(self, path: str, title: str = "") -> None:
        if not self.player:
            return
        self.path = path
        self.now_label.setText(title)
        self.now_label.setToolTip(path)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def toggle(self) -> None:
        if not self.player:
            return
        if not self.path:
            self.play_requested.emit()  # nothing loaded: play the selected song
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def is_playing(self) -> bool:
        return bool(self.player) and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def skip(self, seconds: int) -> None:
        if self.player and self.path:
            pos = max(0, self.player.position() + seconds * 1000)
            if self.player.duration() > 0:
                pos = min(pos, self.player.duration() - 500)
            self.player.setPosition(pos)

    def release(self) -> tuple[str, int, bool]:
        """Stop and let go of the file. Returns (path, position, was playing) for resume()."""
        state = (self.path, self.player.position() if self.player else 0, self.is_playing())
        if self.player:
            self.player.stop()
            self.player.setSource(QUrl())
        self.path = ""
        self.slider.setRange(0, 0)
        self.pos_label.setText("0:00")
        self.len_label.setText("0:00")
        self.play_btn.setText("▶")
        return state

    def resume(self, path: str, position: int, playing: bool, title: str = "") -> None:
        if not self.player or not path:
            return
        self.path = path
        self.now_label.setText(title)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.setPosition(position)
        if playing:
            self.player.play()

    def clear(self) -> None:
        self.release()
        self.now_label.setText("")

    # ------------------------------------------------------------ signals
    def _apply_volume(self, value: int) -> None:
        # Perceived loudness isn't linear: a cubic curve makes the slider feel even.
        self.audio.setVolume((value / 100) ** 3 if value else 0.0)

    def _position(self, ms: int) -> None:
        if not self._seeking:
            self.slider.setValue(ms)
            self.pos_label.setText(fmt_time(ms / 1000))

    def _duration(self, ms: int) -> None:
        self.slider.setRange(0, max(0, ms))
        self.len_label.setText(fmt_time(ms / 1000))

    def _seek_released(self) -> None:
        self._seeking = False
        if self.player:
            self.player.setPosition(self.slider.value())

    def _state(self, state) -> None:  # type: ignore[no-untyped-def]
        self.play_btn.setText("⏸" if state == QMediaPlayer.PlaybackState.PlayingState else "▶")

    def _status(self, status) -> None:  # type: ignore[no-untyped-def]
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.finished.emit()
