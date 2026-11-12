# Развёртывание на сервере (Ubuntu 22.04 / 24.04)

Нужно: VPS (2 vCPU / 2 ГБ хватит с запасом), домен, указывающий на IP сервера (A-запись), токен бота и ваш Telegram ID.

## 1. Подготовка сервера
    apt update && apt upgrade -y
    apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx ufw git
    ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw enable
    adduser --system --group --home /opt/petrofoot petrofoot

Python нужен версии 3.11 или новее (`python3 --version`).

## 2. Код
    git clone https://github.com/musatanakulov/petrofootball.github.io.git /opt/petrofoot
    cd /opt/petrofoot
    python3 -m venv venv
    venv/bin/pip install -r requirements.txt

## 3. Настройка (.env)
    cp .env.example .env
    nano .env
Обязательно:
- `BOT_TOKEN` — токен от @BotFather
- `ADMIN_TELEGRAM_IDS` — ваш Telegram ID
- `ALLOW_DEV_ADMIN=0` — на сервере всегда 0
- `WEBAPP_URL=https://petrofootball.kz/` — ваш домен, обязательно https
- `CORS_ORIGINS=https://petrofootball.kz` — ваш домен
- `BACKUP_COPY_DIR` — второе место для копий базы (отдельный диск или смонтированное хранилище)

    chown -R petrofoot:petrofoot /opt/petrofoot
    chmod 600 /opt/petrofoot/.env

## 4. Данные (не лежат в git)
Скопируйте с вашего компьютера (PowerShell, из папки проекта):
    scp football_tournament.db root@СЕРВЕР:/opt/petrofoot/
    scp -r static/players root@СЕРВЕР:/opt/petrofoot/static/    # если уже есть фото игроков
    ssh root@СЕРВЕР "chown -R petrofoot:petrofoot /opt/petrofoot"
Можно начать и с пустой базы: сервер создаст её и три лиги, команды и составы потом загружаются через админку.

## 5. Сервисы
    cp deploy/systemd/*.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now petrofoot
    systemctl status petrofoot
Бота запускайте после проверки сайта и только когда остановлен бот на вашем компьютере:
    systemctl enable --now petrofoot-bot

## 6. Nginx и HTTPS
    cp deploy/nginx/petrofootball.conf /etc/nginx/sites-available/petrofootball
    nano /etc/nginx/sites-available/petrofootball       # замените petrofootball.kz на ваш домен
    ln -s /etc/nginx/sites-available/petrofootball /etc/nginx/sites-enabled/
    rm -f /etc/nginx/sites-enabled/default
    nginx -t && systemctl reload nginx
    certbot --nginx -d petrofootball.kz -d www.petrofootball.kz
Certbot сам продлевает сертификат (проверка: `certbot renew --dry-run`).

## 7. BotFather
В @BotFather: `/mybots` → ваш бот → Bot Settings → Menu Button, адрес `https://petrofootball.kz/`. Кнопку «Турнир» бот ставит и сам при запуске.

## 8. Проверка после запуска
- `https://petrofootball.kz/` открывается, замок в адресной строке есть.
- `https://petrofootball.kz/docs` — 404, `https://petrofootball.kz/.env` — 404.
- В Telegram приложение открывается, у вас есть админка, у других пользователей нет.
- `journalctl -u petrofoot -f` без ошибок; ошибки приложения пишутся в `/opt/petrofoot/logs/app.log`.

## Обновление кода
    cd /opt/petrofoot && git pull && venv/bin/pip install -r requirements.txt
    systemctl restart petrofoot petrofoot-bot

## Резервные копии
- Приложение раз в сутки копирует базу в `backups/daily` (хранится 30 копий) и в `BACKUP_COPY_DIR`.
- Раз в неделю скачивайте копию себе: `scp root@СЕРВЕР:/opt/petrofoot/backups/daily/* .`
- Восстановление: `systemctl stop petrofoot`, подставьте копию как `football_tournament.db`, `systemctl start petrofoot`.
- Фото игроков и логотипы (`static/`) в копии базы не входят, копируйте эту папку отдельно.
