"""
VirtuAr - Gestão de Compras e Orçamentos
Usa db.py como camada única de banco: Postgres (persistente) quando
configurado via Secrets/variável DATABASE_URL, ou SQLite local como
fallback automático para rodar no seu PC sem configurar nada.
"""

import hashlib
import html
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st

try:
    import requests
except Exception:
    requests = None

import db

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO (precisa ser o primeiro comando Streamlit do arquivo)
# ---------------------------------------------------------------------------
st.set_page_config(page_title="VirtuAr - Gestão de Compras e Orçamentos", layout="wide")

BASE_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# IMPORTS OPCIONAIS — nunca derrubam o app
# ---------------------------------------------------------------------------
ERRO_FPDF = None
try:
    from fpdf import FPDF
except Exception as e:
    FPDF = None
    ERRO_FPDF = str(e)

ERRO_BACKEND = None
try:
    from criar_banco import (
        PASTA_XMLS,
        processar_xml,
        salvar_no_banco,
        importar_todos_xmls,
        salvar_xml_upload,
    )
    BACKEND_OK = True
except Exception as e:
    BACKEND_OK = False
    ERRO_BACKEND = str(e)
    PASTA_XMLS = BASE_DIR / "xmls"
    processar_xml = salvar_no_banco = importar_todos_xmls = salvar_xml_upload = None


# ---------------------------------------------------------------------------
# INICIALIZAÇÃO SEGURA DO BANCO
# ---------------------------------------------------------------------------
try:
    db.init_schema()
    BANCO_OK = True
    ERRO_BANCO = None
except Exception as e:
    BANCO_OK = False
    ERRO_BANCO = str(e)


@st.cache_data(ttl=300, show_spinner=False)
def consultar(query: str, params: dict | None = None) -> pd.DataFrame:
    return db.fetch_df(query, params or {})


def limpar_cache():
    st.cache_data.clear()


# ---------------------------------------------------------------------------
# UTILITÁRIOS
# ---------------------------------------------------------------------------
def limpar_nome_peca(nome):
    if isinstance(nome, str):
        nome = html.unescape(nome)
        nome = nome.replace("&#168;", '"').replace("¨", '"').replace("&amp;", "&")
        nome = re.sub(r"\s+", " ", nome).strip()
    return nome


def moeda(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def texto_pdf(txt) -> str:
    """FPDF clássico só aceita Latin-1; normaliza para não quebrar a geração."""
    if txt is None:
        return ""
    txt = str(txt)
    substituicoes = {"—": "-", "–": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "…": "..."}
    for k, v in substituicoes.items():
        txt = txt.replace(k, v)
    txt = unicodedata.normalize("NFKD", txt)
    return txt.encode("latin-1", "ignore").decode("latin-1")


def obter_frete_por_peso(peso, tabela):
    for peso_maximo, valor in tabela:
        if peso <= peso_maximo:
            return valor
    return tabela[-1][1]


FRETE_NORMAL = [
    (0.3, 6.55), (0.5, 6.65), (1.0, 6.75), (1.5, 6.85),
    (2.0, 6.95), (3.0, 7.95), (4.0, 8.15), (5.0, 8.35),
    (6.0, 8.55), (7.0, 8.75), (8.0, 8.95), (9.0, 9.15),
    (11.0, 9.55), (13.0, 9.95), (15.0, 10.15), (17.0, 10.35),
    (20.0, 10.55), (25.0, 10.95), (30.0, 11.15), (40.0, 11.35),
    (50.0, 11.55), (60.0, 11.75), (70.0, 11.95), (80.0, 12.15),
    (90.0, 12.35), (100.0, 12.55), (float("inf"), 12.75),
]

SUPER_FRETE = [
    (0.3, 12.35), (0.5, 13.25), (1.0, 13.85), (1.5, 14.15),
    (2.0, 14.45), (3.0, 15.75), (4.0, 17.05), (5.0, 18.45),
    (6.0, 25.45), (7.0, 27.05), (8.0, 28.85), (9.0, 29.65),
    (11.0, 41.25), (13.0, 42.15), (15.0, 45.05), (17.0, 48.55),
    (20.0, 54.75), (25.0, 64.05), (30.0, 65.95), (40.0, 67.75),
    (50.0, 70.25), (60.0, 74.95), (70.0, 80.25), (80.0, 83.95),
    (90.0, 93.25), (100.0, 106.55), (125.0, 119.25), (150.0, 126.55),
    (float("inf"), 166.15),
]


def hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode("utf-8")).hexdigest()


def contar_usuarios() -> int:
    try:
        df = db.fetch_df("SELECT COUNT(*) AS qtd FROM usuarios")
        return int(df.loc[0, "qtd"])
    except Exception:
        return 0


def autenticar(usuario: str, senha: str):
    try:
        df = db.fetch_df(
            "SELECT usuario, nome_exibicao FROM usuarios WHERE usuario = :u AND senha_hash = :h",
            {"u": usuario.strip(), "h": hash_senha(senha)},
        )
        if not df.empty:
            return df.iloc[0]["nome_exibicao"] or df.iloc[0]["usuario"]
    except Exception:
        pass
    return None


def buscar_cnpj(cnpj: str):
    """Consulta dados públicos de um CNPJ via BrasilAPI. Retorna dict ou None."""
    if requests is None:
        return None
    numeros = re.sub(r"\D", "", cnpj or "")
    if len(numeros) != 14:
        return None
    try:
        resp = requests.get(f"https://brasilapi.com.br/api/cnpj/v1/{numeros}", timeout=8)
        if resp.status_code != 200:
            return None
        dado = resp.json()
        endereco = f"{dado.get('logradouro', '')}, {dado.get('numero', '')} {dado.get('bairro', '')}".strip()
        cidade_uf = f"{dado.get('municipio', '')} - {dado.get('uf', '')}".strip(" -")
        return {
            "nome": dado.get("razao_social") or dado.get("nome_fantasia") or "",
            "endereco": endereco,
            "cidade_uf": cidade_uf,
            "cep": dado.get("cep", "") or "",
            "telefone": dado.get("ddd_telefone_1", "") or "",
        }
    except Exception:
        return None


@st.cache_data(ttl=120, show_spinner=False)
def calcular_estoque() -> pd.DataFrame:
    """Estoque = total comprado (itens_nota) - total vendido (vendas), por produto."""
    comprado = db.fetch_df(
        "SELECT descricao, SUM(quantidade) AS comprado FROM itens_nota GROUP BY descricao"
    )
    vendido = db.fetch_df(
        "SELECT descricao, SUM(quantidade) AS vendido FROM vendas GROUP BY descricao"
    )
    if comprado.empty:
        return pd.DataFrame(columns=["Produto", "Comprado", "Vendido", "Saldo"])
    df = comprado.merge(vendido, on="descricao", how="left")
    df["vendido"] = df["vendido"].fillna(0)
    df["saldo"] = df["comprado"] - df["vendido"]
    df["descricao"] = df["descricao"].apply(limpar_nome_peca)
    df = df.rename(columns={"descricao": "Produto", "comprado": "Comprado",
                             "vendido": "Vendido", "saldo": "Saldo"})
    return df.sort_values("Saldo")


