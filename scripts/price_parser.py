"""
Парсер цен на топливо из произвольного текста.

Используется для:
- Сообщений ботов-конкурентов в Telegram (handle_bot_message)
- Постов в VK (parse_vk.py)
- Каналов в Telegram через Telethon (parse_tg_channels.py)
"""
import re
from typing import Optional


# === Паттерны цен (₽ за литр) ===
# Разделители: пробелы, дефис, двоеточие, em-dash (—), en-dash (–), ~, |
_PRICE_SEP = r"[\s\-:—–~|]+"

PRICE_PATTERNS = {
    "92": rf"(?:аи-?92|92){_PRICE_SEP}(?:от\s+)?(\d{{2,3}}[.,]\d{{2}})",
    "95": rf"(?:аи-?95|95){_PRICE_SEP}(?:от\s+)?(\d{{2,3}}[.,]\d{{2}})",
    "98": rf"(?:аи-?98|98){_PRICE_SEP}(?:от\s+)?(\d{{2,3}}[.,]\d{{2}})",
    "100": rf"(?:аи-?100|100){_PRICE_SEP}(?:от\s+)?(\d{{2,3}}[.,]\d{{2}})",
    "diesel": rf"(?:дизель|диз\.?|дт){_PRICE_SEP}(?:от\s+)?(\d{{2,3}}[.,]\d{{2}})",
    "lpg": rf"(?:газ|пропан){_PRICE_SEP}(?:от\s+)?(\d{{2,3}}[.,]\d{{2}})",
}

# Ключевые слова сетей
NETWORK_KEYWORDS = {
    "Лукойл": ["lukoil", "лукойл", "лукой"],
    "Газпромнефть": ["газпромнефть", "gazpromneft", "газпром"],
    "Роснефть": ["роснефть", "rosneft"],
    "Татнефть": ["татнефть", "tatneft"],
    "Башнефть": ["башнефть", "bashneft"],
    "Shell": ["shell", "шелл"],
    "Teboil": ["teboil", "тебойл"],
    "Нефтьмагистраль": ["нефтьмагистраль"],
}

# Крупные города РФ (и областные центры)
CITY_KEYWORDS = [
    "Москва", "Санкт-Петербург", "СПб", "Новосибирск", "Екатеринбург",
    "Казань", "Нижний Новгород", "Челябинск", "Самара", "Омск",
    "Ростов-на-Дону", "Уфа", "Красноярск", "Воронеж", "Пермь", "Волгоград",
    "Краснодар", "Саратов", "Тюмень", "Тольятти", "Ижевск", "Барнаул",
    "Иркутск", "Ульяновск", "Хабаровск", "Владивосток", "Ярославль",
    "Махачкала", "Томск", "Оренбург", "Кемерово", "Новокузнецк", "Рязань",
    "Астрахань", "Набережные Челны", "Киров", "Пенза", "Севастополь",
    "Калининград", "Тверь", "Тула", "Иваново", "Брянск", "Курск",
    "Магнитогорск", "Сочи", "Кострома", "Владимир", "Калуга", "Смоленск",
    "Орёл", "Орел", "Череповец", "Вологда", "Мурманск", "Архангельск",
    "Великий Новгород", "Псков", "Петрозаводск", "Сыктывкар",
    "Йошкар-Ола", "Саранск", "Чебоксары", "Киров", "Курган",
    "Тамбов", "Липецк", "Белгород", "Орёл", "Калуга", "Смоленск",
]


def parse_prices(text: str) -> dict[str, float]:
    """Извлекает цены на топливо из текста. Возвращает {fuel: price}."""
    prices = {}
    for fuel, pattern in PRICE_PATTERNS.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                prices[fuel] = float(m.group(1).replace(",", "."))
            except (ValueError, IndexError):
                pass
    return prices


def detect_network(text: str) -> Optional[str]:
    """Определяет сеть АЗС из текста."""
    text_lower = text.lower()
    for network, kws in NETWORK_KEYWORDS.items():
        for kw in kws:
            if kw in text_lower:
                return network
    return None


def detect_city(text: str) -> Optional[str]:
    """Определяет город из текста (берёт первое совпадение из списка)."""
    text_lower = text.lower()
    for city in CITY_KEYWORDS:
        if city.lower() in text_lower:
            return city
    return None


def detect_queue(text: str) -> Optional[int]:
    """Определяет размер очереди (примерно)."""
    m = re.search(r"очередь\s*(?:~\s*)?(\d+)\s*(?:машин|авто|чел)?", text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    if re.search(r"большая очередь|огромная очередь|длинная очередь", text, re.IGNORECASE):
        return 10
    if re.search(r"маленькая очередь|нет очереди|без очереди|очереди нет", text, re.IGNORECASE):
        return 0
    return None


def detect_availability(text: str) -> Optional[bool]:
    """Определяет наличие топлива. None = "кончается"."""
    text_lower = text.lower()
    if re.search(r"топливо\s+есть|бензин\s+есть|заправился|есть\s+аи|есть\s+бензин", text_lower):
        return True
    if re.search(r"топлива\s+нет|бензина\s+нет|нет\s+бензина|нет\s+аи|закончился|нет\s+в\s+наличии", text_lower):
        return False
    if re.search(r"заканчивается|осталось\s+мало|мало\s+бензина", text_lower):
        return None
    return None
