from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.llms import Ollama
from langchain.chains import ConversationalRetrievalChain
from langchain.memory import ConversationBufferMemory
from langchain.prompts import PromptTemplate
import requests
import pandas as pd
import logging
import re
from difflib import get_close_matches
from geopy.distance import geodesic

# --- Логирование ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# --- Конфиги ---
MOYSKLAD_API_KEY = "437b4e11276436c76fa225094a7ff48c25e5bc77"
DGIS_API_KEY = "7c52ad61-fb25-4272-98c9-557a0038ec6c"
FREE_DELIVERY_SUM = 10000

products_df = pd.read_excel("product_id.xlsx")
all_product_names = products_df["Название"].str.lower().tolist()

# --- Самовывоз точки (сделай lat/lon если захочешь ускорить работу) ---
pickup_points = [
    {"city": "Караганда", "name": "Hani, Таттимбета 105",  "address": "Караганда, ул. Таттимбета 105"},
    {"city": "Караганда", "name": "Hani, Шахтеров 52",     "address": "Караганда, ул. Шахтеров 52"},
    {"city": "Караганда", "name": "Hani, ТЦ Глобал Сити",  "address": "Караганда, ТЦ Глобал Сити"},
    {"city": "Караганда", "name": "Hani, ТЦ Таир",         "address": "Караганда, ТЦ Таир"},
    {"city": "Караганда", "name": "Hani, Бухар Жырау 41",  "address": "Караганда, пр. Бухар Жырау 41"},
    {"city": "Караганда", "name": "Hani, Абдирова 172",    "address": "Караганда, ул. Абдирова 172"},
    {"city": "Караганда", "name": "Hani, Гоголя 68",       "address": "Караганда, ул. Гоголя 68"},
    {"city": "Караганда", "name": "Hani, Чкалова 2",       "address": "Караганда, ул. Чкалова 2"},
    {"city": "Караганда", "name": "Hani, Назарбаева 3",    "address": "Караганда, пр. Назарбаева 3"},
    {"city": "Темиртау",  "name": "Hani, пр. Мира 712",    "address": "Темиртау, пр. Мира 712"},
    {"city": "Темиртау",  "name": "Hani, Республики 86",   "address": "Темиртау, ул. Республики 86"},
    {"city": "Темиртау",  "name": "Hani, микрорайон 43а",  "address": "Темиртау, микрорайон 43а"},
    {"city": "Астана",    "name": "Hani, Туркестан 20",    "address": "Астана, ул. Туркестан 20"},
    {"city": "Астана",    "name": "Hani, Туркестан 28",    "address": "Астана, ул. Туркестан 28"},
    {"city": "Астана",    "name": "Hani, Иманбаева 7а",    "address": "Астана, ул. Иманбаева 7а"},
    {"city": "Астана",    "name": "Hani, Мангилик ел 45а", "address": "Астана, пр. Мангилик ел 45а"},
    {"city": "Астана",    "name": "Hani, Тауелсиздик 39",  "address": "Астана, пр. Тауелсиздик 39"},
]

delivery_zones = {
    "город": 500, "юго-восток": 500, "михайловка": 500, "за церковью": 1000,
    "федоровка": 500, "после пожарной части": 1000, "майкудук до тд умай": 1000,
    "майкудук после тд умай": 1500, "пришахтинск": 1500, "жби": 1500, "кункей": 1000
}
city_delivery = {
    "караганд": 500, "темиртау": 500, "астан": 700, "левый берег": 700, "правый берег": 1000
}

# --- PROMPT ---
prompt_template = PromptTemplate(
    input_variables=["context", "question", "chat_history"],
    template="""
Ты — дружелюбный и внимательный консультант кафе-кондитерской Hani.

Всегда начинай диалог с приветствия. Если пользователь впервые обращается — представься. 
Затем вежливо отвечай на его вопрос, используя только предоставленный контекст. 
Если пользователь хочет сделать заказ, узнай адрес или город, предложи ближайшую точку для самовывоза (если есть). 
После подтверждения — уточни, какой именно товар интересует, и дай подробную информацию (состав, цена, вес, наличие).
Если нет информации — честно скажи, что не знаешь.

История диалога:
{chat_history}

Контекст из базы:
{context}

Вопрос:
{question}

Ответ:
"""
)
db = Chroma(
    persist_directory="rag_knowledge_base/chroma_db",
    embedding_function=HuggingFaceEmbeddings(model_name="intfloat/multilingual-e5-small")
)
llm = Ollama(model="yandex/YandexGPT-5-Lite-8B-instruct-GGUF")
memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)
qa = ConversationalRetrievalChain.from_llm(
    llm=llm,
    retriever=db.as_retriever(),
    memory=memory,
    combine_docs_chain_kwargs={"prompt": prompt_template}
)

