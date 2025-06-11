from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from functools import partial
import os

# Путь к директории с файлами
data_dir = "C:/Users/bkana/Downloads/diplom/rag_knowledge_base"

# Загрузка .txt файлов с автоопределением кодировки
loader = DirectoryLoader(
    data_dir,
    glob="*.txt",
    loader_cls=partial(TextLoader, encoding="utf-8", autodetect_encoding=True)
)
docs = loader.load()

# Нарезка на чанки
splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=100)
chunks = splitter.split_documents(docs)

# Эмбеддинги
embeddings = HuggingFaceEmbeddings(model_name="intfloat/multilingual-e5-small")

# Создание и сохранение векторной БД
db = Chroma.from_documents(
    chunks,
    embedding=embeddings,
    persist_directory=os.path.join(data_dir, "chroma_db")
)
db.persist()

print("✅ Векторная база успешно создана и сохранена.")
