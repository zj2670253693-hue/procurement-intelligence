#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo "请使用 admin 执行: sudo bash $0" >&2
    exit 1
fi

APP_ROOT=/srv/procurement-app
CURRENT=${APP_ROOT}/current
VENV=${APP_ROOT}/shared/venv

required_paths=(
    "${CURRENT}/backend/api/main.py"
    "${CURRENT}/frontend-dist/index.html"
    "${VENV}/bin/python"
    "/etc/procurement-app/procurement.env"
)
for path in "${required_paths[@]}"; do
    if [ ! -e "${path}" ]; then
        echo "缺少部署前置文件: ${path}" >&2
        exit 1
    fi
done

nginx -t
systemctl daemon-reload
systemctl enable mysql nginx procurement-api
systemctl restart mysql
systemctl restart procurement-api
systemctl restart nginx

for _ in $(seq 1 30); do
    if curl --fail --silent --show-error http://127.0.0.1/api/health; then
        printf '\n'
        echo "部署健康检查通过。"
        exit 0
    fi
    sleep 1
done

echo "部署健康检查失败。" >&2
systemctl --no-pager --full status procurement-api >&2 || true
exit 1
