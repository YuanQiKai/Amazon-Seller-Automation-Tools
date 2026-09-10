"""Four native Tk design systems; frosted colours emulate glass without OS blur."""
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageDraw, ImageTk


THEMES = {
    '苹果 · iOS 玻璃拟态': dict(bg='#eaf0fa', card='#f6f9ff', ink='#202f49', muted='#63748e', accent='#1766e8', line='#cfdaed', tint='#dce9ff', font='Microsoft YaHei UI', relief='flat'),
    '极简 · 瑞士 / Notion': dict(bg='#efeeeb', card='#faf9f6', ink='#272724', muted='#787770', accent='#373833', line='#deddd7', tint='#e9e7e0', font='Microsoft YaHei UI', relief='solid'),
    'Bento · 彩色卡片网格': dict(bg='#edeafa', card='#faf8ff', ink='#302648', muted='#776c90', accent='#6b45d6', line='#dcd2f2', tint='#e4dafc', font='Microsoft YaHei UI', relief='solid'),
    '新拟态 · Soft UI': dict(bg='#dde5ed', card='#e9eff5', ink='#34495f', muted='#718397', accent='#287b8c', line='#c4d1dd', tint='#d0e7eb', font='Microsoft YaHei UI', relief='raised'),
}


class ThemeManager:
    def __init__(self, root):
        self.root = root
        self.style = ttk.Style(root)
        self.style.theme_use('clam')
        self.images = {}
        for index, (name, colors) in enumerate(THEMES.items()):
            icons = []
            for selected in (False, True):
                image = Image.new('RGBA', (24, 24))
                draw = ImageDraw.Draw(image)
                draw.rounded_rectangle((2, 2, 21, 21), radius=5, fill=colors['accent'] if selected else colors['card'], outline=colors['accent'] if selected else colors['muted'], width=2)
                if selected:
                    draw.line(((6, 12), (10, 16), (18, 7)), fill='white', width=3, joint='curve')
                icons.append(ImageTk.PhotoImage(image, master=root))
            element = f'Check{index}.indicator'
            self.style.element_create(element, 'image', icons[0], ('selected', icons[1]), border=0, sticky='')
            self.images[name] = (element, icons)

    def apply(self, name):
        if name not in THEMES:
            name = next(iter(THEMES))
        c = THEMES[name]
        s = self.style
        self.root.configure(background=c['bg'])
        self.root.option_add('*Font', (c['font'], 10))
        s.configure('.', background=c['card'], foreground=c['ink'], font=(c['font'], 10))
        s.configure('TFrame', background=c['card'])
        s.configure('TLabel', padding=(0, 2))
        s.configure('Title.TLabel', font=(c['font'], 20, 'bold'), foreground=c['accent'])
        s.configure('Subtitle.TLabel', font=(c['font'], 9), foreground=c['muted'])
        s.configure('TLabelframe', bordercolor=c['line'], lightcolor='#ffffff', darkcolor=c['line'], relief=c['relief'], borderwidth=2)
        for key in ('TLabelframe.Label', 'Section.TLabelframe.Label'):
            s.configure(key, font=(c['font'], 11, 'bold'), foreground=c['accent'])
        s.configure('TNotebook', background=c['bg'], borderwidth=0, tabmargins=(0, 6, 0, 0))
        s.configure('TNotebook.Tab', padding=(15, 10), background=c['tint'], foreground=c['muted'], font=(c['font'], 10, 'bold'), expand=(0, 0, 0, 0), borderwidth=0)
        # Clam otherwise expands selected tabs by 2px, changing the page viewport.
        s.map('TNotebook.Tab', background=[('selected', c['accent']), ('active', c['line'])], foreground=[('selected', 'white')], expand=[('selected', (0, 0, 0, 0)), ('!selected', (0, 0, 0, 0))], padding=[('selected', (15, 10)), ('!selected', (15, 10))])
        s.configure('TButton', padding=(10, 7), borderwidth=1, bordercolor=c['line'], lightcolor='#ffffff', darkcolor=c['line'], background=c['card'], relief=c['relief'])
        s.map('TButton', background=[('active', c['tint'])], foreground=[('disabled', c['muted'])])
        s.configure('Primary.TButton', background=c['accent'], foreground='white', font=(c['font'], 10, 'bold'))
        s.map('Primary.TButton', background=[('disabled', c['line']), ('active', c['ink'])], foreground=[('disabled', c['muted'])])
        s.configure('Danger.TButton', background='#fff1f1', foreground='#af3043', bordercolor='#efc4cb')
        s.map('Danger.TButton', background=[('active', '#ffdee4')])
        s.configure('TEntry', fieldbackground='white', padding=6, bordercolor=c['line'])
        s.configure('TCombobox', fieldbackground='white', padding=5, arrowsize=16)
        s.map('TCombobox', fieldbackground=[('readonly', 'white')], foreground=[('readonly', c['ink'])])
        s.configure('Treeview', rowheight=32, background='white', fieldbackground='white', bordercolor=c['line'])
        s.map('Treeview', background=[('selected', c['tint'])], foreground=[('selected', c['ink'])])
        s.configure('Treeview.Heading', background=c['tint'], font=(c['font'], 10, 'bold'), padding=(6, 9))
        s.configure('Vertical.TScrollbar', arrowsize=18, width=18, background=c['line'], troughcolor=c['bg'])
        s.configure('Horizontal.TProgressbar', background=c['accent'], troughcolor=c['tint'])
        s.layout('TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [(self.images[name][0], {'side': 'left', 'sticky': ''}), ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [('Checkbutton.label', {'sticky': 'nswe'})]})]})])
        self._recolor(self.root, c)

    def _recolor(self, parent, colors):
        for widget in parent.winfo_children():
            if isinstance(widget, (tk.Text, tk.Listbox)):
                widget.configure(background='white', foreground=colors['ink'], selectbackground=colors['tint'], selectforeground=colors['ink'], highlightbackground=colors['line'])
            if hasattr(widget, 'scroll_canvas'):
                widget.scroll_canvas.configure(background=colors['card'])
            self._recolor(widget, colors)
