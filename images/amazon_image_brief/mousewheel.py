"""Route the wheel once to the nearest scrollable view under the pointer."""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk


class MouseWheelRouter:
    def __init__(self, root):
        self.root = root
        self.tag = f'WheelRouter{id(self)}'
        for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            root.bind_class(self.tag, sequence, self.route)
        root.bind_all('<Map>', lambda event: self.install(event.widget), add='+')
        self.install(root)

    def install(self, widget):
        if not isinstance(widget, tk.Misc):
            return
        if self.tag not in widget.bindtags():
            widget.bindtags((self.tag, *widget.bindtags()))
        for child in widget.winfo_children():
            self.install(child)

    @staticmethod
    def steps(event) -> int:
        button = getattr(event, 'num', None)
        if button in (4, 5):
            return -3 if button == 4 else 3
        delta = getattr(event, 'delta', 0)
        return -int(math.copysign(max(1, math.ceil(abs(delta) / 120)) * 3, delta)) if delta else 0

    def route(self, event):
        try:
            hovered = self.root.winfo_containing(event.x_root, event.y_root)
        except (tk.TclError, KeyError, AttributeError):
            hovered = None
        widget = hovered or event.widget
        # Do not hijack native dropdown popups or unrelated windows.
        if not isinstance(widget, tk.Misc):
            return None
        steps = self.steps(event)
        if not steps:
            return None
        cursor = widget
        while cursor:
            target = getattr(cursor, 'scroll_canvas', None)
            if isinstance(cursor, (tk.Text, tk.Listbox, ttk.Treeview)):
                target = cursor
            if target is not None:
                start, end = target.yview()
                can_scroll = (steps < 0 and start > 0) or (steps > 0 and end < 1)
                if can_scroll:
                    target.yview_scroll(steps, 'units')
                    return 'break'
            cursor = cursor.master
        # Wheel over inputs must not silently change variants/models/numbers.
        return 'break'
