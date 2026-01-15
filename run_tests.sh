#!/bin/bash
# Скрипт для запуска тестов Solana Arbitrage Bot

echo "Запуск тестов для Solana Arbitrage Bot..."
echo ""

# Проверка установки pytest
if ! python3 -c "import pytest" 2>/dev/null; then
    echo "Ошибка: pytest не установлен"
    echo "Установите зависимости: pip install -r requirements.txt"
    exit 1
fi

# Запуск тестов
python3 -m pytest tests/ -v "$@"
