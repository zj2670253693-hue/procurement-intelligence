#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo "请使用 admin 执行: sudo bash $0" >&2
    exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
APP_USER=${APP_USER:-zhoujin}
APP_GROUP=${APP_GROUP:-projectteam}
APP_ROOT=/srv/procurement-app
ENV_DIR=/etc/procurement-app
ENV_FILE=${ENV_DIR}/procurement.env

if ! id "${APP_USER}" >/dev/null 2>&1; then
    echo "应用用户不存在: ${APP_USER}" >&2
    exit 1
fi

if ! getent group "${APP_GROUP}" >/dev/null 2>&1; then
    groupadd "${APP_GROUP}"
fi
usermod -aG "${APP_GROUP}" "${APP_USER}"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv python3-pip nginx mysql-server unzip curl openssl

if ! swapon --show=NAME --noheadings | grep -Fxq /swapfile; then
    if [ ! -f /swapfile ]; then
        fallocate -l 2G /swapfile
        chmod 600 /swapfile
        mkswap /swapfile >/dev/null
    fi
    swapon /swapfile
fi
if ! grep -Eq '^/swapfile[[:space:]]' /etc/fstab; then
    printf '%s\n' '/swapfile none swap sw 0 0' >> /etc/fstab
fi

install -d -m 2775 -o "${APP_USER}" -g "${APP_GROUP}" "${APP_ROOT}"
install -d -m 2775 -o "${APP_USER}" -g "${APP_GROUP}" \
    "${APP_ROOT}/releases" "${APP_ROOT}/shared" "${APP_ROOT}/shared/data"
install -d -m 0750 -o root -g "${APP_GROUP}" "${ENV_DIR}"

install -m 0644 "${SCRIPT_DIR}/procurement-api.service" \
    /etc/systemd/system/procurement-api.service
install -m 0644 "${SCRIPT_DIR}/nginx-procurement-app.conf" \
    /etc/nginx/sites-available/procurement-app
ln -sfn /etc/nginx/sites-available/procurement-app \
    /etc/nginx/sites-enabled/procurement-app
rm -f /etc/nginx/sites-enabled/default
install -m 0644 "${SCRIPT_DIR}/mysql-low-memory.cnf" \
    /etc/mysql/mysql.conf.d/99-procurement-low-memory.cnf

if [ ! -f "${ENV_FILE}" ]; then
    db_password=$(openssl rand -hex 24)
    sed "s/^DB_PASSWORD=.*/DB_PASSWORD=${db_password}/" \
        "${SCRIPT_DIR}/procurement.env.example" > "${ENV_FILE}"
    chown root:"${APP_GROUP}" "${ENV_FILE}"
    chmod 0640 "${ENV_FILE}"
fi

db_password=$(sed -n 's/^DB_PASSWORD=//p' "${ENV_FILE}")
if [ -z "${db_password}" ] || [ "${db_password}" = CHANGE_ME ]; then
    echo "生产数据库密码尚未生成" >&2
    exit 1
fi

systemctl enable --now mysql
systemctl restart mysql

mysql --protocol=socket --user=root <<SQL
CREATE DATABASE IF NOT EXISTS procurement_kg
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'procurement_app'@'127.0.0.1' IDENTIFIED BY '${db_password}';
ALTER USER 'procurement_app'@'127.0.0.1' IDENTIFIED BY '${db_password}';
GRANT ALL PRIVILEGES ON procurement_kg.* TO 'procurement_app'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL

systemctl daemon-reload
nginx -t
systemctl enable --now nginx

echo "系统初始化完成。应用目录: ${APP_ROOT}"
echo "未启动 procurement-api；请先安装发布版本，再运行 finalize-services.sh。"