@st.cache_data(ttl=300, show_spinner=False)
def mapa_produtos() -> dict:
    df = consultar("SELECT DISTINCT descricao FROM itens_nota ORDER BY descricao")
    if df.empty:
        return {}
    df["tela"] = df["descricao"].apply(limpar_nome_peca)
    return dict(zip(df["tela"], df["descricao"]))


def ultimo_custo(descricao_db: str) -> float:
    df = consultar(
        """
        SELECT i.valor_unitario
        FROM itens_nota i
        JOIN notas_fiscais n ON i.chave_nfe = n.chave_nfe
        WHERE i.descricao = :descricao
        ORDER BY n.data_emissao DESC
        LIMIT 1
        """,
        {"descricao": descricao_db},
    )
    if df.empty or pd.isna(df.iloc[0, 0]):
        return 0.0
    return float(df.iloc[0, 0])


# ---------------------------------------------------------------------------
# LOGIN (opcional): só é exigido depois que o primeiro usuário for criado
# em "👥 Usuários". Antes disso, o app funciona exatamente como hoje.
# ---------------------------------------------------------------------------
if BANCO_OK and contar_usuarios() > 0 and not st.session_state.get("logado"):
    st.title("🔒 VirtuAr - Login")
    with st.form("login_form"):
        usuario_login = st.text_input("Usuário")
        senha_login = st.text_input("Senha", type="password")
        entrar = st.form_submit_button("Entrar", type="primary")
    if entrar:
        nome = autenticar(usuario_login, senha_login)
        if nome:
            st.session_state["logado"] = True
            st.session_state["usuario_logado"] = nome
            st.rerun()
        else:
            st.error("Usuário ou senha incorretos.")
    st.stop()


# ---------------------------------------------------------------------------
# MENU LATERAL
# ---------------------------------------------------------------------------
logo_path = BASE_DIR / "logo.png"
if logo_path.exists():
    st.sidebar.image(str(logo_path), use_container_width=True)
else:
    st.sidebar.markdown("### VirtuAr Compressores")

st.sidebar.markdown("---")

st.session_state.setdefault("usuario_logado", "VirtuAr (Equipe)")
st.sidebar.success(f"👤 Conectado como: **{st.session_state['usuario_logado']}**")

st.sidebar.caption(
    "🟢 Banco: Postgres (persistente)" if db.IS_POSTGRES
    else "🟡 Banco: SQLite local (some a cada deploy no servidor)"
)
st.sidebar.markdown("---")

menu = st.sidebar.radio(
    "Navegação",
    [
        "📊 Dashboard Inicial",
        "🔍 Consultas e Filtros",
        "📤 Upload de XML",
        "💰 Calculadora de Preços",
        "📄 Cotação / Orçamento",
        "🗂️ Histórico de Cotações",
        "📈 Registrar Venda",
        "📦 Estoque",
        "👥 Usuários",
    ],
)

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Atualizar dados (limpar cache)"):
    limpar_cache()
    st.rerun()
if st.session_state.get("logado") and st.sidebar.button("🚪 Sair"):
    st.session_state["logado"] = False
    st.rerun()

if not BANCO_OK:
    st.error(f"Banco de dados indisponível: {ERRO_BANCO}")
if not BACKEND_OK:
    st.sidebar.warning(
        "Módulo `criar_banco.py` não pôde ser carregado. "
        "As telas de consulta funcionam, mas a importação de XML está desativada."
    )
    st.sidebar.caption(f"Detalhe: {ERRO_BACKEND}")
if FPDF is None:
    st.sidebar.warning("Biblioteca `fpdf2` ausente — geração de PDF desativada.")

# ===========================================================================
# TELA 1: DASHBOARD
# ===========================================================================
if menu == "📊 Dashboard Inicial":
    st.title("Dashboard de Compras")
    st.write("Resumo geral das notas fiscais e custos da empresa.")

    try:
        competencia = datetime.now().strftime("%Y-%m")

        df_mes = consultar(
            "SELECT COUNT(*) AS qtd, COALESCE(SUM(valor_total), 0) AS total "
            "FROM notas_fiscais WHERE substr(data_emissao, 1, 7) = :competencia",
            {"competencia": competencia},
        )
        df_geral = consultar(
            "SELECT COUNT(*) AS qtd, COALESCE(SUM(valor_total), 0) AS total FROM notas_fiscais"
        )

        qtd_notas_mes = int(df_mes.loc[0, "qtd"])
        total_gasto_mes = float(df_mes.loc[0, "total"])
        total_gasto_geral = float(df_geral.loc[0, "total"])

        col1, col2, col3 = st.columns(3)
        col1.metric("Gasto no Mês Atual", moeda(total_gasto_mes))
        col2.metric("Notas Lançadas no Mês", qtd_notas_mes)
        col3.metric("Total Histórico Acumulado", moeda(total_gasto_geral))

        st.divider()
        st.subheader("Últimas 5 Notas Lançadas")
        df_ultimas = consultar(
            """
            SELECT n.data_emissao                        AS Data,
                   COALESCE(f.nome, n.cnpj_fornecedor)    AS Fornecedor,
                   n.numero_nf                            AS NF,
                   n.valor_total                          AS Total
            FROM notas_fiscais n
            LEFT JOIN fornecedores f ON n.cnpj_fornecedor = f.cnpj
            ORDER BY n.data_emissao DESC
            LIMIT 5
            """
        )
        if df_ultimas.empty:
            st.info("Nenhuma nota cadastrada ainda. Importe XMLs na aba 'Upload de XML'.")
        else:
            try:
                df_ultimas["Data"] = pd.to_datetime(df_ultimas["Data"]).dt.strftime("%d/%m/%Y")
            except Exception:
                pass
            st.dataframe(df_ultimas, use_container_width=True, hide_index=True)

        # --- Lucro real (precisa de vendas registradas em "📈 Registrar Venda") ---
        df_vendas_mes = consultar(
            "SELECT COALESCE(SUM(valor_total), 0) AS total FROM vendas "
            "WHERE substr(data_venda, 1, 7) = :competencia",
            {"competencia": competencia},
        )
        total_vendido_mes = float(df_vendas_mes.loc[0, "total"])
        if total_vendido_mes > 0:
            st.divider()
            st.subheader("💵 Lucro do Mês (Vendas registradas)")
            lucro_estimado = total_vendido_mes - total_gasto_mes
            colv1, colv2, colv3 = st.columns(3)
            colv1.metric("Vendido no Mês", moeda(total_vendido_mes))
            colv2.metric("Comprado no Mês", moeda(total_gasto_mes))
            colv3.metric("Lucro Bruto Estimado", moeda(lucro_estimado))
            st.caption(
                "Estimativa simples: total vendido menos total comprado no mês. "
                "Não considera estoque de meses anteriores nem despesas fixas."
            )
        else:
            st.info(
                "💡 Registre suas vendas em '📈 Registrar Venda' para ver o lucro real aqui, "
                "não só o gasto."
            )

    except Exception as e:
        st.error(f"Erro ao carregar o dashboard: {e}")

