# Telegram Dice CAPTCHA

Бот проверяет **только новых участников** явно настроенных Telegram-супергрупп. Уже состоящих в группе пользователей бот не сканирует и не ограничивает.

## Возможности

- После вступления нового пользователя бот временно запрещает ему отправлять сообщения и запускает капчу с кубиком 🎲.
- Вопрос: **«Какое число на верхней грани кубика?»** Пользователь выбирает число от 1 до 6.
- На первую капчу отводится 120 секунд. Если ответа нет, бот выдаёт вторую капчу сразу.
- Если первая попытка неверная, кнопки отключаются, затем через 30 секунд появляется новая капча.
- При неверном ответе во второй попытке или истечении времени второй капчи бот исключает пользователя из группы и банит его на 30 минут (`FAIL_BAN_SECONDS`; `0` — просто исключить без бана). Если пользователь вышел из группы во время проверки и вернулся, использованные попытки сохраняются на время бана, поэтому перезаход не даёт новых попыток.
- Кубик, сообщения капчи, уведомления и сообщения меню бота ставятся в очередь удаления через 5 минут. Если Telegram временно не удаляет сообщение, бот повторяет попытку через 60 секунд; после 10 неудачных попыток запись удаляется из очереди и в журнал записывается ошибка для проверки прав.
- При старте бот проверяет права администратора во всех разрешённых группах и пишет в журнал, если не хватает ограничения участников или удаления сообщений. Если Telegram временно недоступен, сервис не завершается: обработка нового вступления будет повторяться при восстановлении API.
- Бот проверяет, что кнопку нажал именно тот пользователь, которому выдана капча, и отклоняет устаревшие и повторные ответы.
- Администраторы могут менять текст приветствия через меню `/captcha_settings`.
- Супергруппа автоматически добавляется в список разрешённых при добавлении бота; при удалении бота она исключается. Для работы капчи назначьте бота администратором с правами ограничения участников и удаления сообщений.

## Хранение данных

**SQLite и постоянный список пользователей не используются.** Состояния текущих капч, очереди удаления и счётчик частоты вступлений находятся только в оперативной памяти. После завершения проверки состояние участника удаляется из памяти. Для вышедших во время проверки в памяти временно (на время `FAIL_BAN_SECONDS`) хранится только номер достигнутой попытки.

Текст приветствия — единственная сохраняемая настройка: бот хранит его в небольшом JSON-файле `settings.json`, с отдельным текстом для каждой группы. Идентификаторы пользователей, попытки и состояния капчи в этот файл не записываются.

Из-за отсутствия базы данных текущие капчи и очередь удаления теряются при перезапуске бота. Ограничение на время капчи ставится со сроком — время ответа + пауза перед повтором + запас `CAPTCHA_LOCK_MARGIN_SECONDS` (по умолчанию 120 + 30 + 300 секунд) — и продлевается перед каждой попыткой. Поэтому после перезапуска или сбоя Telegram сам снимет ограничение с недопроверенных новичков, ручное вмешательство не нужно. Обратная сторона: такие новички остаются в группе без капчи. Это осознанный компромисс режима без постоянного состояния.

## Настройки администратора

В нужной группе отправьте:

```text
/captcha_settings
```

Команда доступна только администраторам группы и только в супергруппе, куда добавлен бот. Откроется меню:

- **Изменить приветствие** — бот попросит следующим сообщением отправить новый текст, до 1000 символов. Текст будет добавляться перед вопросом каждой капчи.
- **Сбросить приветствие** — вернуть текст по умолчанию «Добро пожаловать в группу!».
- **Показать текущее** — вывести текущий текст в меню.

Во время редактирования отправьте `/cancel_greeting`, чтобы отменить изменение. Введённый администратором текст приветствия бот попытается удалить из группы после сохранения, чтобы не засорять чат. Для этого боту нужно право **Удаление сообщений**.

Файл параметров по умолчанию:

```text
/var/lib/telegram-dice-captcha/settings.json
```

Другой путь задаётся переменной `SETTINGS_FILE`. Содержимое файла — только настройки приветствия; пользовательские данные туда не попадают.

## Требования

