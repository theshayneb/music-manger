"""The player bar: play/pause, previous/next, a seek bar and volume.

The file is always released (player source cleared) before the app moves,
renames, converts, deletes or re-tags it, so Windows never says it's in use.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QToolButton, QWidget

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
        self._resume_at: tuple[int, bool] | None = None
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
            b.setMinimumSize(56, 48)
            font = b.font()
            font.setPointSizeF(font.pointSizeF() * (2.6 if b is self.play_btn else 2.0))
            b.setFont(font)
            layout.addWidget(b)
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
        # Start only once the file has loaded, from 0:00: starting straight away could lose the first
        # moment of the song while the file was still opening.
        self._resume_at = (0, True)
        self.player.setSource(QUrl.fromLocalFile(path))
        if self.player.mediaStatus() in (QMediaPlayer.MediaStatus.LoadedMedia,
                                         QMediaPlayer.MediaStatus.BufferedMedia):
            self._apply_resume()

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
        # The position can only be set once the file has loaded; until then setPosition is
        # ignored (which made a song start over after its tags were saved).
        self._resume_at = (position, playing)
        self.player.setSource(QUrl.fromLocalFile(path))
        if self.player.mediaStatus() in (QMediaPlayer.MediaStatus.LoadedMedia,
                                         QMediaPlayer.MediaStatus.BufferedMedia):
            self._apply_resume()

    def _apply_resume(self) -> None:
        position, playing = self._resume_at or (0, False)
        self._resume_at = None
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
        if self._resume_at is not None and status in (QMediaPlayer.MediaStatus.LoadedMedia,
                                                      QMediaPlayer.MediaStatus.BufferedMedia):
            self._apply_resume()
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.finished.emit()