# ===========================================================================
# TELA 2: CONSULTAS
# ===========================================================================
elif menu == "🔍 Consultas e Filtros":
    st.title("Consulta de Peças e Preços")

    try:
        df_fornecedores = consultar("SELECT DISTINCT nome FROM fornecedores ORDER BY nome")
        lista_fornecedores = ["Todos"] + df_fornecedores["nome"].dropna().tolist()
    except Exception as e:
        st.error(f"Erro ao ler fornecedores: {e}")
        lista_fornecedores = ["Todos"]

    col1, col2, col3 = st.columns(3)
    termo_pesquisa = col1.text_input("🔍 Nome da Peça (ex: PRESSOSTATO)")
    fornecedor_selecionado = col2.selectbox("🏢 Fornecedor", lista_fornecedores)
    meses = {
        "Todos": "", "Janeiro": "01", "Fevereiro": "02", "Março": "03", "Abril": "04",
        "Maio": "05", "Junho": "06", "Julho": "07", "Agosto": "08", "Setembro": "09",
        "Outubro": "10", "Novembro": "11", "Dezembro": "12",
    }
    mes_selecionado = col3.selectbox("📅 Mês da Compra", list(meses.keys()))

    query = """
        SELECT n.data_emissao                        AS "Data",
               COALESCE(f.nome, n.cnpj_fornecedor)    AS "Fornecedor",
               i.descricao                            AS "Produto",
               i.quantidade                           AS "Qtd",
               i.valor_unitario                       AS "Preço Un. (R$)",
               i.valor_total                          AS "Total (R$)"
        FROM itens_nota i
        JOIN notas_fiscais n ON i.chave_nfe = n.chave_nfe
        LEFT JOIN fornecedores f ON n.cnpj_fornecedor = f.cnpj
        WHERE 1=1
    """
    params = {}
    if termo_pesquisa:
        query += " AND UPPER(i.descricao) LIKE :termo"
        params["termo"] = f"%{termo_pesquisa.upper()}%"
    if fornecedor_selecionado != "Todos":
        query += " AND f.nome = :fornecedor"
        params["fornecedor"] = fornecedor_selecionado
    if mes_selecionado != "Todos":
        query += " AND substr(n.data_emissao, 6, 2) = :mes"
        params["mes"] = meses[mes_selecionado]
    query += " ORDER BY n.data_emissao DESC LIMIT 5000"

    try:
        df = consultar(query, params)
    except Exception as e:
        st.error(f"Erro ao consultar o banco de dados: {e}")
        df = pd.DataFrame()

    st.write(f"**Resultados encontrados:** {len(df)}")
    if not df.empty:
        df["Produto"] = df["Produto"].apply(limpar_nome_peca)
        try:
            df["Data"] = pd.to_datetime(df["Data"]).dt.strftime("%d/%m/%Y")
        except Exception:
            pass
        if termo_pesquisa:
            menor = df["Preço Un. (R$)"].min()
            if pd.notna(menor):
                st.success(f"💡 Menor preço encontrado nesta busca: **{moeda(float(menor))}**")
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button(
            "⬇️ Baixar resultado em CSV",
            df.to_csv(index=False).encode("utf-8-sig"),
            file_name="consulta_precos.csv",
            mime="text/csv",
        )
    else:
        st.warning("Nenhum registro encontrado.")

# ===========================================================================
# TELA 3: UPLOAD
# ===========================================================================
elif menu == "📤 Upload de XML":
    st.title("Importar Novas Notas Fiscais")

    if not BACKEND_OK:
        st.error(
            "A importação depende do arquivo `criar_banco.py`, que não foi carregado. "
            "Verifique se ele está no mesmo diretório e sem erros de sintaxe."
        )
        st.code(str(ERRO_BACKEND))
    else:
        if not db.IS_POSTGRES:
            st.warning(
                "⚠️ O banco atual é SQLite local. Em servidor, os dados importados "
                "somem no próximo deploy. Configure o Postgres para persistir de verdade."
            )

        st.write("Arraste os arquivos XML para adicionar compras ao banco de dados.")
        PASTA_XMLS.mkdir(parents=True, exist_ok=True)

        arquivos = st.file_uploader(
            "Solte os arquivos XML aqui", type=["xml"], accept_multiple_files=True
        )

        if arquivos and st.button("Processar e Salvar", type="primary"):
            sucessos = ja_existentes = erros = 0
            barra = st.progress(0.0)

            for idx, arquivo in enumerate(arquivos, start=1):
                try:
                    caminho_xml = salvar_xml_upload(arquivo)
                    dados = processar_xml(caminho_xml)
                    if dados:
                        salvar_no_banco(dados)
                        sucessos += 1
                    else:
                        ja_existentes += 1
                except Exception as e:
                    erros += 1
                    st.error(f"Erro na nota {arquivo.name}: {e}")
                barra.progress(idx / len(arquivos))

            limpar_cache()
            st.success(f"✅ {sucessos} nota(s) processada(s) com sucesso!")
            if ja_existentes:
                st.info(f"📁 {ja_existentes} arquivo(s) já existiam ou foram ignorados.")
            if erros:
                st.warning(f"⚠️ {erros} arquivo(s) apresentaram erro.")

        st.markdown("---")
        st.subheader("📁 Sincronizar Pasta Local de XMLs")
        st.write(f"Pasta monitorada: `{PASTA_XMLS}`")

        if st.button("🔄 Sincronizar Todos os XMLs da Pasta"):
            try:
                sucessos_pasta, erros_pasta = importar_todos_xmls()
                limpar_cache()
                st.success(f"✅ Sincronização concluída! {sucessos_pasta} nota(s) importada(s).")
                if erros_pasta:
                    st.warning(f"⚠️ {erros_pasta} arquivo(s) com erro.")
            except Exception as e:
                st.error(f"Erro ao sincronizar pasta: {e}")