# --- Геокодинг, поиск доставки, ближайшей точки ---
def geocode_address_2gis(address, api_key=DGIS_API_KEY):
    url = "https://catalog.api.2gis.com/3.0/items/geocode"
    params = {"q": address, "key": api_key, "fields": "items.point"}
    resp = requests.get(url, params=params)
    data = resp.json()
    if data.get("result", {}).get("items"):
        point = data["result"]["items"][0]["point"]
        return float(point["lat"]), float(point["lon"])
    return None

def find_nearest_pickup(user_coords, pickup_points):
    best_point = None
    min_dist = float("inf")
    for point in pickup_points:
        # Авто-геокодим если нет координат
        if not point.get("lat") or not point.get("lon"):
            coords = geocode_address_2gis(point["address"])
            if coords:
                point["lat"], point["lon"] = coords
            else:
                continue
        pickup_coords = (point["lat"], point["lon"])
        dist = geodesic(user_coords, pickup_coords).kilometers
        if dist < min_dist:
            min_dist = dist
            best_point = point
    return best_point, min_dist

def get_delivery_price(user_text: str, order_sum: int = 0) -> str:
    user_text = user_text.lower()
    for zone, price in delivery_zones.items():
        if zone in user_text:
            if order_sum >= FREE_DELIVERY_SUM:
                return "Доставка бесплатная при заказе от 10 000 ₸."
            return f"Стоимость доставки по району '{zone.title()}': {price} ₸."
    for city, price in city_delivery.items():
        if city in user_text:
            if order_sum >= FREE_DELIVERY_SUM:
                return f"Доставка бесплатная по городу при заказе от 10 000 ₸."
            return f"Стоимость доставки по городу: {price} ₸."
    return "Не удалось определить район или город. Пожалуйста, уточните адрес или район доставки!"

# --- Вся твоя логика осталась прежней: товары, память, наличие ---
def detect_stock_question(query: str) -> bool:
    stock_patterns = [
        r'(есть|имеется|доступн)[а-я]*\s*(ли\s*)?(в\s*наличии|на\s*склад[а-я]*)',
        r'(где\s*взять|где\s*купить|где\s*найти|на\s*какой\s*точке|на\s*точке)',
        r'(сколько\s*осталось|какое\s*количество)',
        r'(можно\s*забрать|можно\s*купить|самовывоз|забрать)',
        r'(где\s*забрать|доставка)'
    ]
    query = re.sub(r'[^\w\s]', '', query.lower())
    return any(re.search(pattern, query) for pattern in stock_patterns)

def find_similar_products(query: str, product_list: list, n=3, cutoff=0.6) -> list:
    query = re.sub(r'[^\w\s]', '', query.lower())
    return get_close_matches(query, product_list, n=n, cutoff=cutoff)

def extract_product_name(query: str) -> str:
    stop_words = {'есть', 'ли', 'где', 'взять', 'купить', 'найти',
                'сколько', 'осталось', 'наличие', 'на', 'точке',
                'можно', 'забрать', 'какое', 'количество', 'в', 'самовывоз', 'доставка'}
    words = [word for word in re.findall(r'\w+', query.lower()) if word not in stop_words]
    return ' '.join(words)

def get_product_stock(meta_href: str, api_key: str) -> str:
    url = f"https://api.moysklad.ru/api/remap/1.2/report/stock/bystore?filter=product={meta_href}"
    headers = {"Authorization": f"Bearer {api_key}", "Accept-Encoding": "gzip", "Content-Type": "application/json"}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        rows = data.get("rows", [])
        stocks = []
        for item in rows:
            for store in item.get("stockByStore", []):
                if store.get("stock", 0) > 0:
                    store_name = store.get("name", "Неизвестный склад")
                    stocks.append(f"• {store_name}: {int(store['stock'])} шт.")
        if stocks:
            return "🔍 **Наличие товара:**\n" + "\n".join(stocks)
        return "Товара нет в наличии."
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка запроса: {str(e)}")
        return f"⚠️ Ошибка при проверке наличия: {str(e)}"

