import os
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector, PGEngine, PGVectorStore
password = os.getenv('PG_CONFIG_PASSWORD')
from dotenv import load_dotenv
from urllib.parse import quote_plus
load_dotenv()

password = quote_plus(os.getenv("PG_CONFIG_PASSWORD"))
connection = f"postgresql+psycopg://{os.getenv('PG_CONFIG_USERNAME')}:{password}@{os.getenv('PG_CONFIG_HOST')}:{os.getenv('PG_CONFIG_PORT')}/{os.getenv('PG_CONFIG_DATABASE')}?sslmode=disable"

embeddings = OpenAIEmbeddings(
    model="openai/text-embedding-3-small", 
    base_url="https://openrouter.ai/api/v1", 
    api_key=os.getenv("API_KEY"),
)

vector_store = PGVector(
    embeddings=embeddings,
    collection_name="my_vectors",  # 这将成为表名，如 langchain_pg_embedding_my_vectors
    connection=connection,
    pre_delete_collection=False  # 避免意外删除
)