# ===========================================================================
# TELA 4: CALCULADORA
# ===========================================================================
elif menu == "💰 Calculadora de Preços":
    st.title("Calculadora de Preços (Espelho da Planilha)")
    st.write("Markup reverso considerando comissões, impostos e custos de frete por peso.")

    mapa = mapa_produtos()
    lista_produtos = ["Digitar valor manualmente..."] + list(mapa.keys())

    st.subheader("1. Produto e Custo")
    produto_selecionado = st.selectbox("Selecione a peça para puxar o custo:", lista_produtos)

    custo_sugerido = 0.0
    if produto_selecionado != "Digitar valor manualmente...":
        try:
            custo_sugerido = ultimo_custo(mapa[produto_selecionado])
            if custo_sugerido:
                st.info(f"💡 Último custo de compra: **{moeda(custo_sugerido)}**")
        except Exception as e:
            st.warning(f"Não foi possível buscar o custo: {e}")

    col_c1, col_c2 = st.columns(2)
    custo_produto = col_c1.number_input(
        "Custo da Peça (R$)", min_value=0.0, value=float(custo_sugerido), step=1.0
    )
    peso_produto = col_c2.number_input(
        "Peso (kg) — base para a tabela de frete", min_value=0.0, value=0.5, step=0.1
    )

    st.subheader("2. Taxas e Parâmetros (%)")
    col_t1, col_t2, col_t3 = st.columns(3)
    tipo_anuncio = col_t1.selectbox(
        "Tipo de Anúncio", ["PREMIUM", "CLASSICO", "SHOPEE", "LOJA INTEGRADA"]
    )

    parametros = {
        "PREMIUM":        (21.11, None, 12.99),
        "CLASSICO":       (16.11, None, 12.99),
        "SHOPEE":         (23.50, 5.00, 10.99),
        "LOJA INTEGRADA": (21.39, 0.50, 12.99),
    }
    comissao_padrao, base_padrao, flex_padrao = parametros[tipo_anuncio]

    frete_tabela = obter_frete_por_peso(peso_produto, FRETE_NORMAL)
    super_frete = obter_frete_por_peso(peso_produto, SUPER_FRETE)
    frete_sem_gratis = frete_tabela if base_padrao is None else base_padrao
    frete_com_gratis = super_frete if base_padrao is None else base_padrao

    taxa_comissao = col_t1.number_input(
        "Taxa de Comissão (%)", min_value=0.0, value=float(comissao_padrao), step=0.01
    )
    imposto_governo = col_t2.number_input("Imposto Governo (%)", min_value=0.0, value=10.0, step=0.1)
    margem_liquida = col_t3.number_input(
        "Margem Líquida Desejada (%)", min_value=0.0, value=15.0, step=1.0
    )

    st.subheader("3. Custos de Frete (R$)")
    col_f1, col_f2, col_f3 = st.columns(3)
    custo_fixo_sem_frete = col_f1.number_input(
        "Frete normal / custo base (R$)", min_value=0.0, value=float(frete_sem_gratis), step=0.5
    )
    custo_frete_gratis = col_f2.number_input(
        "Super Frete / custo base (R$)", min_value=0.0, value=float(frete_com_gratis), step=0.5
    )
    custo_flex = col_f3.number_input(
        "Custo Flex (R$)", min_value=0.0, value=float(flex_padrao), step=0.5
    )

    if st.button("Calcular Preços Exatos", type="primary"):
        soma_percentuais = (taxa_comissao + imposto_governo + margem_liquida) / 100
        if soma_percentuais >= 1:
            st.error("A soma das porcentagens atinge ou ultrapassa 100%. Revise os valores.")
        elif custo_produto <= 0:
            st.warning("Insira um custo válido.")
        else:
            divisor = 1 - soma_percentuais
            preco_sem_frete = (custo_produto + custo_fixo_sem_frete) / divisor
            preco_com_frete = (custo_produto + custo_frete_gratis) / divisor
            preco_flex = (custo_produto + custo_flex) / divisor

            st.markdown("---")
            st.markdown("### 🎯 Preços de Venda Sugeridos")
            c1, c2, c3 = st.columns(3)
            c1.success(f"**SEM FRETE GRÁTIS**\n## {moeda(preco_sem_frete)}")
            c2.info(f"**COM FRETE GRÁTIS**\n## {moeda(preco_com_frete)}")
            c3.warning(f"**VENDEDOR FLEX**\n## {moeda(preco_flex)}")

