"""DriverTool — a clean driver updater. No ads, no bundled software, no telemetry.
Updates come only from Windows Update (the official signed-driver channel)."""
import queue
import threading
import tkinter as tk
import webbrowser
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import quote

import backend

OLD_YEARS = 3


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DriverTool")
        self.geometry("1000x640")
        self.minsize(820, 500)
        self.q: queue.Queue = queue.Queue()
        self.devices: dict = {"drivers": [], "problems": []}
        self.updates: list[dict] = []
        self.busy = False

        self._build_banner()
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(4, 0))
        self._build_devices(nb)
        self._build_updates(nb)
        self._build_backup(nb)
        self.status = tk.StringVar(value="Готово")
        ttk.Label(self, textvariable=self.status, anchor="w", relief="sunken").pack(fill="x", side="bottom")
        self.after(100, self._pump)
        self.after(300, self.scan)

    # ---------- layout ----------
    def _build_banner(self):
        if backend.is_admin():
            return
        bar = tk.Frame(self, bg="#fff3cd")
        bar.pack(fill="x")
        tk.Label(bar, bg="#fff3cd", anchor="w",
                 text="Нет прав администратора: сканирование работает, а установка и резервное копирование — нет.",
                 ).pack(side="left", padx=8, pady=4)
        ttk.Button(bar, text="Перезапустить от администратора", command=self._elevate).pack(side="right", padx=8, pady=3)

    def _tree(self, parent, cols, widths, selectmode="extended"):
        frame = ttk.Frame(parent)
        tv = ttk.Treeview(frame, columns=[c[0] for c in cols], show="headings", selectmode=selectmode)
        for (cid, title), w in zip(cols, widths):
            tv.heading(cid, text=title)
            tv.column(cid, width=w, anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return frame, tv

    def _log_box(self, parent, height=7):
        t = tk.Text(parent, height=height, state="disabled", wrap="word", bg="#f7f7f7")
        t.pack(fill="x", pady=(6, 0))
        return t

    def _build_devices(self, nb):
        tab = ttk.Frame(nb, padding=8)
        nb.add(tab, text="Устройства")
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 6))
        ttk.Button(top, text="Пересканировать", command=self.scan).pack(side="left")
        ttk.Button(top, text="Найти в каталоге Microsoft", command=self.open_catalog).pack(side="left", padx=6)
        self.only_issues = tk.BooleanVar(value=False)
        self.hide_ms = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Только проблемные", variable=self.only_issues, command=self.render_devices).pack(side="right")
        ttk.Checkbutton(top, text="Скрыть встроенные драйверы Windows", variable=self.hide_ms, command=self.render_devices).pack(side="right", padx=10)
        frame, self.dev_tv = self._tree(tab, [
            ("name", "Устройство"), ("class", "Класс"), ("provider", "Поставщик"),
            ("version", "Версия"), ("date", "Дата"), ("status", "Состояние")],
            [300, 100, 160, 110, 90, 220])
        frame.pack(fill="both", expand=True)
        self.dev_tv.tag_configure("problem", background="#ffd6d6")
        self.dev_tv.tag_configure("old", background="#fff3cd")
        self.dev_summary = tk.StringVar()
        ttk.Label(tab, textvariable=self.dev_summary).pack(anchor="w", pady=(4, 0))

    def _build_updates(self, nb):
        tab = ttk.Frame(nb, padding=8)
        nb.add(tab, text="Обновления")
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 6))
        ttk.Button(top, text="Искать обновления", command=self.search).pack(side="left")
        ttk.Button(top, text="Выбрать все", command=lambda: self.upd_tv.selection_set(self.upd_tv.get_children())).pack(side="left", padx=6)
        self.install_btn = ttk.Button(top, text="Установить выбранные", command=self.install)
        self.install_btn.pack(side="left")
        self.restore_pt = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Создать точку восстановления", variable=self.restore_pt).pack(side="right")
        frame, self.upd_tv = self._tree(tab, [
            ("title", "Драйвер"), ("class", "Класс"), ("maker", "Производитель"),
            ("date", "Дата"), ("size", "МБ")], [420, 100, 180, 90, 60])
        frame.pack(fill="both", expand=True)
        self.upd_log = self._log_box(tab)

    def _build_backup(self, nb):
        tab = ttk.Frame(nb, padding=12)
        nb.add(tab, text="Резервная копия")
        ttk.Label(tab, wraplength=700, justify="left", text=(
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
        self.destroy()

    def _pump(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def ui(self, fn):
        self.q.put(fn)

    def run_bg(self, work, done=None, msg="Работаю…"):
        if self.busy:
            return
        self.busy = True
        self.status.set(msg)
        self.config(cursor="watch")

        def target():
            err = res = None
            try:
                res = work()
            except Exception as e:  # noqa: BLE001 — surface any backend failure to the user
                err = e
            def finish():
                self.busy = False
                self.config(cursor="")
                self.status.set("Готово" if not err else f"Ошибка: {err}")
                if err:
                    messagebox.showerror("Ошибка", str(err))
                elif done:
                    done(res)
            self.ui(finish)
        threading.Thread(target=target, daemon=True).start()

    def _append(self, box, text):
        box.configure(state="normal")
        box.insert("end", text + "\n")
        box.see("end")
        box.configure(state="disabled")

    def _need_admin(self) -> bool:
        if backend.is_admin():
            return True
        messagebox.showwarning("Нужны права администратора",
                               "Для этой операции перезапустите программу от администратора.")
        return False

    # ---------- devices ----------
    def scan(self):
        self.run_bg(backend.scan_devices, self._scan_done, "Сканирую устройства…")

    def _scan_done(self, data):
        self.devices = data
        self.render_devices()

    def render_devices(self):
        tv = self.dev_tv
        tv.delete(*tv.get_children())
        self._row_hwid = {}
        n_problem = n_old = 0
        cutoff = date.today().replace(year=date.today().year - OLD_YEARS).isoformat()

        for p in self.devices["problems"]:
            n_problem += 1
            iid = tv.insert("", "end", values=(p["name"], "", "", "", "", f"⚠ {p['reason']}"), tags=("problem",))
            self._row_hwid[iid] = p.get("hwid") or [p["deviceId"]]

        rows = sorted(self.devices["drivers"], key=lambda d: (d["class"] or "", d["name"]))
        for d in rows:
            is_ms = (d["provider"] or "").lower().startswith("microsoft")
            old = bool(d["date"]) and d["date"] < cutoff
            if self.hide_ms.get() and is_ms:
                continue
            if old and not is_ms:
                n_old += 1
            if self.only_issues.get() and not old:
                continue
            status = f"Старше {OLD_YEARS} лет" if old else "OK"
            iid = tv.insert("", "end", tags=("old",) if old else (), values=(
                d["name"], d["class"], d["provider"], d["version"], d["date"], status))
            self._row_hwid[iid] = d["hwid"] or [d["deviceId"]]
        self.dev_summary.set(f"Проблемных устройств: {n_problem} · Драйверов старше {OLD_YEARS} лет (не Microsoft): {n_old}")

    def open_catalog(self):
        sel = self.dev_tv.selection()
        if not sel:
            messagebox.showinfo("Каталог Microsoft", "Выберите устройство в списке.")
            return
        hwids = self._row_hwid.get(sel[0]) or []
        if not hwids:
            return
        webbrowser.open("https://www.catalog.update.microsoft.com/Search.aspx?q=" + quote(hwids[0]))

    # ---------- updates ----------
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

    def install(self):
        ids = list(self.upd_tv.selection())
        if not ids:
            messagebox.showinfo("Установка", "Выберите драйверы в списке.")
            return
        if not self._need_admin():
            return
        if not messagebox.askyesno("Установка", f"Установить драйверов: {len(ids)}?"):
            return
        log = lambda m: self.ui(lambda: self._append(self.upd_log, m))
        self.run_bg(lambda: backend.install_updates(ids, self.restore_pt.get(), log),
                    self._install_done, "Устанавливаю драйверы…")

    def _install_done(self, r):
        self._append(self.upd_log, f"Установлено: {r['installed']}, ошибок: {r['failed']}")
        if r["reboot"]:
            messagebox.showinfo("Установка", "Для завершения нужна перезагрузка.")
        self.scan()

    # ---------- backup ----------
    def backup(self):
        if not self._need_admin():
            return
        folder = filedialog.askdirectory(title="Куда сохранить копию драйверов")
        if not folder:
            return
        dest = Path(folder) / f"drivers-{date.today().isoformat()}"
        log = lambda m: self.ui(lambda: self._append(self.bk_log, m))
        self.run_bg(lambda: backend.backup_drivers(dest, log),
                    lambda n: self._append(self.bk_log, f"Готово: {n} драйверов в {dest}"),
                    "Копирую драйверы…")

    def restore(self):
        if not self._need_admin():
            return
        folder = filedialog.askdirectory(title="Папка с копией драйверов")
        if not folder:
            return
        if not messagebox.askyesno("Восстановление", "Установить все драйверы из этой папки?"):
            return
        log = lambda m: self.ui(lambda: self._append(self.bk_log, m))
        self.run_bg(lambda: backend.restore_drivers(Path(folder), log),
                    lambda _: self._append(self.bk_log, "Восстановление завершено."),
                    "Восстанавливаю драйверы…")


if __name__ == "__main__":
    App().mainloop()
