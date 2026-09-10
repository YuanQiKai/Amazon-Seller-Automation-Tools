from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class InputUndo:
    """Widget-local undo; programmatic context loads are never user edit history."""
    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self.histories: dict[tk.Misc, tuple[list[str], list[str]]] = {}
        self.tag = f'InputUndo{id(self)}'
        root.bind_class(self.tag, '<KeyPress>', self.before_edit)
        for sequence in ('<<Paste>>', '<<Cut>>', '<<Clear>>'):
            root.bind_class(self.tag, sequence, self.before_edit)
        root.bind_class(self.tag, '<Control-z>', self.undo)
        root.bind_class(self.tag, '<Control-Z>', self.undo)
        root.bind_class(self.tag, '<Control-y>', self.redo)
        root.bind_class(self.tag, '<Control-Y>', self.redo)
        root.bind_class(self.tag, '<Control-Shift-Z>', self.redo)
        root.bind_class(self.tag, '<Control-Shift-z>', self.redo)
        root.bind_class(self.tag, '<<ResetUndo>>', lambda event: self.reset(event.widget))
        root.bind_all('<FocusIn>', self.on_focus, add='+')
        self.install(root)

    def install(self, parent: tk.Misc) -> None:
        if isinstance(parent, (tk.Text, tk.Entry, ttk.Entry, ttk.Combobox, tk.Spinbox, ttk.Spinbox)):
            if self.tag not in parent.bindtags():
                parent.bindtags((self.tag, *parent.bindtags()))
                self.histories[parent] = ([], [])
                if isinstance(parent, tk.Text):
                    parent.configure(undo=True, autoseparators=True, maxundo=200)
                self.reset(parent)
        for child in parent.winfo_children():
            self.install(child)

    def on_focus(self, event) -> None:
        self.install(event.widget)

    def reset(self, widget: tk.Misc | None = None) -> None:
        targets = [widget] if widget is not None else list(self.histories)
        for target in targets:
            if not target.winfo_exists():
                self.histories.pop(target, None)
                continue
            if isinstance(target, tk.Text):
                target.edit_reset()
            else:
                self.histories[target] = ([], [])

    def reset_variables(self, variables) -> None:
        names = {str(variable) for variable in variables}
        for widget in list(self.histories):
            if widget.winfo_exists() and not isinstance(widget, tk.Text) and str(widget.cget('textvariable')) in names:
                self.reset(widget)

    @staticmethod
    def editable(widget) -> bool:
        return str(widget.cget('state')) not in ('disabled', 'readonly')

    def before_edit(self, event) -> None:
        widget = event.widget
        if isinstance(widget, tk.Text) or not self.editable(widget):
            return
        # Control shortcuts other than editing do not add history entries.
        if event.type == tk.EventType.KeyPress:
            if event.keysym in ('Control_L', 'Control_R', 'Shift_L', 'Shift_R', 'Alt_L', 'Alt_R', 'Left', 'Right', 'Home', 'End', 'Tab'):
                return
            if event.state & 4 and event.keysym.lower() not in ('v', 'x'):
                return
        undo, redo = self.histories.setdefault(widget, ([], []))
        current = widget.get()
        if not undo or undo[-1] != current:
            undo.append(current)
            del undo[:-200]
        redo.clear()

    def undo(self, event):
        return self.restore(event.widget, False)

    def redo(self, event):
        return self.restore(event.widget, True)

    def restore(self, widget, redo: bool):
        if not self.editable(widget):
            return 'break'
        if isinstance(widget, tk.Text):
            try:
                widget.edit_redo() if redo else widget.edit_undo()
            except tk.TclError:
                pass
        else:
            undo_stack, redo_stack = self.histories.setdefault(widget, ([], []))
            source, destination = (redo_stack, undo_stack) if redo else (undo_stack, redo_stack)
            current = widget.get()
            while source and source[-1] == current:
                source.pop()
            if source:
                value = source.pop()
                destination.append(current)
                variable = str(widget.cget('textvariable'))
                if variable:
                    widget.setvar(variable, value)
                else:
                    widget.delete(0, tk.END)
                    widget.insert(0, value)
                widget.icursor(tk.END)
        widget.event_generate('<<InputUndo>>')
        return 'break'