# ===========================================================================
# TELA 5: COTAÇÃO / ORÇAMENTO
# ===========================================================================
elif menu == "📄 Cotação / Orçamento":
    st.title("Emissão de Cotação e Orçamento Profissional")

    st.session_state.setdefault("itens_orcamento", [])
    st.session_state.setdefault("pdf_gerado", None)
    st.session_state.setdefault("contador_item", 0)

    # ---------------- 1. CLIENTE ----------------
    st.subheader("1. Seleção de Cliente Cadastrado ou Novo")
    try:
        df_cli = consultar(
            "SELECT id, cnpj_cpf, razao_social, telefone, endereco, cidade_uf, cep "
            "FROM clientes ORDER BY razao_social"
        )
    except Exception:
        df_cli = pd.DataFrame(
            columns=["id", "cnpj_cpf", "razao_social", "telefone", "endereco", "cidade_uf", "cep"]
        )

    NOVO = "+ Cadastrar / Usar Novo Cliente"
    lista_nomes = [NOVO] + (df_cli["razao_social"].dropna().tolist() if not df_cli.empty else [])
    cliente_escolhido = st.selectbox("🏢 Buscar Cliente no Banco", lista_nomes)

    v = {
        "nome": "Cliente Balcão", "cnpj": "", "tel": "",
        "end": "", "cid": "Contagem - MG", "cep": "",
    }
    if cliente_escolhido != NOVO and not df_cli.empty:
        linha = df_cli[df_cli["razao_social"] == cliente_escolhido].iloc[0]
        campos = {"nome": "razao_social", "cnpj": "cnpj_cpf", "tel": "telefone",
                  "end": "endereco", "cid": "cidade_uf", "cep": "cep"}
        for chave, coluna in campos.items():
            v[chave] = linha[coluna] if pd.notna(linha[coluna]) else ""

    st.subheader("2. Dados da Cotação e Ordem de Compra")
    col_n1, col_n2 = st.columns(2)
    num_cotacao = col_n1.text_input(
        "🔢 Número da Cotação / Orçamento", f"COT-{datetime.now().strftime('%Y%m%d')}-01"
    )
    num_oc = col_n2.text_input("📋 N° da Ordem de Compra (Cliente — opcional)", "")

    st.subheader("3. Dados do Cliente e Logística")

    # Os campos usam chaves fixas para que a busca por CNPJ possa preenchê-los
    # automaticamente. Ao trocar de cliente selecionado acima, reinicializa os valores.
    if st.session_state.get("_ultimo_cliente_sel") != cliente_escolhido:
        st.session_state["nome_cliente_input"] = v["nome"]
        st.session_state["cnpj_cliente_input"] = v["cnpj"]
        st.session_state["tel_cliente_input"] = v["tel"]
        st.session_state["end_cliente_input"] = v["end"]
        st.session_state["cid_cliente_input"] = v["cid"]
        st.session_state["cep_cliente_input"] = v["cep"]
        st.session_state["_ultimo_cliente_sel"] = cliente_escolhido

    col_cnpj, col_btn = st.columns([4, 1])
    col_cnpj.text_input("📄 CPF / CNPJ", key="cnpj_cliente_input")
    with col_btn:
        st.write("")
        if st.button("🔎 Buscar CNPJ", use_container_width=True):
            with st.spinner("Consultando dados públicos do CNPJ..."):
                dados_cnpj = buscar_cnpj(st.session_state["cnpj_cliente_input"])
            if dados_cnpj:
                st.session_state["nome_cliente_input"] = dados_cnpj["nome"]
                st.session_state["end_cliente_input"] = dados_cnpj["endereco"]
                st.session_state["cid_cliente_input"] = dados_cnpj["cidade_uf"]
                st.session_state["cep_cliente_input"] = dados_cnpj["cep"]
                if dados_cnpj["telefone"]:
                    st.session_state["tel_cliente_input"] = dados_cnpj["telefone"]
                st.success("Dados preenchidos automaticamente!")
                st.rerun()
            else:
                st.warning("CNPJ não encontrado ou serviço indisponível. Preencha manualmente.")

    c1, c3 = st.columns(2)
    c1.text_input("👤 Nome / Razão Social", key="nome_cliente_input")
    c3.text_input("📞 Telefone / WhatsApp", key="tel_cliente_input")

    e1, e2, e3 = st.columns(3)
    e1.text_input("🏠 Endereço", key="end_cliente_input")
    e2.text_input("🏙️ Cidade / UF", key="cid_cliente_input")
    e3.text_input("📮 CEP", key="cep_cliente_input")

    nome_cliente = st.session_state["nome_cliente_input"]
    cnpj_cliente = st.session_state["cnpj_cliente_input"]
    tel_cliente = st.session_state["tel_cliente_input"]
    end_cliente = st.session_state["end_cliente_input"]
    cid_cliente = st.session_state["cid_cliente_input"]
    cep_cliente = st.session_state["cep_cliente_input"]

    salvar_cliente_novo = st.checkbox(
        "💾 Salvar ou atualizar este cliente na base de dados", value=True
    )

    opcoes_pagamento = [
        "À vista (Dinheiro/PIX)", "À vista (Cartão de Débito)", "À vista (Cartão de Crédito)",
        "Boleto Bancário", "30 Dias", "Parcelado (3x)", "7 Dias", "21/35 Dias",
        "28/56 Dias", "30/60/90 Dias", "30/60/90/120 Dias",
    ]

    l1, l2, l3, l4 = st.columns(4)
    transportadora = l1.text_input("🚚 Transportadora", "Correios / Retirada")
    peso_total_orc = l2.text_input("⚖️ Peso Total", "1 kg")
    cond_pagamento = l3.selectbox("💳 Cond. Pagamento", opcoes_pagamento)
    vendedor = l4.text_input("👔 Vendedor Responsável", "VirtuAr Compressores")

    o1, o2 = st.columns(2)
    prazo_entrega = o1.text_input("⏳ Prazo de Entrega", "Imediato / 2 dias úteis")
    validade_proposta = o2.text_input("📅 Validade da Proposta", "7 Dias")
    observacoes = st.text_area(
        "📝 Observações da Cotação",
        "Garantia de 3 meses contra defeitos de fabricação.\n"
        "Entrega mediante confirmação de pagamento.",
    )

    # ---------------- 4. PRODUTOS ----------------
    st.divider()
    st.subheader("4. Adicionar Produtos ao Orçamento")

    mapa = mapa_produtos()
    lista_prods = list(mapa.keys())

    aba_hist, aba_livre = st.tabs(["Do histórico de compras", "Item avulso"])

    with aba_hist:
        if lista_prods:
            i1, i2, i3 = st.columns([3, 1, 1])
            prod_escolhido = i1.selectbox("Selecione a Peça", lista_prods, key="sel_prod_orc")
            custo_bd = ultimo_custo(mapa[prod_escolhido]) if prod_escolhido else 0.0

            # O campo de preço guarda o último valor digitado (via "key").
            # Sem isto, ele não percebe que a peça mudou e continua mostrando
            # o preço da peça anterior. Aqui detectamos a troca e forçamos
            # o campo a assumir o novo custo sugerido.
            if st.session_state.get("_ultimo_prod_hist") != prod_escolhido:
                st.session_state["preco_hist"] = float(custo_bd)
                st.session_state["_ultimo_prod_hist"] = prod_escolhido

            qtd_item = i2.number_input("Quantidade", min_value=1, value=1, key="qtd_hist")
            preco_item = i3.number_input(
                "Preço Unit. (R$)", min_value=0.0, step=1.0, key="preco_hist"
            )
            if st.button("➕ Adicionar Item na Cotação", key="add_hist"):
                st.session_state["contador_item"] += 1
                st.session_state["itens_orcamento"].append({
                    "uid": st.session_state["contador_item"],
                    "produto": prod_escolhido,
                    "quantidade": int(qtd_item),
                    "preco_unitario": float(preco_item),
                    "total": qtd_item * preco_item,
                })
                st.rerun()
        else:
            st.info("Nenhum produto no histórico ainda. Use a aba 'Item avulso'.")

    with aba_livre:
        a1, a2, a3 = st.columns([3, 1, 1])
        desc_livre = a1.text_input("Descrição do item", key="desc_livre")
        qtd_livre = a2.number_input("Quantidade", min_value=1, value=1, key="qtd_livre")
        preco_livre = a3.number_input("Preço Unit. (R$)", min_value=0.0, value=0.0, step=1.0,
                                      key="preco_livre")
        if st.button("➕ Adicionar Item Avulso", key="add_livre"):
            if desc_livre.strip():
                st.session_state["contador_item"] += 1
                st.session_state["itens_orcamento"].append({
                    "uid": st.session_state["contador_item"],
                    "produto": desc_livre.strip(),
                    "quantidade": int(qtd_livre),
                    "preco_unitario": float(preco_livre),
                    "total": qtd_livre * preco_livre,
                })
                st.rerun()
            else:
                st.warning("Informe a descrição do item.")

    # ---------------- CARRINHO ----------------
    if st.session_state["itens_orcamento"]:
        st.write("#### 🛒 Itens Selecionados na Cotação")
        st.caption("Altere quantidade e preço direto nos campos — o total se atualiza sozinho.")

        h1, h2, h3, h4, h5 = st.columns([4, 1.5, 1.5, 1.5, 0.5])
        h1.write("**Produto**")
        h2.write("**Qtd**")
        h3.write("**Preço Unit.**")
        h4.write("**Total**")

        remover = None
        for item in st.session_state["itens_orcamento"]:
            uid = item["uid"]
            c1, c2, c3, c4, c5 = st.columns([4, 1.5, 1.5, 1.5, 0.5])
            c1.markdown(
                f"<div style='padding-top:10px;font-size:14px'>{html.escape(str(item['produto']))}</div>",
                unsafe_allow_html=True,
            )
            nova_qtd = c2.number_input(
                "Qtd", min_value=1, value=int(item["quantidade"]),
                key=f"q_{uid}", label_visibility="collapsed",
            )
            novo_preco = c3.number_input(
                "Preço", min_value=0.0, value=float(item["preco_unitario"]), step=1.0,
                key=f"p_{uid}", label_visibility="collapsed",
            )
            subtotal = nova_qtd * novo_preco
            c4.markdown(
                f"<div style='padding-top:10px;font-weight:bold'>{moeda(subtotal)}</div>",
                unsafe_allow_html=True,
            )
            if c5.button("🗑️", key=f"del_{uid}", help="Remover item"):
                remover = uid

            item["quantidade"] = nova_qtd
            item["preco_unitario"] = novo_preco
            item["total"] = subtotal

        if remover is not None:
            st.session_state["itens_orcamento"] = [
                i for i in st.session_state["itens_orcamento"] if i["uid"] != remover
            ]
            st.rerun()

        st.markdown("---")
        valor_frete = st.number_input(
            "📦 Valor Total do Frete (R$)", min_value=0.0, value=0.0, step=5.0
        )

        total_produtos = sum(i["total"] for i in st.session_state["itens_orcamento"])
        total_geral = total_produtos + valor_frete

        st.markdown(f"#### 📦 Valor dos Produtos: {moeda(total_produtos)}")
        st.markdown(f"### 💰 **VALOR TOTAL DA COTAÇÃO: {moeda(total_geral)}**")

        b1, b2 = st.columns(2)
        if b1.button("🧹 Limpar Todos os Itens"):
            st.session_state["itens_orcamento"] = []
            st.session_state["pdf_gerado"] = None
            st.rerun()

        gerar = b2.button("🖨️ Gerar PDF Oficial (Padrão VirtuAr)", type="primary",
                          disabled=FPDF is None)
        if FPDF is None:
            st.caption("Instale `fpdf2` no requirements.txt para habilitar o PDF.")

        if gerar:
            # --- salva/atualiza cliente ---
            if salvar_cliente_novo and nome_cliente and nome_cliente != "Cliente Balcão" and cnpj_cliente:
                try:
                    db.run(
                        """
                        INSERT INTO clientes (cnpj_cpf, razao_social, telefone, endereco, cidade_uf, cep)
                        VALUES (:cnpj_cpf, :razao_social, :telefone, :endereco, :cidade_uf, :cep)
                        ON CONFLICT (cnpj_cpf) DO UPDATE SET
                            razao_social = excluded.razao_social,
                            telefone     = excluded.telefone,
                            endereco     = excluded.endereco,
                            cidade_uf    = excluded.cidade_uf,
                            cep          = excluded.cep
                        """,
                        {
                            "cnpj_cpf": cnpj_cliente,
                            "razao_social": nome_cliente,
                            "telefone": tel_cliente,
                            "endereco": end_cliente,
                            "cidade_uf": cid_cliente,
                            "cep": cep_cliente,
                        },
                    )
                    limpar_cache()
                except Exception as e:
                    st.warning(f"Não foi possível salvar o cliente: {e}")

            # --- gera o PDF ---
            try:
                pdf = FPDF()
                pdf.set_auto_page_break(auto=True, margin=15)
                pdf.add_page()

                if logo_path.exists():
                    pdf.image(str(logo_path), x=10, y=3, w=40)

                pdf.set_xy(90, 7)
                pdf.set_font("Arial", "B", 14)
                pdf.set_text_color(20, 50, 120)
                pdf.cell(110, 6, texto_pdf("VIRTUAR COMPRESSORES"), 0, 1, "R")

                pdf.set_x(90)
                pdf.set_font("Arial", "", 8)
                pdf.set_text_color(80, 80, 80)
                pdf.cell(110, 4, texto_pdf(
                    "Rua Manoel Teixeira de Camargos, n 90 - Loja 1 - Bairro Gloria - Contagem/MG"
                ), 0, 1, "R")
                pdf.set_x(90)
                pdf.cell(110, 4, texto_pdf("Tel: (31) 2888-0533 / (31) 98288-1653"), 0, 1, "R")
                pdf.set_x(90)
                pdf.set_font("Arial", "B", 8)
                pdf.set_text_color(20, 90, 180)
                pdf.cell(110, 4, texto_pdf("www.VirtuArCompressores.com.br"), 0, 1, "R")

                pdf.set_xy(50, 27)
                pdf.set_font("Arial", "B", 12)
                pdf.set_text_color(0, 0, 0)
                texto_oc = f" | ORDEM DE COMPRA (OC): {num_oc}" if num_oc else ""
                pdf.cell(150, 7, texto_pdf(
                    f"COTACAO DE VENDA / ORCAMENTO: {num_cotacao}{texto_oc}"
                ), 0, 1, "C")

                pdf.set_draw_color(180, 180, 180)
                pdf.set_line_width(0.3)
                pdf.line(10, pdf.get_y(), 200, pdf.get_y())
                pdf.ln(2.5)

                linhas = [
                    (f"Cliente: {nome_cliente[:65]}", f"Data do Documento: {datetime.now():%d/%m/%Y}", "B"),
                    (f"CPF/CNPJ: {cnpj_cliente}", f"Validade da Proposta: {validade_proposta}", ""),
                    (f"Endereco: {end_cliente[:65]}", f"Prazo de Entrega: {prazo_entrega}", ""),
                    (f"Cidade/UF: {cid_cliente} - CEP: {cep_cliente}",
                     f"Transportadora: {transportadora} (Peso: {peso_total_orc})", ""),
                    (f"Telefone: {tel_cliente}", f"Cond. Pagamento: {cond_pagamento}", ""),
                ]
                for esquerda, direita, estilo in linhas:
                    pdf.set_font("Arial", estilo, 8.5)
                    pdf.cell(115, 4.5, texto_pdf(esquerda), 0, 0)
                    pdf.cell(75, 4.5, texto_pdf(direita), 0, 1)

                pdf.set_font("Arial", "", 8.5)
                pdf.cell(115, 4.5, texto_pdf(f"Vendedor: {vendedor}"), 0, 1)

                pdf.ln(2.5)
                pdf.line(10, pdf.get_y(), 200, pdf.get_y())
                pdf.ln(2.5)

                pdf.set_fill_color(23, 100, 175)
                pdf.set_text_color(255, 255, 255)
                pdf.set_font("Arial", "B", 8)
                pdf.cell(110, 6, texto_pdf("  Descricao do Item"), 1, 0, "L", True)
                pdf.cell(15, 6, "Qtd", 1, 0, "C", True)
                pdf.cell(30, 6, texto_pdf("Preco Unit."), 1, 0, "R", True)
                pdf.cell(35, 6, "Total", 1, 1, "R", True)

                pdf.set_font("Arial", "", 8)
                pdf.set_text_color(0, 0, 0)
                pdf.set_fill_color(255, 255, 255)

                for item in st.session_state["itens_orcamento"]:
                    pdf.cell(110, 6, texto_pdf(f"  {str(item['produto'])[:60]}"), 1, 0, "L", True)
                    pdf.cell(15, 6, str(item["quantidade"]), 1, 0, "C", True)
                    pdf.cell(30, 6, texto_pdf(f"R$ {item['preco_unitario']:.2f}"), 1, 0, "R", True)
                    pdf.cell(35, 6, texto_pdf(f"R$ {item['total']:.2f}"), 1, 1, "R", True)

                if valor_frete > 0:
                    pdf.set_font("Arial", "B", 8)
                    pdf.cell(125, 6, texto_pdf("  FRETE / TRANSPORTE"), 1, 0, "L", True)
                    pdf.cell(30, 6, "-", 1, 0, "C", True)
                    pdf.cell(35, 6, texto_pdf(f"R$ {valor_frete:.2f}"), 1, 1, "R", True)

                pdf.ln(5)
                y_totais = pdf.get_y()

                pdf.set_xy(10, y_totais)
                pdf.set_font("Arial", "B", 8.5)
                pdf.cell(90, 5, texto_pdf("OBSERVACOES:"), 0, 1, "L")
                pdf.set_font("Arial", "", 8)
                pdf.multi_cell(90, 4, texto_pdf(observacoes))

                pdf.set_xy(110, y_totais)
                pdf.set_font("Arial", "B", 9)
                pdf.cell(45, 6, texto_pdf("TOTAL EM PRODUTOS:"), 0, 0, "R")
                pdf.cell(35, 6, texto_pdf(f"R$ {total_produtos:,.2f}"), 0, 1, "R")

                if valor_frete > 0:
                    pdf.set_x(110)
                    pdf.cell(45, 6, texto_pdf("VALOR DO FRETE:"), 0, 0, "R")
                    pdf.cell(35, 6, texto_pdf(f"R$ {valor_frete:,.2f}"), 0, 1, "R")

                pdf.set_x(110)
                pdf.cell(45, 6, texto_pdf("VALOR TOTAL GERAL:"), 0, 0, "R")
                pdf.cell(35, 6, texto_pdf(f"R$ {total_geral:,.2f}"), 0, 1, "R")

                saida = pdf.output(dest="S")
                if isinstance(saida, str):
                    saida = saida.encode("latin-1")
                st.session_state["pdf_gerado"] = (bytes(saida), f"Orcamento_{num_cotacao}.pdf")

                # --- Salva a cotação no histórico (tela "🗂️ Histórico de Cotações") ---
                try:
                    itens_para_salvar = [
                        {k: v for k, v in item.items() if k != "uid"}
                        for item in st.session_state["itens_orcamento"]
                    ]
                    db.run(
                        """
                        INSERT INTO cotacoes
                            (numero_cotacao, data_cotacao, cliente, cnpj_cliente,
                             itens_json, valor_frete, valor_total, status)
                        VALUES
                            (:numero_cotacao, :data_cotacao, :cliente, :cnpj_cliente,
                             :itens_json, :valor_frete, :valor_total, 'Enviada')
                        """,
                        {
                            "numero_cotacao": num_cotacao,
                            "data_cotacao": datetime.now().strftime("%Y-%m-%d"),
                            "cliente": nome_cliente,
                            "cnpj_cliente": cnpj_cliente,
                            "itens_json": json.dumps(itens_para_salvar, ensure_ascii=False),
                            "valor_frete": valor_frete,
                            "valor_total": total_geral,
                        },
                    )
                    limpar_cache()
                except Exception as e:
                    st.warning(f"PDF gerado, mas não foi possível salvar no histórico: {e}")

            except Exception as e:
                st.session_state["pdf_gerado"] = None
                st.error(f"Erro ao gerar o PDF: {e}")

        if st.session_state["pdf_gerado"]:
            bytes_pdf, nome_arquivo = st.session_state["pdf_gerado"]
            st.success("PDF pronto! (também salvo em '🗂️ Histórico de Cotações')")

            colb1, colb2, colb3 = st.columns(3)
            colb1.download_button(
                "📥 Baixar PDF",
                data=bytes_pdf,
                file_name=nome_arquivo,
                mime="application/pdf",
                type="primary",
            )

            texto_whats = (
                f"Olá {nome_cliente}! Segue sua cotação {num_cotacao} da VirtuAr Compressores.\n"
                f"Valor total: R$ {total_geral:,.2f}\n"
                f"Validade: {validade_proposta}\n"
                f"(O PDF está anexo separadamente)"
            )
            link_whats = f"https://wa.me/?text={quote(texto_whats)}"
            colb2.link_button("📲 Enviar por WhatsApp", link_whats)
            colb2.caption("Abre o WhatsApp com a mensagem pronta. Anexe o PDF baixado manualmente.")

            assunto_email = quote(f"Cotação {num_cotacao} - VirtuAr Compressores")
            corpo_email = quote(
                f"Olá {nome_cliente},\n\nSegue sua cotação {num_cotacao}.\n"
                f"Valor total: R$ {total_geral:,.2f}\nValidade: {validade_proposta}\n\n"
                f"Atenciosamente,\n{vendedor}"
            )
            colb3.link_button(
                "✉️ Enviar por E-mail",
                f"mailto:?subject={assunto_email}&body={corpo_email}",
            )
            colb3.caption("Abre seu programa de e-mail. Anexe o PDF baixado manualmente.")
    else:
        st.info("Adicione ao menos um item para gerar a cotação.")

