"""
VirtuAr - Gestão de Compras e Orçamentos
Usa db.py como camada única de banco: Postgres (persistente) quando
configurado via Secrets/variável DATABASE_URL, ou SQLite local como
fallback automático para rodar no seu PC sem configurar nada.
"""

import html
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

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
    ],
)

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Atualizar dados (limpar cache)"):
    limpar_cache()
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
    c1, c2, c3 = st.columns(3)
    nome_cliente = c1.text_input("👤 Nome / Razão Social", v["nome"])
    cnpj_cliente = c2.text_input("📄 CPF / CNPJ", v["cnpj"])
    tel_cliente = c3.text_input("📞 Telefone / WhatsApp", v["tel"])

    e1, e2, e3 = st.columns(3)
    end_cliente = e1.text_input("🏠 Endereço", v["end"])
    cid_cliente = e2.text_input("🏙️ Cidade / UF", v["cid"])
    cep_cliente = e3.text_input("📮 CEP", v["cep"])

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
            qtd_item = i2.number_input("Quantidade", min_value=1, value=1, key="qtd_hist")
            preco_item = i3.number_input(
                "Preço Unit. (R$)", min_value=0.0, value=float(custo_bd), step=1.0, key="preco_hist"
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

            except Exception as e:
                st.session_state["pdf_gerado"] = None
                st.error(f"Erro ao gerar o PDF: {e}")

        if st.session_state["pdf_gerado"]:
            bytes_pdf, nome_arquivo = st.session_state["pdf_gerado"]
            st.success("PDF pronto!")
            st.download_button(
                "📥 Baixar PDF da Cotação",
                data=bytes_pdf,
                file_name=nome_arquivo,
                mime="application/pdf",
                type="primary",
            )
    else:
        st.info("Adicione ao menos um item para gerar a cotação.")