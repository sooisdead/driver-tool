"""DriverTool — a clean driver updater. No ads, no bundled software, no telemetry.
Drivers come only from Windows Update and the Microsoft Update Catalog (signed)."""
import os
import queue
import re
import subprocess
import threading
import tkinter as tk
import webbrowser
from datetime import date, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import backend
import i18n
from i18n import tr as _

HERE = Path(__file__).parent
LOG = backend.LOG

PALETTES = {
    "light": dict(bg="#f0f0f0", fg="#000000", field="#ffffff", sel="#cce4f7", banner="#fff3cd",
                  problem="#ffd6d6", old="#fff3cd", avail="#d7f0dc", log="#f7f7f7"),
    "dark": dict(bg="#2b2b2b", fg="#e6e6e6", field="#1e1e1e", sel="#3d5a80", banner="#5c4a1a",
                 problem="#6b2b2b", old="#5c4a1a", avail="#2b5a35", log="#1e1e1e"),
}


def sort_key(v: str):
    """Numbers and dotted versions sort numerically, everything else as text."""
    if re.fullmatch(r"\d+(\.\d+)*", v or ""):
        return (0, [int(x) for x in v.split(".")], "")
    return (1, [], (v or "").lower())


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        backend.init_logging()
        s = backend.load_settings()
        i18n.set_lang(s.get("lang", "ru"))
        self.settings = s
        self.title("DriverTool")
        self.geometry(s.get("geometry", "1120x720"))
        self.minsize(900, 540)
        icon = HERE / "icon.ico"
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
            except tk.TclError:
                pass
        self.q: queue.Queue = queue.Queue()
        self.devices: dict = {"drivers": [], "problems": []}
        self.maker = ""
        self.avail: dict[str, dict] = {}       # deviceId -> catalog upgrade found by auto-scan
        self.auto_results: dict[str, dict] = {}
        self.updates: list[dict] = []
        self.catalog_results: dict[str, dict] = {}
        self.stale: dict[str, dict] = {}
        self.row_dev: dict[str, dict] = {}
        self.busy = False
        self.logs: list[tk.Text] = []
        self.trees: list[ttk.Treeview] = []
        self.tree_names: dict[ttk.Treeview, str] = {}

        self.old_years = tk.IntVar(value=int(s.get("old_years", 3)))
        self.dark = tk.BooleanVar(value=bool(s.get("dark", False)))
        self.hide_ms = tk.BooleanVar(value=bool(s.get("hide_ms", True)))
        self.restore_pt = tk.BooleanVar(value=bool(s.get("restore_point", True)))
        self.group = tk.BooleanVar(value=bool(s.get("group", False)))
        self.only_issues = tk.BooleanVar(value=False)
        self.filter_text = tk.StringVar()
        self.lang = tk.StringVar(value=s.get("lang", "ru"))

        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self._build_banner()
        self._build_statusbar()
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(4, 0))
        self._build_devices()
        self._build_updates()
        self._build_catalog()
        self._build_auto()
        self._build_maintenance()
        self._build_backup()
        self._restore_columns()
        self.apply_theme()
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.old_years.trace_add("write", lambda *_a: self.render_devices())
        self.filter_text.trace_add("write", lambda *_a: self.render_devices())
        self.after(100, self._pump)
        self.after(300, self.scan)
        self.after(500, self.load_journal)

    # ---------- theme / settings ----------
    def apply_theme(self):
        p = PALETTES["dark" if self.dark.get() else "light"]
        self.pal = p
        st = self.style
        self.configure(bg=p["bg"])
        st.configure(".", background=p["bg"], foreground=p["fg"], fieldbackground=p["field"])
        st.configure("Treeview", background=p["field"], fieldbackground=p["field"], foreground=p["fg"], rowheight=22)
        st.map("Treeview", background=[("selected", p["sel"])], foreground=[("selected", p["fg"])])
        st.configure("Treeview.Heading", background=p["bg"], foreground=p["fg"])
        st.configure("TNotebook", background=p["bg"])
        st.configure("TNotebook.Tab", background=p["bg"], foreground=p["fg"], padding=(10, 4))
        st.map("TNotebook.Tab", background=[("selected", p["field"])])
        st.configure("TButton", background=p["bg"], foreground=p["fg"])
        st.configure("TEntry", fieldbackground=p["field"], foreground=p["fg"])
        st.configure("TSpinbox", fieldbackground=p["field"], foreground=p["fg"])
        st.configure("TCombobox", fieldbackground=p["field"], foreground=p["fg"], background=p["bg"],
                     arrowcolor=p["fg"], selectbackground=p["field"], selectforeground=p["fg"])
        st.map("TCombobox", fieldbackground=[("readonly", p["field"])], foreground=[("readonly", p["fg"])],
               selectbackground=[("readonly", p["field"])], selectforeground=[("readonly", p["fg"])])
        self.option_add("*TCombobox*Listbox.background", p["field"])
        self.option_add("*TCombobox*Listbox.foreground", p["fg"])
        for tv in self.trees:
            for tag in ("problem", "old", "avail"):
                tv.tag_configure(tag, background=p[tag])
        for t in self.logs:
            t.configure(bg=p["log"], fg=p["fg"])
        if hasattr(self, "banner"):
            self.banner.configure(bg=p["banner"])
            for w in self.banner.winfo_children():
                if isinstance(w, tk.Label):
                    w.configure(bg=p["banner"], fg=p["fg"])
        self.save_prefs()

    def save_prefs(self):
        try:
            years = int(self.old_years.get())
        except (tk.TclError, ValueError):
            years = 3
        self.settings.update({"old_years": years, "dark": self.dark.get(), "hide_ms": self.hide_ms.get(),
                              "restore_point": self.restore_pt.get(), "group": self.group.get(),
                              "lang": self.lang.get()})
        backend.save_settings(self.settings)

    def _restore_columns(self):
        saved = self.settings.get("columns", {})
        for tv, name in self.tree_names.items():
            for col, w in saved.get(name, {}).items():
                if col in tv["columns"]:
                    tv.column(col, width=int(w))

    def _close(self):
        self.settings["geometry"] = self.geometry()
        self.settings["columns"] = {name: {c: tv.column(c)["width"] for c in tv["columns"]}
                                    for tv, name in self.tree_names.items()}
        self.save_prefs()
        backend.cancel_all()
        self.destroy()

    def _bind_keys(self):
        self.bind_all("<F5>", lambda e: self.scan())
        self.bind_all("<Control-f>", lambda e: (self.nb.select(0), self.search_entry.focus_set()))
        self.bind_all("<Control-e>", lambda e: self.export_csv())
        self.bind_all("<Control-d>", lambda e: self.show_details())
        self.bind_all("<Escape>", lambda e: backend.cancel_all() if self.busy else None)
        for i in range(6):
            self.bind_all(f"<Control-Key-{i + 1}>", lambda e, i=i: self.nb.select(i))

    # ---------- layout helpers ----------
    def _tree(self, parent, name, cols, widths, selectmode="extended"):
        frame = ttk.Frame(parent)
        tv = ttk.Treeview(frame, columns=[c[0] for c in cols], show="headings", selectmode=selectmode)
        for (cid, title), w in zip(cols, widths):
            tv.heading(cid, text=_(title), command=lambda c=cid, t=tv: self._sort(t, c, False))
            tv.column(cid, width=w, anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.trees.append(tv)
        self.tree_names[tv] = name
        return frame, tv

    def _sort(self, tv, col, reverse):
        top = tv.get_children("")
        parents = [i for i in top if tv.get_children(i)]
        for parent in (parents or [""]):
            kids = tv.get_children(parent)
            rows = sorted(((sort_key(tv.set(i, col)), i) for i in kids), reverse=reverse)
            for idx, (_k, i) in enumerate(rows):
                tv.move(i, parent, idx)
        tv.heading(col, command=lambda: self._sort(tv, col, not reverse))

    def _log_box(self, parent, height=7):
        t = tk.Text(parent, height=height, state="disabled", wrap="word")
        t.pack(fill="x", pady=(6, 0))
        self.logs.append(t)
        return t

    def _build_banner(self):
        if backend.is_admin():
            return
        self.banner = tk.Frame(self)
        self.banner.pack(fill="x")
        tk.Label(self.banner, anchor="w", text=_(
            "Нет прав администратора: сканирование работает, а установка и резервное копирование — нет."),
        ).pack(side="left", padx=8, pady=4)
        ttk.Button(self.banner, text=_("Перезапустить от администратора"), command=self._elevate).pack(side="right", padx=8, pady=3)

    def _build_statusbar(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", side="bottom")
        self.status = tk.StringVar(value=_("Готово"))
        ttk.Label(bar, textvariable=self.status, anchor="w").pack(side="left", fill="x", expand=True, padx=6)
        self.progress = ttk.Progressbar(bar, length=220, mode="determinate")
        self.progress.pack(side="left", padx=6, pady=3)
        self.cancel_btn = ttk.Button(bar, text=_("Отмена"), command=backend.cancel_all, state="disabled")
        self.cancel_btn.pack(side="left", padx=(0, 6))

    # ---------- tabs ----------
    def _build_devices(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text=_("Устройства"))
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 4))
        ttk.Button(top, text=_("Пересканировать"), command=self.scan).pack(side="left")
        ttk.Button(top, text=_("Подробности"), command=self.show_details).pack(side="left", padx=6)
        ttk.Button(top, text=_("Искать в каталоге Microsoft"), command=self.catalog_for_selected).pack(side="left")
        ttk.Button(top, text=_("Производитель и утилиты"), command=self.vendor_dialog).pack(side="left", padx=6)
        ttk.Button(top, text=_("Экспорт в CSV"), command=self.export_csv).pack(side="left")
        row2 = ttk.Frame(tab)
        row2.pack(fill="x", pady=(0, 6))
        ttk.Label(row2, text=_("Поиск:")).pack(side="left")
        self.search_entry = ttk.Entry(row2, textvariable=self.filter_text, width=26)
        self.search_entry.pack(side="left", padx=6)
        ttk.Label(row2, text=_("Устаревшим считать старше (лет):")).pack(side="left", padx=(12, 4))
        ttk.Spinbox(row2, from_=1, to=15, width=4, textvariable=self.old_years, command=self.render_devices).pack(side="left")
        lang = ttk.Combobox(row2, textvariable=self.lang, values=["ru", "en"], width=4, state="readonly")
        lang.pack(side="right")
        lang.bind("<<ComboboxSelected>>", lambda e: (self.save_prefs(), messagebox.showinfo(
            "DriverTool", _("Язык изменится после перезапуска программы."))))
        ttk.Label(row2, text="Language:").pack(side="right", padx=(10, 4))
        ttk.Checkbutton(row2, text=_("Тёмная тема"), variable=self.dark, command=self.apply_theme).pack(side="right", padx=6)
        row3 = ttk.Frame(tab)
        row3.pack(fill="x", pady=(0, 6))
        ttk.Checkbutton(row3, text=_("Скрыть драйверы Windows"), variable=self.hide_ms,
                        command=lambda: (self.render_devices(), self.save_prefs())).pack(side="left")
        ttk.Checkbutton(row3, text=_("Только проблемные и устаревшие"), variable=self.only_issues,
                        command=self.render_devices).pack(side="left", padx=10)
        ttk.Checkbutton(row3, text=_("Группировать по классам"), variable=self.group,
                        command=lambda: (self.render_devices(), self.save_prefs())).pack(side="left")
        frame, self.dev_tv = self._tree(tab, "devices", [
            ("name", "Устройство"), ("class", "Класс"), ("provider", "Поставщик"),
            ("version", "Версия"), ("date", "Дата"), ("status", "Состояние")],
            [300, 100, 160, 110, 90, 260])
        frame.pack(fill="both", expand=True)
        self.dev_tv.bind("<Double-1>", lambda e: self.show_details())
        self.dev_summary = tk.StringVar()
        ttk.Label(tab, textvariable=self.dev_summary).pack(anchor="w", pady=(4, 0))

    def _build_updates(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text=_("Обновления Windows"))
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 6))
        ttk.Button(top, text=_("Искать обновления"), command=self.search).pack(side="left")
        ttk.Button(top, text=_("Выбрать все"), command=lambda: self.upd_tv.selection_set(self.upd_tv.get_children())).pack(side="left", padx=6)
        ttk.Button(top, text=_("Установить выбранные"), command=self.install).pack(side="left")
        ttk.Checkbutton(top, text=_("Создать точку восстановления"), variable=self.restore_pt, command=self.save_prefs).pack(side="right")
        frame, self.upd_tv = self._tree(tab, "updates", [
            ("title", "Драйвер"), ("class", "Класс"), ("maker", "Производитель"),
            ("date", "Дата"), ("size", "МБ")], [440, 100, 180, 90, 60])
        frame.pack(fill="both", expand=True)
        self.upd_log = self._log_box(tab)

    def _build_catalog(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text=_("Каталог Microsoft"))
        ttk.Label(tab, wraplength=900, justify="left", text=_(
            "Поиск драйвера по коду оборудования в официальном каталоге Microsoft Update. "
            "Выберите устройство на вкладке «Устройства» и нажмите «Искать в каталоге». "
            "Перед установкой проверяются контрольная сумма и цифровая подпись файла.")).pack(anchor="w")
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=6)
        self.cat_target = tk.StringVar(value=_("Устройство не выбрано"))
        ttk.Label(top, textvariable=self.cat_target, font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(top, text=_("Скачать и установить"), command=self.catalog_install).pack(side="right")
        ttk.Button(top, text=_("Подробности"), command=self.catalog_details).pack(side="right", padx=6)
        frame, self.cat_tv = self._tree(tab, "catalog", [
            ("title", "Драйвер"), ("version", "Версия"), ("date", "Дата"),
            ("class", "Класс"), ("size", "МБ")], [460, 120, 90, 140, 70], selectmode="browse")
        frame.pack(fill="both", expand=True)
        self.cat_log = self._log_box(tab, height=6)

    def _build_auto(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text=_("Авто-подбор"))
        ttk.Label(tab, wraplength=900, justify="left", text=_(
            "Проверяет все устройства в каталоге Microsoft и показывает только драйверы новее установленных "
            "(версия выше и дата не старше). Понижение версии невозможно. Проверка идёт по одному запросу "
            "на устройство и может занять несколько минут.")).pack(anchor="w")
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=6)
        ttk.Button(top, text=_("Найти новее для всех"), command=self.auto_scan).pack(side="left")
        ttk.Button(top, text=_("Выбрать все"), command=lambda: self.auto_tv.selection_set(self.auto_tv.get_children())).pack(side="left", padx=6)
        ttk.Button(top, text=_("Установить выбранные"), command=self.auto_install).pack(side="left")
        self.auto_ms = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text=_("Включая драйверы Windows"), variable=self.auto_ms).pack(side="right")
        frame, self.auto_tv = self._tree(tab, "auto", [
            ("name", "Устройство"), ("installed", "Установлено"), ("version", "Доступно"),
            ("date", "Дата"), ("size", "МБ")], [380, 130, 130, 90, 70])
        frame.pack(fill="both", expand=True)
        self.auto_log = self._log_box(tab, height=6)

    def _build_maintenance(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text=_("Обслуживание"))
        ttk.Label(tab, font=("Segoe UI", 10, "bold"), text=_("Журнал установок")).pack(anchor="w")
        jt = ttk.Frame(tab)
        jt.pack(fill="x", pady=4)
        ttk.Button(jt, text=_("Обновить"), command=self.load_journal).pack(side="left")
        ttk.Button(jt, text=_("Откатить выбранное"), command=self.do_rollback).pack(side="left", padx=6)
        frame, self.jr_tv = self._tree(tab, "journal", [
            ("time", "Дата"), ("source", "Источник"), ("title", "Что установлено"),
            ("pkgs", "Пакетов"), ("state", "Состояние")], [130, 120, 420, 80, 140], selectmode="browse")
        frame.pack(fill="both", expand=True)
        ttk.Label(tab, font=("Segoe UI", 10, "bold"), text=_("Старые версии драйверов в хранилище Windows")).pack(anchor="w", pady=(8, 0))
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=4)
        ttk.Button(top, text=_("Найти старые версии"), command=self.find_stale).pack(side="left")
        ttk.Button(top, text=_("Выбрать все"), command=lambda: self.stale_tv.selection_set(self.stale_tv.get_children())).pack(side="left", padx=6)
        ttk.Button(top, text=_("Удалить выбранные"), command=self.delete_stale).pack(side="left")
        frame, self.stale_tv = self._tree(tab, "stale", [
            ("pkg", "Пакет"), ("inf", "Файл .inf"), ("provider", "Поставщик"), ("class", "Класс"),
            ("version", "Версия"), ("date", "Дата"), ("newer", "Есть новее")], [90, 170, 170, 100, 110, 90, 110])
        frame.pack(fill="both", expand=True)
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text=_("Что изменилось с последнего снимка"), command=self.show_changes).pack(side="left")
        ttk.Button(row, text=_("Точки восстановления Windows"), command=lambda: subprocess.Popen(["rstrui.exe"])).pack(side="left", padx=6)
        ttk.Button(row, text=_("Папка с данными"), command=lambda: (backend.DATA_DIR.mkdir(parents=True, exist_ok=True), os.startfile(backend.DATA_DIR))).pack(side="left")
        self.mnt_log = self._log_box(tab, height=5)

    def _build_backup(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text=_("Резервная копия"))
        ttk.Label(tab, wraplength=800, justify="left", text=_(
            "Резервная копия сохраняет все сторонние драйверы из хранилища Windows в папку. "
            "Её можно восстановить после переустановки системы или неудачного обновления.")).pack(anchor="w")
        row = ttk.Frame(tab)
        row.pack(anchor="w", pady=10)
        ttk.Button(row, text=_("Создать копию…"), command=self.backup).pack(side="left")
        ttk.Button(row, text=_("Восстановить из папки…"), command=self.restore).pack(side="left", padx=8)
        self.bk_log = self._log_box(tab, height=12)

    # ---------- infrastructure ----------
    def _elevate(self):
        backend.relaunch_as_admin()
        self._close()

    def _pump(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def ui(self, fn):
        self.q.put(fn)

    def set_progress(self, frac):
        self.ui(lambda: (self.progress.stop(), self.progress.configure(mode="determinate", value=frac * 100)))

    def run_bg(self, work, done=None, msg="Работаю…"):
        if self.busy:
            messagebox.showinfo(_("Занято"), _("Дождитесь окончания текущей операции или нажмите «Отмена»."))
            return
        self.busy = True
        backend.reset_cancel()
        self.status.set(_(msg))
        LOG.info("start: %s", msg)
        self.config(cursor="watch")
        self.cancel_btn.configure(state="normal")
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)

        def target():
            err = res = None
            try:
                res = work()
            except Exception as e:  # noqa: BLE001 — surface any backend failure to the user
                err = e
                if not isinstance(e, backend.Cancelled):
                    LOG.exception("failed: %s", msg)

            def finish():
                self.busy = False
                self.config(cursor="")
                self.cancel_btn.configure(state="disabled")
                self.progress.stop()
                self.progress.configure(mode="determinate", value=0)
                if isinstance(err, backend.Cancelled):
                    self.status.set(_("Отменено"))
                elif err:
                    text = backend.friendly_error(err)
                    self.status.set(_("Ошибка: {text}", text=text))
                    messagebox.showerror(_("Ошибка"), text)
                else:
                    self.status.set(_("Готово"))
                    if done:
                        done(res)
            self.ui(finish)
        threading.Thread(target=target, daemon=True).start()

    def _append(self, box, text):
        box.configure(state="normal")
        box.insert("end", text + "\n")
        box.see("end")
        box.configure(state="disabled")

    def logger(self, box):
        def log(m):
            LOG.info(m)
            self.ui(lambda: self._append(box, m))
        return log

    def _need_admin(self) -> bool:
        if backend.is_admin():
            return True
        messagebox.showwarning(_("Нужны права администратора"),
                               _("Для этой операции перезапустите программу от администратора."))
        return False

    def _dialog(self, title, text):
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("640x420")
        win.configure(bg=self.pal["bg"])
        t = tk.Text(win, wrap="word", bg=self.pal["log"], fg=self.pal["fg"])
        t.insert("1.0", text)
        t.configure(state="disabled")
        t.pack(fill="both", expand=True, padx=8, pady=8)

    # ---------- devices ----------
    def scan(self):
        self.run_bg(lambda: {**backend.scan_devices(), "maker": backend.system_manufacturer()},
                    self._scan_done, "Сканирую устройства…")

    def _scan_done(self, data):
        self.maker = data.pop("maker", "")
        self.devices = data
        # forget upgrades that are no longer upgrades (e.g. just installed)
        by_id = {d["deviceId"]: d for d in data["drivers"]}
        self.avail = {k: v for k, v in self.avail.items() if k in by_id and backend.is_newer(
            v["version"], v["date"], by_id[k]["version"], by_id[k]["date"])}
        self.render_devices()

    def render_devices(self):
        tv = self.dev_tv
        tv.delete(*tv.get_children())
        self.row_dev = {}
        try:
            years = max(1, int(self.old_years.get()))
        except (tk.TclError, ValueError):
            years = 3
        cutoff = (date.today() - timedelta(days=365 * years)).isoformat()
        needle = self.filter_text.get().strip().lower()
        n_problem = n_old = 0
        groups: dict[str, str] = {}

        def match(*fields):
            return not needle or needle in " ".join(f or "" for f in fields).lower()

        def parent_for(cls):
            if not self.group.get():
                return ""
            cls = cls or "—"  # a None/empty value would be dropped by ttk and shift the options
            if cls not in groups:
                groups[cls] = tv.insert("", "end", open=True, values=(f"▸ {cls}", "", "", "", "", ""))
            return groups[cls]

        for p in self.devices["problems"]:
            n_problem += 1
            if not match(p["name"], p["reason"]):
                continue
            iid = tv.insert(parent_for(_("Проблемы")), "end",
                            values=(p["name"], "", "", "", "", f"⚠ {p['reason']}"), tags=("problem",))
            self.row_dev[iid] = {"name": p["name"], "hwid": p.get("hwid") or [p["deviceId"]],
                                 "deviceId": p["deviceId"], "problem": p["reason"]}

        for d in sorted(self.devices["drivers"], key=lambda d: (d["class"] or "", d["name"])):
            is_ms = (d["provider"] or "").lower().startswith("microsoft")
            old = bool(d["date"]) and d["date"] < cutoff
            up = self.avail.get(d["deviceId"])
            if old and not is_ms:
                n_old += 1
            if (self.hide_ms.get() and is_ms and not up) or (self.only_issues.get() and not old and not up):
                continue
            if not match(d["name"], d["class"], d["provider"]):
                continue
            if up:
                status, tags = _("Доступно новее: {ver}", ver=up["version"]), ("avail",)
            elif old:
                status, tags = _("Старше {years} лет", years=years), ("old",)
            else:
                status, tags = "OK", ()
            iid = tv.insert(parent_for(d["class"]), "end", tags=tags, values=(
                d["name"], d["class"] or "", d["provider"] or "", d["version"] or "", d["date"] or "", status))
            self.row_dev[iid] = d
        self.dev_summary.set(_("Проблемных устройств: {p} · Старше {y} лет (не Microsoft): {o} · Новее в каталоге: {a}",
                               p=n_problem, y=years, o=n_old, a=len(self.avail)))

    def _selected_device(self):
        sel = self.dev_tv.selection()
        d = self.row_dev.get(sel[0]) if sel else None
        if not d:
            messagebox.showinfo(_("Устройство"), _("Выберите устройство в списке."))
        return d

    def show_details(self):
        d = self._selected_device()
        if not d:
            return
        lines = [_("Устройство: {v}", v=d["name"]), _("Класс: {v}", v=d.get("class") or ""),
                 _("Поставщик: {v}", v=d.get("provider") or ""), _("Версия: {v}", v=d.get("version") or ""),
                 _("Дата: {v}", v=d.get("date") or ""), f"INF: {d.get('inf') or ''}",
                 _("Подписан: {v}", v=_("да") if d.get("signed") else _("нет/неизвестно")),
                 _("ID устройства: {v}", v=d.get("deviceId") or ""), _("Коды оборудования:")]
        lines += [f"  {h}" for h in d.get("hwid", [])]
        if d.get("problem"):
            lines.insert(1, _("Проблема: {v}", v=d["problem"]))
        if d.get("deviceId") in self.avail:
            a = self.avail[d["deviceId"]]
            lines.append("\n" + _("Новее в каталоге: {t} ({v}, {d})", t=a["title"], v=a["version"], d=a["date"]))
        self._dialog(_("Подробности устройства"), "\n".join(lines))

    def vendor_dialog(self):
        d = self._selected_device()
        if not d:
            return
        hw = d.get("hwid", [])
        links = backend.support_links(hw, self.maker)
        tools = backend.suggested_tools(hw, self.maker)
        win = tk.Toplevel(self)
        win.title(_("Производитель и утилиты"))
        win.configure(bg=self.pal["bg"])
        win.geometry("480x300")
        ttk.Label(win, text=d["name"], font=("Segoe UI", 10, "bold"), wraplength=440).pack(anchor="w", padx=12, pady=(12, 6))
        if not links and not tools:
            ttk.Label(win, wraplength=440, text=_("Для этого оборудования ничего не известно. "
                                                  "Используйте поиск в каталоге Microsoft.")).pack(padx=12, pady=6, anchor="w")
        for name, url in links:
            ttk.Button(win, text=_("Открыть сайт: {name}", name=name), command=lambda u=url: webbrowser.open(u)).pack(fill="x", padx=12, pady=3)
        for name, pid in tools:
            ttk.Button(win, text=_("Установить через winget: {name}", name=name),
                       command=lambda n=name, p=pid: (win.destroy(), self.install_tool(n, p))).pack(fill="x", padx=12, pady=3)
        ttk.Label(win, wraplength=440, text=_(
            "Драйверы NVIDIA и AMD в winget отсутствуют — для них доступна только страница загрузки производителя.")).pack(padx=12, pady=10, anchor="w")

    def install_tool(self, name, pkg_id):
        if not messagebox.askyesno(_("Установка"), _("Установить {name} через winget?\nПакет: {id}", name=name, id=pkg_id)):
            return
        log = self.logger(self.mnt_log)
        self.nb.select(4)
        self.run_bg(lambda: backend.winget_install(pkg_id, log),
                    lambda _r: self._append(self.mnt_log, _("Установлено: {name}", name=name)),
                    "Устанавливаю утилиту через winget…")

    def export_csv(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialfile=f"drivers-{date.today().isoformat()}.csv")
        if not path:
            return
        rows = []
        for i in self.dev_tv.get_children(""):
            kids = self.dev_tv.get_children(i)
            for k in (kids or [i]):
                rows.append([self.dev_tv.set(k, c) for c in self.dev_tv["columns"]])
        backend.export_csv(Path(path), rows, [self.dev_tv.heading(c)["text"] for c in self.dev_tv["columns"]])
        self.status.set(_("Сохранено: {path}", path=path))

    # ---------- Windows Update ----------
    def search(self):
        self.upd_tv.delete(*self.upd_tv.get_children())
        self._append(self.upd_log, _("Поиск драйверов в Windows Update…"))
        self.run_bg(backend.search_updates, self._search_done, "Ищу обновления (может занять пару минут)…")

    def _search_done(self, updates):
        self.updates = updates
        for u in updates:
            self.upd_tv.insert("", "end", iid=u["id"], values=(
                u["title"], u["class"], u["manufacturer"], u["date"], u["sizeMB"]))
        self._append(self.upd_log, _("Найдено обновлений драйверов: {n}", n=len(updates)))

    def _snapshot(self):
        if self.devices["drivers"]:
            backend.save_snapshot(self.devices["drivers"])

    def install(self):
        ids = list(self.upd_tv.selection())
        if not ids:
            messagebox.showinfo(_("Установка"), _("Выберите драйверы в списке."))
            return
        if not self._need_admin() or not messagebox.askyesno(_("Установка"), _("Установить драйверов: {n}?", n=len(ids))):
            return
        self._snapshot()
        log = self.logger(self.upd_log)
        title = _("Windows Update: {n} драйв.", n=len(ids))
        self.run_bg(lambda: backend.journaled(title, "Windows Update",
                                              lambda: backend.install_updates(ids, self.restore_pt.get(), log)),
                    self._install_done, "Устанавливаю драйверы…")

    def _install_done(self, r):
        self._append(self.upd_log, _("Установлено: {ok}, ошибок: {bad}", ok=r["installed"], bad=r["failed"]))
        if r["reboot"]:
            messagebox.showinfo(_("Установка"), _("Для завершения нужна перезагрузка."))
        self.scan()
        self.load_journal()

    # ---------- Microsoft Update Catalog ----------
    def catalog_for_selected(self):
        d = self._selected_device()
        if not d:
            return
        hwids = d.get("hwid") or [d["deviceId"]]
        self.nb.select(2)
        self.cat_target.set(d["name"])
        self.cat_tv.delete(*self.cat_tv.get_children())
        self.catalog_results = {}
        self._append(self.cat_log, _("Ищу в каталоге: {hw}", hw=hwids[0]))
        self.run_bg(lambda: backend.catalog_search(hwids), self._catalog_done, "Ищу в каталоге Microsoft…")

    def _catalog_done(self, results):
        for r in results:
            self.catalog_results[r["guid"]] = r
            self.cat_tv.insert("", "end", iid=r["guid"], values=(
                r["title"], r["version"], r["date"], r["class"], r["sizeMB"]))
        self._append(self.cat_log, _("Найдено драйверов: {n}", n=len(results)) if results
                     else _("В каталоге ничего не найдено для этого оборудования."))

    def _selected_catalog(self):
        sel = self.cat_tv.selection()
        if not sel:
            messagebox.showinfo(_("Каталог"), _("Выберите драйвер в списке."))
            return None
        return self.catalog_results[sel[0]]

    def catalog_details(self):
        r = self._selected_catalog()
        if r:
            self._dialog(_("Драйвер из каталога"), _(
                "{title}\n\nВерсия: {ver}\nДата: {date}\nКласс: {cls}\nРазмер: {size} МБ\n\nПрименим к:\n{prod}",
                title=r["title"], ver=r["version"], date=r["date"], cls=r["class"], size=r["sizeMB"], prod=r["products"]))

    def catalog_install(self):
        r = self._selected_catalog()
        if not r or not self._need_admin():
            return
        if not messagebox.askyesno(_("Установка"), _(
                "Скачать ({size} МБ) и установить?\n\n{title}\n\nПроверьте, что дата и версия новее установленной.",
                size=r["sizeMB"], title=r["title"])):
            return
        self._snapshot()
        log = self.logger(self.cat_log)

        def work():
            if self.restore_pt.get():
                log(_("Создаю точку восстановления…"))
                log(backend.create_restore_point())
            return backend.journaled(r["title"], _("Каталог Microsoft"), lambda: backend.install_from_catalog(
                r["guid"], r["title"], r["sizeMB"], log, self.set_progress))

        self.run_bg(work, self._catalog_installed, "Устанавливаю драйвер из каталога…")

    def _catalog_installed(self, res):
        self._append(self.cat_log, _("Установлено: {name}", name=res["title"]))
        if res["reboot"]:
            messagebox.showinfo(_("Установка"), _("Для завершения нужна перезагрузка."))
        self.scan()
        self.load_journal()

    # ---------- auto-scan ----------
    def auto_scan(self):
        self.auto_tv.delete(*self.auto_tv.get_children())
        self.auto_results = {}
        log = self.logger(self.auto_log)
        drivers = list(self.devices["drivers"])
        if not drivers:
            messagebox.showinfo(_("Авто-подбор"), _("Сначала дождитесь сканирования устройств."))
            return
        log(_("Проверяю устройства в каталоге Microsoft…"))
        self.run_bg(lambda: backend.auto_scan(drivers, self.set_progress, log, self.auto_ms.get()),
                    self._auto_done, "Проверяю все устройства в каталоге…")

    def _auto_done(self, found):
        for r in found:  # keyed by device: one catalog package can fit several devices
            self.auto_results[r["deviceId"]] = r
            self.avail[r["deviceId"]] = r
            self.auto_tv.insert("", "end", iid=r["deviceId"], values=(
                r["name"], r["installed"], r["version"], r["date"], r["sizeMB"]))
        self._append(self.auto_log, _("Найдено обновлений: {n}", n=len(found)))
        self.render_devices()

    def auto_install(self):
        sel = list(self.auto_tv.selection())
        if not sel or not self._need_admin():
            if not sel:
                messagebox.showinfo(_("Установка"), _("Выберите драйверы в списке."))
            return
        items = list({self.auto_results[i]["guid"]: self.auto_results[i] for i in sel}.values())  # each package once
        total = sum(i["sizeMB"] for i in items)
        if not messagebox.askyesno(_("Установка"), _("Скачать и установить драйверов: {n} (≈{mb} МБ)?", n=len(items), mb=round(total))):
            return
        self._snapshot()
        log = self.logger(self.auto_log)

        def work():
            if self.restore_pt.get():
                log(_("Создаю точку восстановления…"))
                log(backend.create_restore_point())
            ok = bad = 0
            reboot = False
            for n, it in enumerate(items):
                log(f"[{n + 1}/{len(items)}] {it['name']}")
                try:
                    res = backend.journaled(it["title"], _("Каталог Microsoft"), lambda: backend.install_from_catalog(
                        it["guid"], it["title"], it["sizeMB"], log, self.set_progress))
                    ok += 1
                    reboot = reboot or res["reboot"]
                except backend.Cancelled:
                    raise
                except Exception as e:  # noqa: BLE001 — one failed driver must not stop the batch
                    bad += 1
                    LOG.exception("batch item failed")
                    log(_("Ошибка: {text}", text=backend.friendly_error(e)))
            return {"ok": ok, "bad": bad, "reboot": reboot}

        self.run_bg(work, self._auto_installed, "Устанавливаю драйверы из каталога…")

    def _auto_installed(self, r):
        self._append(self.auto_log, _("Установлено: {ok}, ошибок: {bad}", ok=r["ok"], bad=r["bad"]))
        if r["reboot"]:
            messagebox.showinfo(_("Установка"), _("Для завершения нужна перезагрузка."))
        self.auto_tv.delete(*self.auto_tv.get_children())
        self.scan()
        self.load_journal()

    # ---------- maintenance ----------
    def load_journal(self):
        self.jr_tv.delete(*self.jr_tv.get_children())
        for e in reversed(backend.journal_load()):
            state = _("откатано") if e["rolled_back"] else (_("можно откатить") if e["packages"] else _("нет данных для отката"))
            self.jr_tv.insert("", "end", iid=e["id"], values=(e["time"], e["source"], e["title"], len(e["packages"]), state))

    def do_rollback(self):
        sel = self.jr_tv.selection()
        if not sel or not self._need_admin():
            if not sel:
                messagebox.showinfo(_("Откат"), _("Выберите запись в журнале."))
            return
        if not messagebox.askyesno(_("Откат"), _(
                "Удалить установленные этим действием пакеты драйверов? Windows вернётся к предыдущему драйверу.")):
            return
        log = self.logger(self.mnt_log)
        self.run_bg(lambda: backend.rollback(sel[0], log),
                    lambda n: (self._append(self.mnt_log, _("Откат выполнен, удалено пакетов: {n}", n=n)),
                               self.load_journal(), self.scan()),
                    "Откатываю драйвер…")

    def find_stale(self):
        if not self._need_admin():
            return
        self.stale_tv.delete(*self.stale_tv.get_children())
        self.stale = {}
        self.run_bg(backend.stale_drivers, self._stale_done, "Анализирую хранилище драйверов…")

    def _stale_done(self, items):
        for s in items:
            self.stale[s["published"]] = s
            self.stale_tv.insert("", "end", iid=s["published"], values=(
                s["published"], s["inf"], s["provider"], s["class"], s["version"], s["date"], s["newer"]))
        self._append(self.mnt_log, _("Старых версий найдено: {n}", n=len(items)))

    def delete_stale(self):
        sel = list(self.stale_tv.selection())
        if not sel or not self._need_admin():
            return
        if not messagebox.askyesno(_("Удаление"), _(
                "Удалить старых пакетов: {n}?\nПакеты, которые сейчас используются, будут пропущены.", n=len(sel))):
            return
        log = self.logger(self.mnt_log)
        self.run_bg(lambda: backend.delete_drivers(sel, log),
                    lambda r: (self._append(self.mnt_log, _("Удалено: {ok}, пропущено: {bad}", ok=r[0], bad=r[1])), self.find_stale()),
                    "Удаляю старые пакеты…")

    def show_changes(self):
        snap = backend.latest_snapshot()
        if not snap:
            self._append(self.mnt_log, _("Снимков пока нет — они создаются перед каждой установкой."))
            return
        lines = backend.diff_snapshot(snap, self.devices["drivers"])
        self._append(self.mnt_log, _("Снимок от {when}: изменено драйверов — {n}", when=snap.stem, n=len(lines)))
        for ln in lines:
            self._append(self.mnt_log, "  " + ln)

    # ---------- backup ----------
    def backup(self):
        if not self._need_admin():
            return
        folder = filedialog.askdirectory(title=_("Куда сохранить копию драйверов"))
        if not folder:
            return
        dest = Path(folder) / f"drivers-{date.today().isoformat()}"
        log = self.logger(self.bk_log)
        self.run_bg(lambda: backend.backup_drivers(dest, log),
                    lambda n: self._append(self.bk_log, _("Готово: {n} драйверов в {dest}", n=n, dest=dest)),
                    "Копирую драйверы…")

    def restore(self):
        if not self._need_admin():
            return
        folder = filedialog.askdirectory(title=_("Папка с копией драйверов"))
        if not folder or not messagebox.askyesno(_("Восстановление"), _("Установить все драйверы из этой папки?")):
            return
        log = self.logger(self.bk_log)
        self.run_bg(lambda: backend.restore_drivers(Path(folder), log),
                    lambda _r: self._append(self.bk_log, _("Восстановление завершено.")),
                    "Восстанавливаю драйверы…")


if __name__ == "__main__":
    App().mainloop()