# ===========================================================================
# TELA 6: HISTÓRICO DE COTAÇÕES
# ===========================================================================
elif menu == "🗂️ Histórico de Cotações":
    st.title("Histórico de Cotações")
    st.write("Todas as cotações geradas ficam salvas aqui, mesmo depois de baixar o PDF.")

    try:
        df_cot = consultar(
            "SELECT id, numero_cotacao, data_cotacao, cliente, valor_total, status "
            "FROM cotacoes ORDER BY id DESC LIMIT 200"
        )
    except Exception as e:
        st.error(f"Erro ao carregar histórico: {e}")
        df_cot = pd.DataFrame()

    if df_cot.empty:
        st.info("Nenhuma cotação salva ainda. Gere uma em '📄 Cotação / Orçamento'.")
    else:
        total_cot = len(df_cot)
        convertidas = int((df_cot["status"] == "Convertida em Venda").sum())
        col1, col2, col3 = st.columns(3)
        col1.metric("Cotações Geradas", total_cot)
        col2.metric("Convertidas em Venda", convertidas)
        col3.metric(
            "Taxa de Conversão",
            f"{(convertidas / total_cot * 100):.0f}%" if total_cot else "0%",
        )

        st.divider()
        for _, linha in df_cot.iterrows():
            with st.expander(
                f"{linha['numero_cotacao']} — {linha['cliente']} — "
                f"{moeda(float(linha['valor_total'] or 0))} — [{linha['status']}]"
            ):
                st.write(f"**Data:** {linha['data_cotacao']}")
                if linha["status"] != "Convertida em Venda":
                    if st.button(
                        "✅ Marcar como Convertida em Venda (registra a venda e baixa estoque)",
                        key=f"conv_{linha['id']}",
                    ):
                        try:
                            df_full = consultar(
                                "SELECT itens_json, cliente FROM cotacoes WHERE id = :id",
                                {"id": int(linha["id"])},
                            )
                            itens = json.loads(df_full.loc[0, "itens_json"])
                            hoje = datetime.now().strftime("%Y-%m-%d")
                            for item in itens:
                                db.run(
                                    """
                                    INSERT INTO vendas
                                        (data_venda, descricao, quantidade, valor_unitario,
                                         valor_total, cliente, origem_cotacao)
                                    VALUES
                                        (:data_venda, :descricao, :quantidade, :valor_unitario,
                                         :valor_total, :cliente, :origem_cotacao)
                                    """,
                                    {
                                        "data_venda": hoje,
                                        "descricao": item["produto"],
                                        "quantidade": item["quantidade"],
                                        "valor_unitario": item["preco_unitario"],
                                        "valor_total": item["total"],
                                        "cliente": df_full.loc[0, "cliente"],
                                        "origem_cotacao": int(linha["id"]),
                                    },
                                )
                            db.run(
                                "UPDATE cotacoes SET status = 'Convertida em Venda' WHERE id = :id",
                                {"id": int(linha["id"])},
                            )
                            limpar_cache()
                            st.success("Convertida! Estoque e vendas atualizados.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Erro ao converter: {e}")
                else:
                    st.success("Já convertida em venda.")