print("Консультант Hani готов к диалогу. Напишите вопрос или 'выход':")
logger.info("Бот запущен и готов к работе")

current_selection = None
last_product_query = None
user_address = None
order_sum = 0

clarifying_phrases = [
    'есть в наличии', 'есть?', 'можно забрать?', 'доступен?', 'самовывоз',
    'где забрать', 'а есть', 'есть ли в наличии', 'наличие?', 'где взять',
    'забрать', 'на точке', 'на какой точке', 'доступен для самовывоза', 'точка'
]

while True:
    q = input("Вы: ").strip()
    logger.info(f"Получен вопрос: '{q}'")

    # --- Определяем стоимость доставки, ближайшую точку по адресу или району ---
    if any(word in q.lower() for word in ["город", "адрес", "нахожусь", "я из", "район", "доставка"]):
        user_address = q
        try:
            order_sum = int(input("Введите сумму заказа (₸): "))
        except Exception:
            order_sum = 0
        delivery_msg = get_delivery_price(user_address, order_sum)
        print("Бот:", delivery_msg)
        coords = geocode_address_2gis(user_address)
        if coords:
            nearest, dist = find_nearest_pickup(coords, pickup_points)
            print(f"Бот: Ближайшая точка для самовывоза — {nearest['name']} ({nearest['address']}). До неё {dist:.1f} км.")
        else:
            print("Бот: Не удалось определить координаты вашего адреса, попробуйте написать подробнее.")
        continue

    # --- Товарный выбор и остальное ---
    if q.lower() in ["выход", "exit", "quit"]:
        logger.info("Завершение работы по команде пользователя")
        print("До свидания!")
        break

    if current_selection and q.isdigit():
        choice = int(q)
        if 1 <= choice <= len(current_selection):
            selected_product = current_selection[choice-1]
            product_row = products_df[products_df["Название"] == selected_product].iloc[0]
            meta_href = product_row["Meta Href"]
            logger.info(f"Выбран товар: Meta Href={meta_href}, название='{product_row['Название']}'")
            stock_info = get_product_stock(meta_href, MOYSKLAD_API_KEY)
            print("Бот:", stock_info)
            last_product_query = selected_product.lower()
            current_selection = None
            continue
        else:
            print("Бот: Пожалуйста, укажите номер из предложенных вариантов.")
            continue

    if current_selection:
        print("Бот: Пожалуйста, укажите номер товара или задайте новый вопрос.")
        continue

    if detect_stock_question(q):
        query_words = extract_product_name(q).split()
        if (
            (len(query_words) <= 2 and last_product_query)
            or any(phrase in q.lower() for phrase in clarifying_phrases)
        ):
            product_query = last_product_query if last_product_query else extract_product_name(q)
        else:
            product_query = extract_product_name(q)
            last_product_query = product_query

        logger.info(f"Извлечен запрос товара: '{product_query}'")
        if not product_query:
            print("Бот: Сначала уточните, какой товар вас интересует.")
            continue

        similar_products = find_similar_products(product_query, all_product_names)
        if not similar_products:
            print("Бот: Товар не найден в ассортименте.")
            continue

        if len(similar_products) > 1:
            original_names = []
            for name in similar_products:
                original_name = products_df[products_df["Название"].str.lower() == name].iloc[0]["Название"]
                original_names.append(original_name)

            print("Бот: Уточните, какой именно товар вас интересует:")
            for i, name in enumerate(original_names, 1):
                print(f"{i}. {name}")
            print("Пожалуйста, укажите номер товара.")
            current_selection = original_names
            continue

        product_name = similar_products[0]
        product_row = products_df[products_df["Название"].str.lower() == product_name].iloc[0]
        meta_href = product_row["Meta Href"]
        logger.info(f"Выбран товар: Meta Href={meta_href}, название='{product_row['Название']}'")
        stock_info = get_product_stock(meta_href, MOYSKLAD_API_KEY)
        print("Бот:", stock_info)
        last_product_query = product_name

    else:
        logger.info("Вопрос не про наличие - обращение к YandexGPT")
        result = qa.run(q)
        print("Бот:", result)
