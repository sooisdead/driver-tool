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
from urllib.parse import quote

import backend

PALETTES = {
    "light": dict(bg="#f0f0f0", fg="#000000", field="#ffffff", sel="#cce4f7", banner="#fff3cd",
                  problem="#ffd6d6", old="#fff3cd", log="#f7f7f7"),
    "dark": dict(bg="#2b2b2b", fg="#e6e6e6", field="#1e1e1e", sel="#3d5a80", banner="#5c4a1a",
                 problem="#6b2b2b", old="#5c4a1a", log="#1e1e1e"),
}


def sort_key(v: str):
    """Numbers and dotted versions sort numerically, everything else as text."""
    if re.fullmatch(r"\d+(\.\d+)*", v or ""):
        return (0, [int(x) for x in v.split(".")], "")
    return (1, [], (v or "").lower())


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DriverTool")
        self.geometry("1100x700")
        self.minsize(900, 540)
        self.q: queue.Queue = queue.Queue()
        self.devices: dict = {"drivers": [], "problems": []}
        self.updates: list[dict] = []
        self.catalog_results: dict[str, dict] = {}
        self.stale: dict[str, dict] = {}
        self.row_dev: dict[str, dict] = {}
        self.busy = False
        self.logs: list[tk.Text] = []

        s = backend.load_settings()
        self.old_years = tk.IntVar(value=int(s.get("old_years", 3)))
        self.dark = tk.BooleanVar(value=bool(s.get("dark", False)))
        self.hide_ms = tk.BooleanVar(value=bool(s.get("hide_ms", True)))
        self.restore_pt = tk.BooleanVar(value=bool(s.get("restore_point", True)))
        self.only_issues = tk.BooleanVar(value=False)
        self.filter_text = tk.StringVar()

        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self._build_banner()
        self._build_statusbar()
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(4, 0))
        self._build_devices()
        self._build_updates()
        self._build_catalog()
        self._build_maintenance()
        self._build_backup()
        self.apply_theme()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.old_years.trace_add("write", lambda *_: self.render_devices())
        self.filter_text.trace_add("write", lambda *_: self.render_devices())
        self.after(100, self._pump)
        self.after(300, self.scan)

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
        for tv in self.trees:
            tv.tag_configure("problem", background=p["problem"])
            tv.tag_configure("old", background=p["old"])
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
        backend.save_settings({"old_years": years, "dark": self.dark.get(),
                               "hide_ms": self.hide_ms.get(), "restore_point": self.restore_pt.get()})

    def _close(self):
        self.save_prefs()
        backend.cancel_all()
        self.destroy()

    # ---------- layout helpers ----------
    trees: list

    def _tree(self, parent, cols, widths, selectmode="extended"):
        if not hasattr(self, "trees"):
            self.trees = []
        frame = ttk.Frame(parent)
        tv = ttk.Treeview(frame, columns=[c[0] for c in cols], show="headings", selectmode=selectmode)
        for (cid, title), w in zip(cols, widths):
            tv.heading(cid, text=title, command=lambda c=cid, t=tv: self._sort(t, c, False))
            tv.column(cid, width=w, anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.trees.append(tv)
        return frame, tv

    def _sort(self, tv, col, reverse):
        rows = [(sort_key(tv.set(i, col)), i) for i in tv.get_children("")]
        rows.sort(reverse=reverse)
        for idx, (_, i) in enumerate(rows):
            tv.move(i, "", idx)
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
        tk.Label(self.banner, anchor="w",
                 text="Нет прав администратора: сканирование работает, а установка и резервное копирование — нет.",
                 ).pack(side="left", padx=8, pady=4)
        ttk.Button(self.banner, text="Перезапустить от администратора", command=self._elevate).pack(side="right", padx=8, pady=3)

    def _build_statusbar(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", side="bottom")
        self.status = tk.StringVar(value="Готово")
        ttk.Label(bar, textvariable=self.status, anchor="w").pack(side="left", fill="x", expand=True, padx=6)
        self.progress = ttk.Progressbar(bar, length=220, mode="determinate")
        self.progress.pack(side="left", padx=6, pady=3)
        self.cancel_btn = ttk.Button(bar, text="Отмена", command=backend.cancel_all, state="disabled")
        self.cancel_btn.pack(side="left", padx=(0, 6))

    # ---------- tabs ----------
    def _build_devices(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Устройства")
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 4))
        ttk.Button(top, text="Пересканировать", command=self.scan).pack(side="left")
        ttk.Button(top, text="Подробности", command=self.show_details).pack(side="left", padx=6)
        ttk.Button(top, text="Искать в каталоге Microsoft", command=self.catalog_for_selected).pack(side="left")
        ttk.Button(top, text="Сайт производителя", command=self.open_vendor).pack(side="left", padx=6)
        ttk.Button(top, text="Экспорт в CSV", command=self.export_csv).pack(side="left")
        row2 = ttk.Frame(tab)
        row2.pack(fill="x", pady=(0, 6))
        ttk.Label(row2, text="Поиск:").pack(side="left")
        ttk.Entry(row2, textvariable=self.filter_text, width=28).pack(side="left", padx=6)
        ttk.Label(row2, text="Устаревшим считать старше (лет):").pack(side="left", padx=(12, 4))
        ttk.Spinbox(row2, from_=1, to=15, width=4, textvariable=self.old_years, command=self.render_devices).pack(side="left")
        ttk.Checkbutton(row2, text="Тёмная тема", variable=self.dark, command=self.apply_theme).pack(side="right")
        ttk.Checkbutton(row2, text="Только проблемные", variable=self.only_issues, command=self.render_devices).pack(side="right", padx=10)
        ttk.Checkbutton(row2, text="Скрыть драйверы Windows", variable=self.hide_ms, command=lambda: (self.render_devices(), self.save_prefs())).pack(side="right")
        frame, self.dev_tv = self._tree(tab, [
            ("name", "Устройство"), ("class", "Класс"), ("provider", "Поставщик"),
            ("version", "Версия"), ("date", "Дата"), ("status", "Состояние")],
            [300, 100, 160, 110, 90, 240])
        frame.pack(fill="both", expand=True)
        self.dev_tv.bind("<Double-1>", lambda e: self.show_details())
        self.dev_summary = tk.StringVar()
        ttk.Label(tab, textvariable=self.dev_summary).pack(anchor="w", pady=(4, 0))

    def _build_updates(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Обновления Windows")
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 6))
        ttk.Button(top, text="Искать обновления", command=self.search).pack(side="left")
        ttk.Button(top, text="Выбрать все", command=lambda: self.upd_tv.selection_set(self.upd_tv.get_children())).pack(side="left", padx=6)
        ttk.Button(top, text="Установить выбранные", command=self.install).pack(side="left")
        ttk.Checkbutton(top, text="Создать точку восстановления", variable=self.restore_pt, command=self.save_prefs).pack(side="right")
        frame, self.upd_tv = self._tree(tab, [
            ("title", "Драйвер"), ("class", "Класс"), ("maker", "Производитель"),
            ("date", "Дата"), ("size", "МБ")], [440, 100, 180, 90, 60])
        frame.pack(fill="both", expand=True)
        self.upd_log = self._log_box(tab)

    def _build_catalog(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Каталог Microsoft")
        ttk.Label(tab, wraplength=900, justify="left", text=(
            "Поиск драйвера по коду оборудования в официальном каталоге Microsoft Update. "
            "Выберите устройство на вкладке «Устройства» и нажмите «Искать в каталоге». "
            "Перед установкой проверяются контрольная сумма и цифровая подпись файла.")).pack(anchor="w")
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=6)
        self.cat_target = tk.StringVar(value="Устройство не выбрано")
        ttk.Label(top, textvariable=self.cat_target, font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(top, text="Скачать и установить", command=self.catalog_install).pack(side="right")
        ttk.Button(top, text="Подробности", command=self.catalog_details).pack(side="right", padx=6)
        frame, self.cat_tv = self._tree(tab, [
            ("title", "Драйвер"), ("version", "Версия"), ("date", "Дата"),
            ("class", "Класс"), ("size", "МБ")], [460, 120, 90, 140, 70], selectmode="browse")
        frame.pack(fill="both", expand=True)
        self.cat_log = self._log_box(tab, height=6)

    def _build_maintenance(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Обслуживание")
        ttk.Label(tab, font=("Segoe UI", 10, "bold"), text="Старые версии драйверов в хранилище Windows").pack(anchor="w")
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=6)
        ttk.Button(top, text="Найти старые версии", command=self.find_stale).pack(side="left")
        ttk.Button(top, text="Выбрать все", command=lambda: self.stale_tv.selection_set(self.stale_tv.get_children())).pack(side="left", padx=6)
        ttk.Button(top, text="Удалить выбранные", command=self.delete_stale).pack(side="left")
        frame, self.stale_tv = self._tree(tab, [
            ("pkg", "Пакет"), ("inf", "Файл .inf"), ("provider", "Поставщик"), ("class", "Класс"),
            ("version", "Версия"), ("date", "Дата"), ("newer", "Есть новее")], [90, 170, 170, 100, 110, 90, 110])
        frame.pack(fill="both", expand=True)
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="Что изменилось с последнего снимка", command=self.show_changes).pack(side="left")
        ttk.Button(row, text="Точки восстановления Windows", command=lambda: subprocess.Popen(["rstrui.exe"])).pack(side="left", padx=6)
        ttk.Button(row, text="Папка с данными", command=lambda: (backend.DATA_DIR.mkdir(parents=True, exist_ok=True), os.startfile(backend.DATA_DIR))).pack(side="left")
        self.mnt_log = self._log_box(tab, height=6)

    def _build_backup(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text="Резервная копия")
        ttk.Label(tab, wraplength=800, justify="left", text=(
            "Резервная копия сохраняет все сторонние драйверы из хранилища Windows в папку. "
            "Её можно восстановить после переустановки системы или неудачного обновления.")).pack(anchor="w")
        row = ttk.Frame(tab)
        row.pack(anchor="w", pady=10)
        ttk.Button(row, text="Создать копию…", command=self.backup).pack(side="left")
        ttk.Button(row, text="Восстановить из папки…", command=self.restore).pack(side="left", padx=8)
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
            messagebox.showinfo("Занято", "Дождитесь окончания текущей операции или нажмите «Отмена».")
            return
        self.busy = True
        backend.reset_cancel()
        self.status.set(msg)
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

            def finish():
                self.busy = False
                self.config(cursor="")
                self.cancel_btn.configure(state="disabled")
                self.progress.stop()
                self.progress.configure(mode="determinate", value=0)
                if isinstance(err, backend.Cancelled):
                    self.status.set("Отменено")
                elif err:
                    text = backend.friendly_error(err)
                    self.status.set(f"Ошибка: {text}")
                    messagebox.showerror("Ошибка", text)
                else:
                    self.status.set("Готово")
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
        return lambda m: self.ui(lambda: self._append(box, m))

    def _need_admin(self) -> bool:
        if backend.is_admin():
            return True
        messagebox.showwarning("Нужны права администратора",
                               "Для этой операции перезапустите программу от администратора.")
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
        self.run_bg(backend.scan_devices, self._scan_done, "Сканирую устройства…")

    def _scan_done(self, data):
        self.devices = data
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

        def match(*fields):
            return not needle or needle in " ".join(f or "" for f in fields).lower()

        for p in self.devices["problems"]:
            n_problem += 1
            if not match(p["name"], p["reason"]):
                continue
            iid = tv.insert("", "end", values=(p["name"], "", "", "", "", f"⚠ {p['reason']}"), tags=("problem",))
            self.row_dev[iid] = {"name": p["name"], "hwid": p.get("hwid") or [p["deviceId"]],
                                 "deviceId": p["deviceId"], "problem": p["reason"]}

        for d in sorted(self.devices["drivers"], key=lambda d: (d["class"] or "", d["name"])):
            is_ms = (d["provider"] or "").lower().startswith("microsoft")
            old = bool(d["date"]) and d["date"] < cutoff
            if old and not is_ms:
                n_old += 1
            if (self.hide_ms.get() and is_ms) or (self.only_issues.get() and not old):
                continue
            if not match(d["name"], d["class"], d["provider"]):
                continue
            iid = tv.insert("", "end", tags=("old",) if old else (), values=(
                d["name"], d["class"], d["provider"], d["version"], d["date"],
                f"Старше {years} лет" if old else "OK"))
            self.row_dev[iid] = d
        self.dev_summary.set(f"Проблемных устройств: {n_problem} · Драйверов старше {years} лет (не Microsoft): {n_old}")

    def _selected_device(self):
        sel = self.dev_tv.selection()
        if not sel:
            messagebox.showinfo("Устройство", "Выберите устройство в списке.")
            return None
        return self.row_dev.get(sel[0])

    def show_details(self):
        d = self._selected_device()
        if not d:
            return
        lines = [f"Устройство: {d['name']}", f"Класс: {d.get('class', '')}",
                 f"Поставщик: {d.get('provider', '')}", f"Версия: {d.get('version', '')}",
                 f"Дата: {d.get('date', '')}", f"INF: {d.get('inf', '')}",
                 f"Подписан: {'да' if d.get('signed') else 'нет/неизвестно'}",
                 f"ID устройства: {d.get('deviceId', '')}", "Коды оборудования:"]
        lines += [f"  {h}" for h in d.get("hwid", [])]
        if d.get("problem"):
            lines.insert(1, f"Проблема: {d['problem']}")
        self._dialog("Подробности устройства", "\n".join(lines))

    def open_vendor(self):
        d = self._selected_device()
        if not d:
            return
        v = backend.vendor_page(d.get("hwid", []))
        if not v:
            messagebox.showinfo("Сайт производителя", "Для этого оборудования страница производителя неизвестна. "
                                "Используйте поиск в каталоге Microsoft.")
            return
        webbrowser.open(v[1])

    def export_csv(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialfile=f"drivers-{date.today().isoformat()}.csv")
        if not path:
            return
        rows = [[self.dev_tv.set(i, c) for c in self.dev_tv["columns"]] for i in self.dev_tv.get_children()]
        backend.export_csv(Path(path), rows, [self.dev_tv.heading(c)["text"] for c in self.dev_tv["columns"]])
        self.status.set(f"Сохранено: {path}")

    # ---------- Windows Update ----------
    def search(self):
        self.upd_tv.delete(*self.upd_tv.get_children())
        self._append(self.upd_log, "Поиск драйверов в Windows Update…")
        self.run_bg(backend.search_updates, self._search_done, "Ищу обновления (может занять пару минут)…")

    def _search_done(self, updates):
        self.updates = updates
        for u in updates:
            self.upd_tv.insert("", "end", iid=u["id"], values=(
                u["title"], u["class"], u["manufacturer"], u["date"], u["sizeMB"]))
        self._append(self.upd_log, f"Найдено обновлений драйверов: {len(updates)}")

    def _snapshot(self):
        if self.devices["drivers"]:
            backend.save_snapshot(self.devices["drivers"])

    def install(self):
        ids = list(self.upd_tv.selection())
        if not ids:
            messagebox.showinfo("Установка", "Выберите драйверы в списке.")
            return
        if not self._need_admin() or not messagebox.askyesno("Установка", f"Установить драйверов: {len(ids)}?"):
            return
        self._snapshot()
        log = self.logger(self.upd_log)
        self.run_bg(lambda: backend.install_updates(ids, self.restore_pt.get(), log),
                    self._install_done, "Устанавливаю драйверы…")

    def _install_done(self, r):
        self._append(self.upd_log, f"Установлено: {r['installed']}, ошибок: {r['failed']}")
        if r["reboot"]:
            messagebox.showinfo("Установка", "Для завершения нужна перезагрузка.")
        self.scan()

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
        self._append(self.cat_log, f"Ищу в каталоге: {hwids[0]}")
        self.run_bg(lambda: backend.catalog_search(hwids), self._catalog_done, "Ищу в каталоге Microsoft…")

    def _catalog_done(self, results):
        for r in results:
            self.catalog_results[r["guid"]] = r
            self.cat_tv.insert("", "end", iid=r["guid"], values=(
                r["title"], r["version"], r["date"], r["class"], r["sizeMB"]))
        self._append(self.cat_log, f"Найдено драйверов: {len(results)}" if results
                     else "В каталоге ничего не найдено для этого оборудования.")

    def _selected_catalog(self):
        sel = self.cat_tv.selection()
        if not sel:
            messagebox.showinfo("Каталог", "Выберите драйвер в списке.")
            return None
        return self.catalog_results[sel[0]]

    def catalog_details(self):
        r = self._selected_catalog()
        if r:
            self._dialog("Драйвер из каталога", f"{r['title']}\n\nВерсия: {r['version']}\nДата: {r['date']}\n"
                         f"Класс: {r['class']}\nРазмер: {r['sizeMB']} МБ\n\nПрименим к:\n{r['products']}")

    def catalog_install(self):
        r = self._selected_catalog()
        if not r or not self._need_admin():
            return
        if not messagebox.askyesno("Установка", f"Скачать ({r['sizeMB']} МБ) и установить?\n\n{r['title']}\n\n"
                                   "Проверьте, что дата и версия новее установленной."):
            return
        self._snapshot()
        log = self.logger(self.cat_log)

        def work():
            if self.restore_pt.get():
                log("Создаю точку восстановления…")
                log(backend.create_restore_point())
            return backend.install_from_catalog(r["guid"], r["title"], log, self.set_progress)

        self.run_bg(work, self._catalog_installed, "Устанавливаю драйвер из каталога…")

    def _catalog_installed(self, res):
        self._append(self.cat_log, f"Установлено: {res['title']}")
        if res["reboot"]:
            messagebox.showinfo("Установка", "Для завершения нужна перезагрузка.")
        self.scan()

    # ---------- maintenance ----------
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
        self._append(self.mnt_log, f"Старых версий найдено: {len(items)}")

    def delete_stale(self):
        sel = list(self.stale_tv.selection())
        if not sel or not self._need_admin():
            return
        if not messagebox.askyesno("Удаление", f"Удалить старых пакетов: {len(sel)}?\n"
                                   "Пакеты, которые сейчас используются, будут пропущены."):
            return
        log = self.logger(self.mnt_log)
        self.run_bg(lambda: backend.delete_drivers(sel, log),
                    lambda r: (self._append(self.mnt_log, f"Удалено: {r[0]}, пропущено: {r[1]}"), self.find_stale()),
                    "Удаляю старые пакеты…")

    def show_changes(self):
        snap = backend.latest_snapshot()
        if not snap:
            self._append(self.mnt_log, "Снимков пока нет — они создаются перед каждой установкой.")
            return
        lines = backend.diff_snapshot(snap, self.devices["drivers"])
        self._append(self.mnt_log, f"Снимок от {snap.stem}: изменено драйверов — {len(lines)}")
        for ln in lines:
            self._append(self.mnt_log, "  " + ln)

    # ---------- backup ----------
    def backup(self):
        if not self._need_admin():
            return
        folder = filedialog.askdirectory(title="Куда сохранить копию драйверов")
        if not folder:
            return
        dest = Path(folder) / f"drivers-{date.today().isoformat()}"
        log = self.logger(self.bk_log)
        self.run_bg(lambda: backend.backup_drivers(dest, log),
                    lambda n: self._append(self.bk_log, f"Готово: {n} драйверов в {dest}"),
                    "Копирую драйверы…")

    def restore(self):
        if not self._need_admin():
            return
        folder = filedialog.askdirectory(title="Папка с копией драйверов")
        if not folder or not messagebox.askyesno("Восстановление", "Установить все драйверы из этой папки?"):
            return
        log = self.logger(self.bk_log)
        self.run_bg(lambda: backend.restore_drivers(Path(folder), log),
                    lambda _: self._append(self.bk_log, "Восстановление завершено."),
                    "Восстанавливаю драйверы…")


if __name__ == "__main__":
    App().mainloop()