# ===========================================================================
# TELA 7: REGISTRAR VENDA (manual, sem passar por cotação)
# ===========================================================================
elif menu == "📈 Registrar Venda":
    st.title("Registrar Venda")
    st.write("Use esta tela para vendas feitas fora de uma cotação formal (venda direta no balcão, por exemplo).")

    mapa_v = mapa_produtos()
    lista_prods_v = list(mapa_v.keys())

    if not lista_prods_v:
        st.info("Nenhum produto no histórico ainda. Importe XMLs primeiro.")
    else:
        col1, col2, col3 = st.columns(3)
        produto_v = col1.selectbox("Produto", lista_prods_v, key="prod_venda")
        qtd_v = col2.number_input("Quantidade", min_value=1, value=1, key="qtd_venda")

        if st.session_state.get("_ultimo_prod_venda") != produto_v:
            st.session_state["preco_venda"] = float(ultimo_custo(mapa_v[produto_v]))
            st.session_state["_ultimo_prod_venda"] = produto_v
        preco_v = col3.number_input(
            "Preço Unit. de Venda (R$)", min_value=0.0, step=1.0, key="preco_venda"
        )
        cliente_v = st.text_input("Cliente (opcional)", "")
        data_v = st.date_input("Data da Venda", value=datetime.now())

        if st.button("💾 Registrar Venda", type="primary"):
            try:
                db.run(
                    """
                    INSERT INTO vendas (data_venda, descricao, quantidade, valor_unitario, valor_total, cliente)
                    VALUES (:data_venda, :descricao, :quantidade, :valor_unitario, :valor_total, :cliente)
                    """,
                    {
                        "data_venda": data_v.strftime("%Y-%m-%d"),
                        "descricao": mapa_v[produto_v],
                        "quantidade": qtd_v,
                        "valor_unitario": preco_v,
                        "valor_total": qtd_v * preco_v,
                        "cliente": cliente_v,
                    },
                )
                limpar_cache()
                st.success(f"✅ Venda de {qtd_v}x {produto_v} registrada!")
            except Exception as e:
                st.error(f"Erro ao registrar venda: {e}")

    st.divider()
    st.subheader("Últimas vendas registradas")
    try:
        df_vendas = consultar(
            "SELECT data_venda AS Data, descricao AS Produto, quantidade AS Qtd, "
            "valor_unitario AS \"Preço Unit.\", valor_total AS Total, cliente AS Cliente "
            "FROM vendas ORDER BY id DESC LIMIT 20"
        )
        if not df_vendas.empty:
            df_vendas["Produto"] = df_vendas["Produto"].apply(limpar_nome_peca)
            st.dataframe(df_vendas, use_container_width=True, hide_index=True)
        else:
            st.info("Nenhuma venda registrada ainda.")
    except Exception as e:
        st.error(f"Erro ao carregar vendas: {e}")

