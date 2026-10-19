"""Tiny i18n: source strings are Russian; `tr()` returns the English text when LANG == 'en'."""
LANG = "ru"


def set_lang(lang: str) -> None:
    global LANG
    LANG = lang if lang in ("ru", "en") else "ru"


def tr(s: str, **kw) -> str:
    t = EN.get(s, s) if LANG == "en" else s
    return t.format(**kw) if kw else t


EN: dict[str, str] = {
    # progress / status
    "Работаю…": "Working…",
    "Сканирую устройства…": "Scanning devices…",
    "Устанавливаю утилиту через winget…": "Installing the tool via winget…",
    "Ищу обновления (может занять пару минут)…": "Searching for updates (may take a couple of minutes)…",
    "Устанавливаю драйверы…": "Installing drivers…",
    "Ищу в каталоге Microsoft…": "Searching the Microsoft Catalog…",
    "Устанавливаю драйвер из каталога…": "Installing the driver from the catalog…",
    "Проверяю все устройства в каталоге…": "Checking all devices in the catalog…",
    "Устанавливаю драйверы из каталога…": "Installing drivers from the catalog…",
    "Откатываю драйвер…": "Rolling back the driver…",
    "Анализирую хранилище драйверов…": "Analyzing the driver store…",
    "Удаляю старые пакеты…": "Removing old packages…",
    "Копирую драйверы…": "Copying drivers…",
    "Восстанавливаю драйверы…": "Restoring drivers…",
    "Готово": "Ready",
    "Отмена": "Cancel",
    "Отменено": "Cancelled",
    "Операция отменена": "Operation cancelled",
    "Ошибка": "Error",
    "Ошибка: {text}": "Error: {text}",
    "Занято": "Busy",
    "Дождитесь окончания текущей операции или нажмите «Отмена».": "Wait for the current operation to finish or press Cancel.",
    # admin
    "Нужны права администратора": "Administrator rights required",
    "Для этой операции перезапустите программу от администратора.": "Restart the program as administrator for this operation.",
    "Нет прав администратора: сканирование работает, а установка и резервное копирование — нет.":
        "No administrator rights: scanning works, installing and backup do not.",
    "Перезапустить от администратора": "Restart as administrator",
    # tabs
    "Устройства": "Devices",
    "Обновления Windows": "Windows Updates",
    "Каталог Microsoft": "Microsoft Catalog",
    "Авто-подбор": "Auto-match",
    "Обслуживание": "Maintenance",
    "Резервная копия": "Backup",
    # devices tab
    "Пересканировать": "Rescan",
    "Подробности": "Details",
    "Искать в каталоге Microsoft": "Search Microsoft Catalog",
    "Производитель и утилиты": "Vendor & tools",
    "Экспорт в CSV": "Export to CSV",
    "Поиск:": "Search:",
    "Устаревшим считать старше (лет):": "Outdated if older than (years):",
    "Язык изменится после перезапуска программы.": "The language will change after restarting the program.",
    "Тёмная тема": "Dark theme",
    "Скрыть драйверы Windows": "Hide Windows drivers",
    "Только проблемные и устаревшие": "Only problem and outdated",
    "Группировать по классам": "Group by class",
    "Проблемы": "Problems",
    "Проблемных устройств: {p} · Старше {y} лет (не Microsoft): {o} · Новее в каталоге: {a}":
        "Problem devices: {p} · Older than {y} years (non-Microsoft): {o} · Newer in catalog: {a}",
    "Доступно новее: {ver}": "Newer available: {ver}",
    "Старше {years} лет": "Older than {years} years",
    "Устройство": "Device",
    "Выберите устройство в списке.": "Select a device in the list.",
    "Подробности устройства": "Device details",
    "Устройство: {v}": "Device: {v}",
    "Класс: {v}": "Class: {v}",
    "Поставщик: {v}": "Provider: {v}",
    "Версия: {v}": "Version: {v}",
    "Дата: {v}": "Date: {v}",
    "Подписан: {v}": "Signed: {v}",
    "да": "yes",
    "нет/неизвестно": "no/unknown",
    "ID устройства: {v}": "Device ID: {v}",
    "Коды оборудования:": "Hardware IDs:",
    "Проблема: {v}": "Problem: {v}",
    "Новее в каталоге: {t} ({v}, {d})": "Newer in catalog: {t} ({v}, {d})",
    "Сохранено: {path}": "Saved: {path}",
    "Для этого оборудования ничего не известно. Используйте поиск в каталоге Microsoft.":
        "Nothing is known for this hardware. Use the Microsoft Catalog search.",
    "Открыть сайт: {name}": "Open website: {name}",
    "Установить через winget: {name}": "Install via winget: {name}",
    "Драйверы NVIDIA и AMD в winget отсутствуют — для них доступна только страница загрузки производителя.":
        "NVIDIA and AMD drivers are not in winget — only the vendor's download page is available for them.",
    "Установить {name} через winget?\nПакет: {id}": "Install {name} via winget?\nPackage: {id}",
    # columns
    "Класс": "Class", "Поставщик": "Provider", "Версия": "Version", "Дата": "Date",
    "Состояние": "Status", "Драйвер": "Driver", "Производитель": "Manufacturer", "МБ": "MB",
    "Установлено": "Installed", "Доступно": "Available", "Источник": "Source",
    "Что установлено": "What was installed", "Пакетов": "Packages", "Пакет": "Package",
    "Файл .inf": ".inf file", "Есть новее": "Newer exists",
    # updates tab
    "Искать обновления": "Search for updates",
    "Выбрать все": "Select all",
    "Установить выбранные": "Install selected",
    "Создать точку восстановления": "Create a restore point",
    "Поиск драйверов в Windows Update…": "Searching drivers in Windows Update…",
    "Найдено обновлений драйверов: {n}": "Driver updates found: {n}",
    "Установка": "Installation",
    "Выберите драйверы в списке.": "Select drivers in the list.",
    "Установить драйверов: {n}?": "Install {n} driver(s)?",
    "Windows Update: {n} драйв.": "Windows Update: {n} driver(s)",
    "Установлено: {ok}, ошибок: {bad}": "Installed: {ok}, errors: {bad}",
    "Для завершения нужна перезагрузка.": "A reboot is required to finish.",
    # catalog tab
    "Поиск драйвера по коду оборудования в официальном каталоге Microsoft Update. Выберите устройство на вкладке «Устройства» и нажмите «Искать в каталоге». Перед установкой проверяются контрольная сумма и цифровая подпись файла.":
        "Looks a driver up by hardware ID in the official Microsoft Update Catalog. Select a device on the Devices tab and press “Search Microsoft Catalog”. The checksum and digital signature are verified before installing.",
    "Устройство не выбрано": "No device selected",
    "Скачать и установить": "Download and install",
    "Ищу в каталоге: {hw}": "Searching the catalog: {hw}",
    "Найдено драйверов: {n}": "Drivers found: {n}",
    "В каталоге ничего не найдено для этого оборудования.": "Nothing found in the catalog for this hardware.",
    "Каталог": "Catalog",
    "Выберите драйвер в списке.": "Select a driver in the list.",
    "Драйвер из каталога": "Driver from the catalog",
    "{title}\n\nВерсия: {ver}\nДата: {date}\nКласс: {cls}\nРазмер: {size} МБ\n\nПрименим к:\n{prod}":
        "{title}\n\nVersion: {ver}\nDate: {date}\nClass: {cls}\nSize: {size} MB\n\nApplies to:\n{prod}",
    "Скачать ({size} МБ) и установить?\n\n{title}\n\nПроверьте, что дата и версия новее установленной.":
        "Download ({size} MB) and install?\n\n{title}\n\nMake sure the date and version are newer than what is installed.",
    "Создаю точку восстановления…": "Creating a restore point…",
    "Установлено: {name}": "Installed: {name}",
    # auto tab
    "Проверяет все устройства в каталоге Microsoft и показывает только драйверы новее установленных (версия выше и дата не старше). Понижение версии невозможно. Проверка идёт по одному запросу на устройство и может занять несколько минут.":
        "Checks every device against the Microsoft Catalog and shows only drivers newer than the installed ones (higher version and not older by date). Downgrades are impossible. One request per device; it may take a few minutes.",
    "Найти новее для всех": "Find newer for all",
    "Включая драйверы Windows": "Including Windows drivers",
    "Установлено": "Installed",
    "Проверяю устройства в каталоге Microsoft…": "Checking devices in the Microsoft Catalog…",
    "Сначала дождитесь сканирования устройств.": "Wait for the device scan to finish first.",
    "Найдено обновлений: {n}": "Updates found: {n}",
    "Скачать и установить драйверов: {n} (≈{mb} МБ)?": "Download and install {n} driver(s) (≈{mb} MB)?",
    "Есть новее: {name} → {ver}": "Newer found: {name} → {ver}",
    # maintenance
    "Журнал установок": "Install journal",
    "Обновить": "Refresh",
    "Откатить выбранное": "Roll back selected",
    "откатано": "rolled back",
    "можно откатить": "can be rolled back",
    "нет данных для отката": "no rollback data",
    "Откат": "Rollback",
    "Выберите запись в журнале.": "Select a journal entry.",
    "Удалить установленные этим действием пакеты драйверов? Windows вернётся к предыдущему драйверу.":
        "Remove the driver packages installed by this action? Windows will fall back to the previous driver.",
    "Откат выполнен, удалено пакетов: {n}": "Rollback done, packages removed: {n}",
    "Старые версии драйверов в хранилище Windows": "Old driver versions in the Windows driver store",
    "Найти старые версии": "Find old versions",
    "Удалить выбранные": "Delete selected",
    "Старых версий найдено: {n}": "Old versions found: {n}",
    "Удаление": "Deletion",
    "Удалить старых пакетов: {n}?\nПакеты, которые сейчас используются, будут пропущены.":
        "Delete {n} old package(s)?\nPackages currently in use will be skipped.",
    "Удалено: {ok}, пропущено: {bad}": "Deleted: {ok}, skipped: {bad}",
    "Что изменилось с последнего снимка": "What changed since the last snapshot",
    "Точки восстановления Windows": "Windows restore points",
    "Папка с данными": "Data folder",
    "Снимков пока нет — они создаются перед каждой установкой.": "No snapshots yet — one is created before each install.",
    "Снимок от {when}: изменено драйверов — {n}": "Snapshot {when}: drivers changed — {n}",
    # backup
    "Резервная копия сохраняет все сторонние драйверы из хранилища Windows в папку. Её можно восстановить после переустановки системы или неудачного обновления.":
        "A backup saves all third-party drivers from the Windows driver store to a folder. You can restore it after reinstalling the system or a failed update.",
    "Создать копию…": "Create backup…",
    "Восстановить из папки…": "Restore from folder…",
    "Куда сохранить копию драйверов": "Where to save the driver backup",
    "Папка с копией драйверов": "Folder with the driver backup",
    "Готово: {n} драйверов в {dest}": "Done: {n} drivers in {dest}",
    "Восстановление": "Restore",
    "Установить все драйверы из этой папки?": "Install all drivers from this folder?",
    "Восстановление завершено.": "Restore finished.",
    # backend
    "Устройство настроено неправильно": "The device is not configured correctly",
    "Устройство не запускается": "The device cannot start",
    "Нужно переустановить драйверы": "Drivers need to be reinstalled",
    "Устройство отключено": "The device is disabled",
    "Драйверы не установлены": "Drivers are not installed",
    "Устройство работает неправильно": "The device is not working properly",
    "Не удаётся загрузить драйвер": "The driver cannot be loaded",
    "Windows остановила устройство (сбой)": "Windows stopped the device (failure)",
    "Не удаётся проверить цифровую подпись": "The digital signature cannot be verified",
    "Код ошибки {code}": "Error code {code}",
    "Нет подключения к интернету или сервер Microsoft недоступен.": "No internet connection or the Microsoft server is unavailable.",
    "Служба Windows Update отключена. Включите службу «Центр обновления Windows» (wuauserv).":
        "The Windows Update service is disabled. Enable the “Windows Update” service (wuauserv).",
    "Недостаточно прав. Запустите программу от администратора.": "Not enough rights. Run the program as administrator.",
    "Служба обновления Windows остановлена или занята. Повторите попытку позже.":
        "The Windows Update service is stopped or busy. Try again later.",
    "Неизвестная ошибка": "Unknown error",
    "Точка восстановления создана.": "Restore point created.",
    "Точка восстановления не создана (Windows разрешает не чаще 1 раза в сутки).":
        "Restore point not created (Windows allows one per 24 hours).",
    "Запись журнала не найдена.": "Journal entry not found.",
    "Для этой записи нет данных об установленных пакетах — откат невозможен.":
        "No installed-package data for this entry — rollback is impossible.",
    "пустой ответ PowerShell": "empty PowerShell response",
    "winget не найден. Установите «Установщик приложений» из Microsoft Store.":
        "winget not found. Install “App Installer” from the Microsoft Store.",
    "winget завершился с кодом {code}": "winget exited with code {code}",
    "Недостаточно места на диске: нужно около {need} МБ, свободно {free} МБ.":
        "Not enough disk space: about {need} MB needed, {free} MB free.",
    "Контрольная сумма файла не совпала — файл повреждён, установка отменена.":
        "File checksum mismatch — the file is corrupted, installation cancelled.",
    "В каталоге нет .cab-файла для этого драйвера.": "The catalog has no .cab file for this driver.",
    "Скачиваю {i}/{n}: {name}": "Downloading {i}/{n}: {name}",
    "Проверяю цифровую подпись…": "Verifying the digital signature…",
    "Подпись верна: {who}": "Signature valid: {who}",
    "Цифровая подпись файла недействительна — установка отменена.": "The file's digital signature is invalid — installation cancelled.",
    "Не удалось распаковать архив драйвера.": "Could not unpack the driver archive.",
    "Устанавливаю…": "Installing…",
    "pnputil завершился с ошибкой": "pnputil finished with an error",
    "pnputil вернул код {code}": "pnputil returned code {code}",
    "Удалён пакет {pkg}": "Removed package {pkg}",
    "Не удалось удалить {pkg}": "Could not remove {pkg}",
    "Удалён {name}": "Removed {name}",
    "Пропущен {name} (используется или защищён)": "Skipped {name} (in use or protected)",
}
