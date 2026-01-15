# Тесты для Solana Arbitrage Bot

Этот каталог содержит тесты для всех модулей бота арбитража на Solana.

## Структура тестов

- `test_risk_manager.py` - Тесты для модуля управления рисками
- `test_arbitrage_finder.py` - Тесты для поиска арбитражных возможностей
- `test_jupiter_client.py` - Тесты для клиента Jupiter API (с моками)
- `test_solana_client.py` - Тесты для клиента Solana RPC (с моками)
- `test_trader.py` - Тесты для основного модуля торговли (с моками)
- `test_utils.py` - Тесты для утилит
- `conftest.py` - Общие фикстуры для pytest

## Установка зависимостей

```bash
pip install -r requirements.txt
```

## Запуск тестов

Запустить все тесты:
```bash
pytest tests/
```

Запустить конкретный файл тестов:
```bash
pytest tests/test_risk_manager.py
```

Запустить конкретный тест:
```bash
pytest tests/test_risk_manager.py::TestRiskManager::test_can_open_position_success
```

Запустить с подробным выводом:
```bash
pytest tests/ -v
```

Запустить с покрытием кода:
```bash
pytest tests/ --cov=src --cov-report=html
```

## Особенности тестов

- Все тесты используют моки для внешних зависимостей (Jupiter API, Solana RPC)
- Тесты не требуют реального подключения к сети
- Асинхронные тесты используют `pytest-asyncio`
- Тесты покрывают основные сценарии работы каждого модуля

## Покрытие тестами

Тесты покрывают:
- ✅ Инициализацию всех классов
- ✅ Основные методы и функции
- ✅ Обработку ошибок
- ✅ Граничные случаи
- ✅ Валидацию входных данных
- ✅ Различные режимы работы (scan, simulate, live)