# ===========================================================================
# TELA 8: ESTOQUE
# ===========================================================================
elif menu == "📦 Estoque":
    st.title("Estoque")
    st.write("Calculado como: total comprado (notas fiscais) − total vendido (vendas registradas).")
    st.caption(
        "⚠️ Só é preciso se todas as vendas forem registradas em '📈 Registrar Venda' "
        "ou convertidas a partir de uma cotação."
    )

    limite_baixo = st.number_input("Avisar quando o saldo for menor ou igual a:", min_value=0, value=3)

    try:
        df_estoque = calcular_estoque()
    except Exception as e:
        st.error(f"Erro ao calcular estoque: {e}")
        df_estoque = pd.DataFrame()

    if df_estoque.empty:
        st.info("Sem dados suficientes ainda. Importe XMLs e registre vendas.")
    else:
        baixos = df_estoque[df_estoque["Saldo"] <= limite_baixo]
        if not baixos.empty:
            st.warning(f"⚠️ {len(baixos)} produto(s) com estoque baixo (≤ {limite_baixo}):")
            st.dataframe(baixos, use_container_width=True, hide_index=True)
            st.divider()

        st.subheader("Estoque completo")
        st.dataframe(df_estoque, use_container_width=True, hide_index=True)

# ===========================================================================
# TELA 9: USUÁRIOS (login)
# ===========================================================================
elif menu == "👥 Usuários":
    st.title("Usuários do Sistema")

    qtd_usuarios = contar_usuarios()
    if qtd_usuarios == 0:
        st.info(
            "Ainda não existe nenhum usuário cadastrado — o app está aberto para qualquer "
            "pessoa com o link. Crie o primeiro usuário abaixo para ativar a tela de login."
        )
    else:
        st.success(f"🔒 Login ativado — {qtd_usuarios} usuário(s) cadastrado(s).")

    with st.form("novo_usuario_form"):
        st.subheader("Adicionar novo usuário")
        novo_usuario = st.text_input("Usuário (login)")
        novo_nome = st.text_input("Nome de exibição", "")
        nova_senha = st.text_input("Senha", type="password")
        criar = st.form_submit_button("Criar usuário", type="primary")

    if criar:
        if not novo_usuario or not nova_senha:
            st.warning("Preencha usuário e senha.")
        else:
            try:
                db.run(
                    "INSERT INTO usuarios (usuario, senha_hash, nome_exibicao) "
                    "VALUES (:usuario, :senha_hash, :nome_exibicao)",
                    {
                        "usuario": novo_usuario.strip(),
                        "senha_hash": hash_senha(nova_senha),
                        "nome_exibicao": novo_nome or novo_usuario,
                    },
                )
                st.success(f"Usuário '{novo_usuario}' criado!")
                st.rerun()
            except Exception as e:
                st.error(f"Não foi possível criar (usuário já existe?): {e}")

    st.divider()
    st.subheader("Usuários cadastrados")
    try:
        df_users = consultar("SELECT usuario, nome_exibicao FROM usuarios ORDER BY usuario")
        if not df_users.empty:
            st.dataframe(df_users, use_container_width=True, hide_index=True)
        else:
            st.info("Nenhum usuário cadastrado.")
    except Exception as e:
        st.error(f"Erro ao listar usuários: {e}")