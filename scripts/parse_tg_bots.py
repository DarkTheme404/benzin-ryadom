"""
Парсер цен из сообщений ботов-конкурентов.

⚠️ ВАЖНО: Активный подход (отправка запросов ботам) НЕ работает через Bot API.
Боты не могут читать ответы других ботов.

✅ ПРАВИЛЬНЫЙ ПОДХОД: Перехват в handlers.py
Наш бот автоматически перехватывает:
  1. Сообщения от других ботов в группах (handle_bot_message)
  2. Пересланные сообщения от ботов (handle_forwarded_bot_message)

Эти хандлеры уже注册ированы в bot/handlers.py и работают в polling-режиме.

Для ручного тестирования цен из конкурентных ботов используй:
  python scripts/price_parser.py "АИ-95 54.50 Лукойл Москва"

Для парсинга VK-групп (без API ключа):
  python scripts/parse_vk_groups.py --all

Источники данных (без Telethon):
  - bot/handlers.py: handle_bot_message, handle_forwarded_bot_message
  - scripts/parse_vk_groups.py: 557 VK-групп
  - scripts/parse_benzin_status_tech.py: Mini App API
  - scripts/parse_benzin_price.py: агрегатор цен
  - scripts/parse_networks.py: официальные сайты сетей
"""
import sys
from pathlib import Path

# Добавляем scripts/ в путь для price_parser
sys.path.insert(0, str(Path(__file__).parent))

from price_parser import parse_prices, detect_network, detect_city, detect_availability  # noqa: E402


def main():
    """Тестовый запуск: парсинг текста из stdin или аргументов."""
    import argparse
    parser = argparse.ArgumentParser(description="Тест парсера цен из текста")
    parser.add_argument("text", nargs="*", help="Текст для парсинга")
    args = parser.parse_args()

    if args.text:
        text = " ".join(args.text)
    else:
        print("Введите текст (Ctrl+D для завершения):")
        text = sys.stdin.read()

    prices = parse_prices(text)
    network = detect_network(text)
    city = detect_city(text)
    available = detect_availability(text)

    print(f"Текст: {text[:100]}")
    print(f"Цены: {prices}")
    print(f"Сеть: {network}")
    print(f"Город: {city}")
    print(f"Наличие: {available}")


if __name__ == "__main__":
    main()
