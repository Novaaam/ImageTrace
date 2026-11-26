"""Reusable small widgets."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QLayout, QVBoxLayout, QWidget


def label(
    text: str,
    *,
    obj: str | None = None,
    muted: bool = False,
    wrap: bool = True,
    state: str | None = None,
) -> QLabel:
    lbl = QLabel(text)
    # Plain text always: labels often show user/evidence-derived strings, which must never be
    # interpreted as HTML (Qt's default is to auto-detect rich text).
    lbl.setTextFormat(Qt.TextFormat.PlainText)
    lbl.setWordWrap(wrap)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if obj:
        lbl.setObjectName(obj)
    if muted:
        lbl.setProperty("muted", True)
    if state:
        lbl.setProperty("state", state)
    return lbl


def set_state(lbl: QLabel, state: str | None) -> None:
    """Change a label's state property ('ok' | 'warn' | 'error') and re-polish its style."""
    lbl.setProperty("state", state)
    lbl.style().unpolish(lbl)
    lbl.style().polish(lbl)


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


def elide(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Card(QFrame):
    """Titled panel."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 12, 16, 14)
        self.body.setSpacing(6)
        self.title_label = label(title, obj="CardTitle")
        self.body.addWidget(self.title_label)
        self.setAccessibleName(title)


class Page(QWidget):
    """Base page: title, optional subtitle, content area."""

    title = ""
    subtitle = ""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(24, 20, 24, 20)
        self.layout_.setSpacing(12)
        self.layout_.addWidget(label(self.title, obj="PageTitle"))
        if self.subtitle:
            self.layout_.addWidget(label(self.subtitle, muted=True))
        self.setAccessibleName(f"{self.title} page")

    def refresh(self) -> None:
        """Called when the page becomes visible."""


class PlaceholderPage(Page):
    """Honest empty page for features that aren't built yet."""

    phase = ""
    planned = ""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        card = Card("Not available yet")
        card.body.addWidget(label(f"This section is planned for {self.phase}.", muted=True))
        if self.planned:
            card.body.addWidget(label(self.planned, muted=True))
        self.layout_.addWidget(card)
        self.layout_.addStretch(1)