- Debian 12/13 или совместимая Linux-система.
- Python 3.11 или новее.
- Бот создан через [@BotFather](https://t.me/BotFather).
- Бот добавлен администратором в супергруппу и имеет права **Ограничение участников** и **Удаление сообщений**.
- В Telegram для бота должны быть доступны обновления о вступлении/выходе участников и нажатиях кнопок.

## Установка в Debian

Команды ниже выполняются от `root` или через `sudo`.

### 1. Установите пакеты

```sh
apt update
apt install -y python3 python3-venv python3-pip ca-certificates
python3 --version
```

### 2. Создайте пользователя сервиса и каталоги

```sh
adduser --system --group --home /opt/telegram-dice-captcha --no-create-home telegram-captcha
install -d -o root -g root -m 0755 /opt/telegram-dice-captcha
install -d -o telegram-captcha -g telegram-captcha -m 0750 /var/lib/telegram-dice-captcha
```

Распакуйте архив и скопируйте модули проекта. Выполняйте команду из каталога с файлами проекта:

```sh
install -m 0644 main.py handlers.py utils.py cleanup.py captcha_state.py captcha_payload.py rate_limit.py constants.py settings_store.py permissions_policy.py requirements.txt /opt/telegram-dice-captcha/
```

### 3. Создайте файл окружения

Укажите настоящий токен. Список `ALLOWED_CHAT_IDS` необязателен: его можно заполнить заранее, а группы, куда добавят бота, будут сохраняться автоматически:

```sh
cat > /etc/telegram-dice-captcha.env <<'ENV_VARS'
API_TOKEN=ВСТАВЬТЕ_ТОКЕН_БОТА
ALLOWED_CHAT_IDS=
SETTINGS_FILE=/var/lib/telegram-dice-captcha/settings.json
ENV_VARS
chown root:telegram-captcha /etc/telegram-dice-captcha.env
chmod 0640 /etc/telegram-dice-captcha.env
```

Замените `ВСТАВЬТЕ_ТОКЕН_БОТА` и `-1001234567890` реальными значениями. Если групп несколько, укажите ID через запятую, например:

```env
ALLOWED_CHAT_IDS=-1001234567890,-1009876543210
```

Бот откажется запускаться, если `API_TOKEN` не настроен. До первого события о добавлении он не обрабатывает группы. Не публикуйте токен и не добавляйте файл окружения в репозиторий.

Дополнительные переменные (необязательные; значения по умолчанию показаны):

```env
CAPTCHA_TIMEOUT_SECONDS=120
BAN_TIMEOUT_SECONDS=30
FAIL_BAN_SECONDS=1800
CAPTCHA_LOCK_MARGIN_SECONDS=300
MESSAGE_DELETE_TIMEOUT_SECONDS=300
MAX_CAPTCHA_ATTEMPTS=2
MAX_ACTIVE_CAPTCHAS_PER_CHAT=50
JOIN_WINDOW_SECONDS=60
MAX_JOINS_PER_CHAT_WINDOW=30
API_RETRY_DELAY_SECONDS=30
LOG_LEVEL=INFO
```

### 4. Установите зависимости Python

```sh
python3 -m venv /opt/telegram-dice-captcha/venv
/opt/telegram-dice-captcha/venv/bin/pip install --upgrade pip
/opt/telegram-dice-captcha/venv/bin/pip install -r /opt/telegram-dice-captcha/requirements.txt
```

### 5. Установите systemd-сервис

Создайте `/etc/systemd/system/telegram-dice-captcha.service`:

```ini
[Unit]
Description=Telegram Dice Captcha Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=telegram-captcha
Group=telegram-captcha
WorkingDirectory=/opt/telegram-dice-captcha
EnvironmentFile=/etc/telegram-dice-captcha.env
Environment=ENVIRONMENT=production
ExecStart=/opt/telegram-dice-captcha/venv/bin/python /opt/telegram-dice-captcha/main.py
Restart=on-failure
RestartSec=5
StateDirectory=telegram-dice-captcha
StateDirectoryMode=0750
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictRealtime=true
RestrictNamespaces=true
MemoryDenyWriteExecute=true
UMask=0077

[Install]
WantedBy=multi-user.target
```

Затем выполните:

```sh
systemctl daemon-reload
systemctl enable --now telegram-dice-captcha.service
systemctl status telegram-dice-captcha.service --no-pager
journalctl -u telegram-dice-captcha.service -n 100 --no-pager
```

### 6. Настройте меню администратора

После запуска добавьте бота в нужную супергруппу и назначьте администратором с правами ограничения участников и удаления сообщений. ID группы сохранится автоматически. Затем отправьте `/captcha_settings`.

## Обновление с версии, использующей SQLite

Новая версия не читает и не изменяет старые базы SQLite. Остановите старый сервис, сделайте резервную копию кода и файла окружения, замените модули и перезапустите сервис:

```sh
systemctl stop telegram-dice-captcha.service
# Скопируйте новые .py-файлы из архива в /opt/telegram-dice-captcha/
/opt/telegram-dice-captcha/venv/bin/pip install -r /opt/telegram-dice-captcha/requirements.txt
systemctl start telegram-dice-captcha.service
systemctl status telegram-dice-captcha.service --no-pager
journalctl -u telegram-dice-captcha.service -n 100 --no-pager
```

Старые файлы SQLite можно удалить вручную после проверки новой версии и резервного копирования. Новый код к ним не обращается. Не удаляйте `settings.json`, если хотите сохранить настроенное приветствие.

## Важное ограничение защиты

Кнопки капчи не раскрывают правильный ответ в `callback_data`: бот проверяет выбранное число по значению выпавшего кубика. Однако сам результат публичного кубика виден участникам группы, поэтому продвинутый Telegram userbot всё ещё может прочитать значение кубика и автоматически выбрать правильную кнопку. Для гарантированной защиты от userbot потребуется другой механизм проверки, не основанный на публичном результате кубика.

## Проверки и отладка

```sh
/opt/telegram-dice-captcha/venv/bin/python -m compileall -q /opt/telegram-dice-captcha
journalctl -u telegram-dice-captcha.service -n 100 --no-pager
```

Если новичок не получает капчу, проверьте, что бот добавлен в супергруппу и имеет права администратора на ограничение участников и удаление сообщений, а также журнал сервиса. При временной ошибке `getChatMember` бот оставляет новую проверку в оперативной очереди, пытается применить ограничение напрямую по событию вступления и повторяет операцию. Если сообщение не удаляется, проверьте право **Удаление сообщений**; после 10 неудачных попыток очередь прекращает повторение и пишет ошибку в журнал. Если приветствие не сохраняется, проверьте владельца и права каталога `/var/lib/telegram-dice-captcha`. Для сохранения приветствия администратор должен ответить на сообщение-подсказку бота в течение 5 минут.
