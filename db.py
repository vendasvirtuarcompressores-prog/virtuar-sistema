"""
Camada única de acesso ao banco de dados.

Comportamento:
- Se existir uma DATABASE_URL configurada (nos "Secrets" do Streamlit Cloud
  ou como variável de ambiente), o app usa Postgres — persistente, não some
  a cada deploy.
- Caso contrário, usa SQLite local (útil para rodar no seu PC sem configurar
  nada). Em servidor, o SQLite é apagado a cada reinício/deploy — por isso
  o Postgres é o recomendado em produção.

Todas as consultas usam parâmetros nomeados (:algo), compatíveis tanto com
SQLite quanto com Postgres via SQLAlchemy.
"""

import os
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

try:
    import streamlit as st
except Exception:
    st = None


def _connection_string():
    # 1) Streamlit Secrets (recomendado no Streamlit Cloud)
    if st is not None:
        try:
            if "DATABASE_URL" in st.secrets:
                return st.secrets["DATABASE_URL"]
        except Exception:
            pass
    # 2) Variável de ambiente (Render, Railway, VPS, etc.)
    return os.environ.get("DATABASE_URL")


def _local_sqlite_path() -> Path:
    data_dir = Path(os.environ.get("VIRTUAR_DATA_DIR", Path.home() / "VirtuArData")).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "compras_nfe.db"


_url = _connection_string()
if _url:
    # SQLAlchemy/psycopg2 exigem "postgresql://", alguns provedores dão "postgres://"
    if _url.startswith("postgres://"):
        _url = _url.replace("postgres://", "postgresql://", 1)
    IS_POSTGRES = True
else:
    _url = f"sqlite:///{_local_sqlite_path()}"
    IS_POSTGRES = False

engine = create_engine(_url, pool_pre_ping=True, pool_recycle=300)


def run(sql: str, params: dict | None = None):
    """Executa um INSERT/UPDATE/DELETE dentro de uma transação."""
    with engine.begin() as conn:
        conn.execute(text(sql), params or {})


def run_many(sql: str, lista_params: list[dict]):
    """Executa o mesmo comando várias vezes (ex: inserir itens de uma nota)."""
    if not lista_params:
        return
    with engine.begin() as conn:
        conn.execute(text(sql), lista_params)


def fetch_df(sql: str, params: dict | None = None) -> pd.DataFrame:
    """Executa um SELECT e devolve um DataFrame."""
    with engine.connect() as conn:
        return pd.read_sql_query(text(sql), conn, params=params or {})


def init_schema():
    """Cria as tabelas se não existirem. Roda uma vez no import do app."""
    if IS_POSTGRES:
        ddl = """
        CREATE TABLE IF NOT EXISTS fornecedores (
            cnpj TEXT PRIMARY KEY,
            nome TEXT,
            uf   TEXT
        );
        CREATE TABLE IF NOT EXISTS notas_fiscais (
            chave_nfe       TEXT PRIMARY KEY,
            numero_nf       TEXT,
            data_emissao    TEXT,
            cnpj_fornecedor TEXT REFERENCES fornecedores(cnpj),
            valor_total     DOUBLE PRECISION
        );
        CREATE TABLE IF NOT EXISTS itens_nota (
            id             SERIAL PRIMARY KEY,
            chave_nfe      TEXT REFERENCES notas_fiscais(chave_nfe),
            codigo_prod    TEXT,
            descricao      TEXT,
            ean            TEXT,
            ncm            TEXT,
            quantidade     DOUBLE PRECISION,
            valor_unitario DOUBLE PRECISION,
            valor_total    DOUBLE PRECISION
        );
        CREATE TABLE IF NOT EXISTS clientes (
            id            SERIAL PRIMARY KEY,
            cnpj_cpf      TEXT UNIQUE,
            razao_social  TEXT,
            telefone      TEXT,
            endereco      TEXT,
            cidade_uf     TEXT,
            cep           TEXT,
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_itens_descricao ON itens_nota(descricao);
        CREATE INDEX IF NOT EXISTS idx_itens_chave     ON itens_nota(chave_nfe);
        CREATE INDEX IF NOT EXISTS idx_nf_data         ON notas_fiscais(data_emissao);
        """
    else:
        ddl = """
        CREATE TABLE IF NOT EXISTS fornecedores (
            cnpj TEXT PRIMARY KEY,
            nome TEXT,
            uf   TEXT
        );
        CREATE TABLE IF NOT EXISTS notas_fiscais (
            chave_nfe       TEXT PRIMARY KEY,
            numero_nf       TEXT,
            data_emissao    TEXT,
            cnpj_fornecedor TEXT,
            valor_total     REAL
        );
        CREATE TABLE IF NOT EXISTS itens_nota (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            chave_nfe      TEXT,
            codigo_prod    TEXT,
            descricao      TEXT,
            ean            TEXT,
            ncm            TEXT,
            quantidade     REAL,
            valor_unitario REAL,
            valor_total    REAL
        );
        CREATE TABLE IF NOT EXISTS clientes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            cnpj_cpf      TEXT UNIQUE,
            razao_social  TEXT,
            telefone      TEXT,
            endereco      TEXT,
            cidade_uf     TEXT,
            cep           TEXT,
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_itens_descricao ON itens_nota(descricao);
        CREATE INDEX IF NOT EXISTS idx_itens_chave     ON itens_nota(chave_nfe);
        CREATE INDEX IF NOT EXISTS idx_nf_data         ON notas_fiscais(data_emissao);
        """

    with engine.begin() as conn:
        for comando in ddl.strip().split(";"):
            comando = comando.strip()
            if comando:
                conn.execute(text(comando))
