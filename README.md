# Telegram Dice Captcha

Telegram-бот для проверки новых участников групп с помощью 🎲. Бот временно ограничивает участника, отправляет кубик и предлагает выбрать выпавшее число.

## Поведение

- Участник получает не более двух капч за одно вступление.
- Если первая капча не решена за 2 минуты, бот выдаёт вторую. При неправильном ответе бот снимает кнопки, оставляет участника ограниченным и выдаёт вторую капчу через 30 секунд.
- Если вторая капча решена неверно или не решена за 2 минуты, бот исключает участника из группы. При новом вступлении начинается новый цикл.
- Сообщения бота и кубики, добавленные в очередь очистки, удаляются через 5 минут. Очередь хранится в SQLite и переживает перезапуск процесса; при временной ошибке Telegram бот повторяет удаление позже.

## Установка на Debian 12/13

Команды ниже выполняются на сервере от `root` или через `sudo`. Для запуска нужны доступ к Telegram Bot API и исходники этого проекта.

### 1. Установить системные пакеты

```
apt update
apt install -y python3 python3-venv python3-pip ca-certificates
```

Убедитесь, что Python 3.11 или новее доступен:

```
python3 --version
```

### 2. Создать системного пользователя и каталоги

Бот будет запускаться от отдельного непривилегированного пользователя `telegram-captcha`. Код хранится в `/opt/telegram-dice-captcha`, изменяемая очередь очистки — в `/var/lib/telegram-dice-captcha`.

```
adduser --system --group --home /opt/telegram-dice-captcha --no-create-home telegram-captcha
install -d -o root -g root -m 0755 /opt/telegram-dice-captcha
install -d -o telegram-captcha -g telegram-captcha -m 0750 /var/lib/telegram-dice-captcha
```

Скопируйте файлы проекта (как минимум `main.py`, `handlers.py`, `utils.py`, `cleanup.py`, `constants.py`, `requirements.txt`) в `/opt/telegram-dice-captcha`. Например, если исходники распакованы в текущий каталог:

```
install -m 0644 main.py handlers.py utils.py cleanup.py constants.py requirements.txt /opt/telegram-dice-captcha/
```

### 3. Создать виртуальное окружение и установить Python-зависимости

```
python3 -m venv /opt/telegram-dice-captcha/venv
/opt/telegram-dice-captcha/venv/bin/pip install --upgrade pip
/opt/telegram-dice-captcha/venv/bin/pip install -r /opt/telegram-dice-captcha/requirements.txt
```

### 4. Создать Telegram-бота и настроить токен

1. Откройте [@BotFather](https://t.me/BotFather), выполните `/newbot` и сохраните выданный токен.
2. Создайте файл окружения. Подставьте настоящий токен вместо `ВСТАВЬТЕ_ТОКЕН_ОТ_BOTFATHER`; не добавляйте кавычки и не публикуйте этот файл.

```
cat > /etc/telegram-dice-captcha.env <<'EOF'
API_TOKEN=ВСТАВЬТЕ_ТОКЕН_ОТ_BOTFATHER
EOF
chown root:telegram-captcha /etc/telegram-dice-captcha.env
chmod 0640 /etc/telegram-dice-captcha.env
```

Токен — это секрет. Если он попал в публичный доступ, отзовите его через BotFather и обновите файл.

### 5. Добавить бота в группу и выдать права администратора

Добавьте бота в нужную группу и назначьте администратором. Ему необходимы права:

- ограничивать участников (Restrict members), чтобы временно запрещать отправку сообщений;
- удалять сообщения (Delete messages), чтобы очищать капчи, кубики и прочие сообщения бота.

Боту также должны приходить обновления о вступлении участников. Если обработчик новых участников не срабатывает, проверьте настройки группы, статус бота и права администратора. В группе с включённой защитой контента или ограничениями удаления Telegram может не разрешить отдельные операции.

### 6. Установить службу systemd

Создайте `/etc/systemd/system/telegram-dice-captcha.service`:

```
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
ExecStart=/opt/telegram-dice-captcha/venv/bin/python /opt/telegram-dice-captcha/main.py
Restart=on-failure
RestartSec=5
StateDirectory=telegram-dice-captcha
StateDirectoryMode=0750
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Затем включите автозапуск и запустите бота:

```
systemctl daemon-reload
systemctl enable --now telegram-dice-captcha.service
```

Проверить состояние и последние записи журнала:

```
systemctl status telegram-dice-captcha.service --no-pager
journalctl -u telegram-dice-captcha.service -n 100 --no-pager
```

Следить за журналом в реальном времени:

```
journalctl -u telegram-dice-captcha.service -f
```

## Обновление кода

Сначала сохраните копию текущего кода. Затем замените файлы в `/opt/telegram-dice-captcha`, обновите зависимости (если менялся `requirements.txt`) и перезапустите службу:

```
/opt/telegram-dice-captcha/venv/bin/pip install -r /opt/telegram-dice-captcha/requirements.txt
systemctl restart telegram-dice-captcha.service
systemctl status telegram-dice-captcha.service --no-pager
```

Не удаляйте `/var/lib/telegram-dice-captcha`: там находится постоянная очередь отложенного удаления сообщений и состояние активных капч. Файл `/etc/telegram-dice-captcha.env` при обновлении кода также нужно сохранить.

## Управление службой

```
systemctl start telegram-dice-captcha.service
systemctl stop telegram-dice-captcha.service
systemctl restart telegram-dice-captcha.service
systemctl disable --now telegram-dice-captcha.service  # выключить автозапуск и остановить
```

## Диагностика

- **Служба не запускается:** проверьте `systemctl status` и `journalctl`; убедитесь, что путь `ExecStart`, виртуальное окружение и файл `/etc/telegram-dice-captcha.env` существуют.
- **Ошибка авторизации:** проверьте `API_TOKEN` в env-файле и права `0640`; перезапустите службу после изменения.
- **Бот не реагирует на вступления:** проверьте, что в группе он администратор и может ограничивать участников; проверьте журнал и что в группе нет второго процесса с тем же токеном, который одновременно забирает обновления.
- **Не удаляются сообщения:** проверьте право Delete messages и ошибки Telegram в журнале. Часть старых сообщений могла быть удалена вручную; очередь очистки удалит такие записи при обработке.
- **После рестарта участник остался ограничен:** активные капчи, счётчик попыток и очередь отложенного удаления сообщений хранятся в SQLite (`/var/lib/telegram-dice-captcha`) и переживают перезапуск процесса — при старте бот сам продолжает прерванные капчи и довыполняет отложенные удаления. Если участник всё же остался ограничен дольше ожидаемого, проверьте журнал на ошибки Telegram API (например, бот мог временно потерять права администратора) и, при необходимости, снимите ограничения или исключите участника вручную.

## Ручной запуск для разработки

В каталоге проекта создайте локальный `.env` с `API_TOKEN=...`, затем:

```
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt
python main.py
```

Не запускайте вручную второй экземпляр одновременно со службой systemd, используя тот же токен.
